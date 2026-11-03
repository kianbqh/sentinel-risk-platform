"""Pure CPU regression tests: no cluster, service, process or network is launched."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kind_run as candidate


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Journal:
    def __init__(self):
        self.events = []

    def record(self, event, **fields):
        self.events.append({"event": event, **fields})

    def attempts(self):
        return [event for event in self.events if event["event"] == "initial_service_smoke_attempt"]


SERVICES = {"gateway": "http://gateway/readyz", "backend": "http://backend/readyz", "frontend": "http://frontend/"}


def process(statuses=None, exit_code=0, stdout=None, stderr=""):
    statuses = statuses if statuses is not None else {name: 200 for name in SERVICES}
    if stdout is None:
        stdout = json.dumps({"service_status": statuses}) + "\n"
    return subprocess.CompletedProcess(["kubectl", "exec", "startup-probe"], exit_code, stdout, stderr)


class InitialSmokeTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.journal = Journal()

    def wait(self, probe, timeout=1):
        return candidate.wait_for_initial_smoke(probe, self.journal, SERVICES, timeout, .25,
                                                monotonic=self.clock, sleep=self.clock.sleep)

    def test_connection_refused_then_ready_keeps_failed_and_successful_raw_attempts(self):
        raw = json.dumps({"event": "service_probe", "service": "gateway", "url": SERVICES["gateway"],
                          "status": None, "error": "[Errno 111] Connection refused"}) + "\n"
        responses = iter([process(exit_code=1, stdout=raw, stderr="remote terminated 1"), process()])
        result = self.wait(lambda remaining: next(responses))
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["service_status"], {name: 200 for name in SERVICES})
        attempts = self.journal.attempts()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["stdout"], raw)
        self.assertEqual(attempts[0]["stderr"], "remote terminated 1")
        self.assertEqual(attempts[0]["exit_code"], 1)
        self.assertEqual(attempts[1]["exit_code"], 0)
        self.assertEqual(attempts[1]["remaining_budget_seconds"], .75)
        self.assertIn("before load", result["scope"])

    def test_persistent_failure_stops_at_shared_deadline_and_keeps_every_attempt(self):
        budgets = []
        def unavailable(remaining):
            budgets.append(remaining)
            return process(exit_code=1, stdout="refused\n")
        with self.assertRaisesRegex(RuntimeError, "did not converge"):
            self.wait(unavailable)
        self.assertEqual(budgets, [1, .75, .5, .25])
        self.assertEqual(len(self.journal.attempts()), 4)
        self.assertEqual(self.clock.now, 1)

    def test_missing_service_cannot_pass_even_when_process_exits_zero(self):
        with self.assertRaisesRegex(RuntimeError, "every expected Service"):
            self.wait(lambda _: process({"gateway": 200, "backend": 200}))
        self.assertEqual(len(self.journal.attempts()), 1)

    def test_unready_status_cannot_pass_even_when_process_exits_zero(self):
        with self.assertRaisesRegex(RuntimeError, "every expected Service"):
            self.wait(lambda _: process({"gateway": 200, "backend": 503, "frontend": 200}))
        self.assertEqual(len(self.journal.attempts()), 1)

    def test_failed_process_with_forged_success_summary_is_not_accepted(self):
        with self.assertRaisesRegex(RuntimeError, "did not converge"):
            self.wait(lambda _: process(exit_code=1), timeout=.25)
        self.assertEqual(self.journal.attempts()[0]["exit_code"], 1)

    def test_process_deadline_preserves_partial_stdout_and_stderr(self):
        def timeout(remaining):
            self.clock.now += remaining
            raise subprocess.TimeoutExpired(["kubectl", "exec"], remaining,
                                            output=b'{"service":"gateway","status":200}\n', stderr=b"still waiting")
        with self.assertRaisesRegex(RuntimeError, "startup deadline"):
            self.wait(timeout)
        attempts = self.journal.attempts()
        self.assertEqual(len(attempts), 1)
        self.assertTrue(attempts[0]["timed_out"])
        self.assertEqual(attempts[0]["stdout"], '{"service":"gateway","status":200}\n')
        self.assertEqual(attempts[0]["stderr"], "still waiting")

    def test_success_completing_after_budget_is_rejected(self):
        def late(_):
            self.clock.now = 1.01
            return process()
        with self.assertRaisesRegex(RuntimeError, "did not converge"):
            self.wait(late)
        self.assertEqual(len(self.journal.attempts()), 1)

    def test_invalid_summary_is_recorded_then_rejected_without_retries(self):
        with self.assertRaisesRegex(RuntimeError, "no valid summary"):
            self.wait(lambda _: process(stdout="invalid summary"))
        self.assertEqual(len(self.journal.attempts()), 1)

    def test_invalid_budgets_or_no_services_do_not_launch_probe(self):
        for services, timeout, interval in (({}, 1, .5), (SERVICES, 0, .5), (SERVICES, float("inf"), .5),
                                             (SERVICES, float("nan"), .5), (SERVICES, 1, 0)):
            with self.subTest(services=services, timeout=timeout, interval=interval):
                with self.assertRaises(ValueError):
                    candidate.wait_for_initial_smoke(lambda _: self.fail("must not probe"), self.journal,
                                                     services, timeout, interval)


class ProbeSourceTests(unittest.TestCase):
    def remote(self, responses):
        output = io.StringIO()
        with patch.object(sys, "argv", ["initial-probe", json.dumps(SERVICES), "2"]), \
             patch("urllib.request.urlopen", side_effect=responses) as urlopen, \
             contextlib.redirect_stdout(output):
            try:
                exec(candidate.INITIAL_SMOKE_SOURCE, {})
                code = 0
            except SystemExit as error:
                code = error.code
        return code, [json.loads(line) for line in output.getvalue().splitlines()], urlopen

    def test_remote_probe_names_the_exact_refused_service(self):
        code, rows, urlopen = self.remote([urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))])
        self.assertEqual(code, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["service"], "gateway")
        self.assertEqual(rows[0]["url"], SERVICES["gateway"])
        self.assertIsNone(rows[0]["status"])
        self.assertIn("Connection refused", rows[0]["error"])
        self.assertEqual(urlopen.call_count, 1)

    def test_remote_probe_records_all_services_before_success(self):
        response = contextlib.nullcontext(type("Response", (), {"status": 200})())
        code, rows, urlopen = self.remote([response, response, response])
        self.assertEqual(code, 0)
        self.assertEqual([row["service"] for row in rows[:-1]], list(SERVICES))
        self.assertTrue(all(row["error"] is None and row["status"] == 200 for row in rows[:-1]))
        self.assertEqual(rows[-1], {"service_status": {name: 200 for name in SERVICES}})
        self.assertEqual([call.kwargs["timeout"] for call in urlopen.call_args_list], [2, 2, 2])

    def test_remote_backend_503_keeps_gateway_success_and_backend_failure(self):
        response = contextlib.nullcontext(type("Response", (), {"status": 200})())
        error = urllib.error.HTTPError(SERVICES["backend"], 503, "not ready", {}, None)
        code, rows, _ = self.remote([response, error])
        self.assertEqual(code, 1)
        self.assertEqual([(row["service"], row["status"]) for row in rows], [("gateway", 200), ("backend", 503)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
