"""Synthetic and replay-derived IPv4 persistence regressions; no live services."""
import asyncio
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from copilot.agent.llm import canonical
from copilot.agent.redaction import findings, redact
from copilot.agent.workflow import AgentConfig, AnalystCopilot
from copilot.db.persistence import validate_turn


FIXTURE = Path(__file__).parent / "fixtures/case_1000105_persistence.json"


def immediate(coroutine):
    """Execute only recorded/pure-CPU await chains, without an event loop."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("Boundary regression unexpectedly suspended for I/O")


async def inline_cpu_thread(function, /, *args, **kwargs):
    return function(*args, **kwargs)


class CapturedAfterValidation(Exception):
    """Stop at the storage boundary without fabricating a committed receipt."""


class CaptureOnlyStore:
    def __init__(self):
        self.captured = None

    def save_turn(self, case_id, **kwargs):
        self.captured = copy.deepcopy({"case_id": case_id, **kwargs})
        validate_turn(case_id, kwargs["run_id"], kwargs["redacted_memory"], kwargs["draft"])
        raise CapturedAfterValidation("Pure-CPU validation passed; no DB connection or receipt")


class IPv4PersistenceTests(unittest.TestCase):
    def replay_payload(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(data["mode"], "replay-derived-offline-captured-save-turn-no-receipt")
        return data["captured"]

    def test_actual_failed_save_payload_redacts_before_original_validator(self):
        row = self.replay_payload()
        # The original final storage boundary still rejects the actual payload.
        with self.assertRaisesRegex(ValueError, "Unredacted or oversized"):
            validate_turn(row["case_id"], row["run_id"], row["redacted_memory"], row["draft"])
        memory, draft = redact(row["redacted_memory"]), redact(row["draft"])
        self.assertIn("IP [REDACTED_IPV4].", draft["summary"])
        self.assertIn("IP [REDACTED_IPV4].", memory[0]["draft"]["summary"])
        validate_turn(row["case_id"], row["run_id"], memory, draft)
        self.assertFalse(findings(canonical({"memory": memory, "draft": draft})))

    def test_ipv4_sentence_dot_is_redacted_without_consuming_punctuation(self):
        for suffix in (".", ". Next sentence", "..", "... Next", ",", ";", ":", "!", "?", ")", "。", "\n", ""):
            with self.subTest(suffix=suffix):
                text = "IP 192.0.2.44" + suffix
                self.assertEqual(findings(text), [{"kind": "ipv4", "start": 3, "end": 13}])
                self.assertEqual(redact(text), "IP [REDACTED_IPV4]" + suffix)

    def test_valid_ipv4_octet_boundaries(self):
        for ip in ("0.0.0.0", "255.255.255.255", "127.0.0.1", "192.0.2.44"):
            with self.subTest(ip=ip):
                self.assertEqual(redact("(" + ip + ")."), "([REDACTED_IPV4]).")

    def test_invalid_ip_and_adjacent_digits_do_not_become_valid_suffixes(self):
        for text in ("256.0.2.44.", "192.0.2.444.", "1192.0.2.44.", "192.0.2.44.5", "1.192.0.2.44", "192.0.2.44e12", "id192.0.2.44", "192.0.2.44x", "192.0.02.44."):
            with self.subTest(text=text):
                self.assertEqual(redact(text), text)
                self.assertFalse(findings(text))

    def test_decimals_scientific_notation_feature_and_case_ids_preserved(self):
        for text in ("score 0.1234567890123456789.", "V12=-1.234567890123456e-10", "case 1000105", "number 123456789012345e-10", "amount 1234567890.12", "123456789012345.0", "192.0002", "1.234567890123456e+20", "1.234567890123456e20"):
            with self.subTest(text=text):
                self.assertEqual(redact(text), text)
                self.assertFalse(findings(text))

    def test_email_card_phone_ipv6_patterns_still_redacted(self):
        values = {"email": "a@example.invalid", "card": "4111 1111 1111 1111", "phone": "+1 (202) 555-0146", "ipv6": "2001:db8::7"}
        for kind, value in values.items():
            with self.subTest(kind=kind):
                self.assertIn(kind, {hit["kind"] for hit in findings(value)})
                self.assertFalse(findings(redact(value)))
                validate_turn(1000105, "synthetic-regression", [{"content": redact(value)}], {})

    def test_storage_still_rejects_all_original_pii_patterns(self):
        for value in ("a@example.invalid", "4111 1111 1111 1111", "+1 (202) 555-0146", "192.0.2.44", "192.0.2.44."):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Unredacted or oversized"):
                validate_turn(1000105, "synthetic-regression", [{"content": value}], {})

    def test_storage_size_types_ids_and_run_ids_remain_enforced(self):
        for args in ((True, "run", [], {}), (0, "run", [], {}), (2**63, "run", [], {}), (1, "bad key", [], {}), (1, "x" * 121, [], {}), (1, "run", {}, {}), (1, "run", ["text"], {}), (1, "run", [], []), (1, "run", [], {"summary": "x" * 131073}), (1, "run", [], {"score": float("nan")})):
            with self.subTest(args=(args[0], args[1], type(args[2]).__name__, type(args[3]).__name__)), self.assertRaises(ValueError):
                validate_turn(*args)
        validate_turn(1, "valid-id_1:2.3", [], {"summary": "Reviewed"})

    def test_guard_identifies_actual_draft_as_pii_and_requests_human_review(self):
        row = self.replay_payload()
        draft = row["draft"]
        state = {"case_id": row["case_id"], "draft": draft, "observations": [{"name": "get_case", "response": {"ok": True, "data": {"summary": draft["summary"], "fact": draft["key_evidence"][0]["fact"]}}}]}
        result = immediate(AnalystCopilot(None, None, None).guard_step(state))
        self.assertEqual(result["guard_reasons"], ["pii_output"])
        self.assertTrue(result["draft"]["abstain"])
        self.assertEqual(result["draft"]["handoff_reason"], "pii_output")

    def test_prompt_and_capture_only_memory_use_same_redaction(self):
        row = self.replay_payload()
        original = "IP 192.0.2.44."
        store = CaptureOnlyStore()
        agent = AnalystCopilot(None, None, store, AgentConfig())
        state = {"case_id": row["case_id"], "run_id": row["run_id"], "task": redact(original), "persist": True, "memory": [],
                 "draft": redact(row["draft"]), "tool_count": 1, "model_count": 0,
                 "observations": [{"name": "get_case", "arguments": {"case_id": row["case_id"]}, "response": {"ok": True, "data": {"case_id": row["case_id"], "summary": redact(original)}}}]}
        messages = agent._messages(state)
        self.assertNotIn("192.0.2.44", canonical(messages))
        self.assertIn("[REDACTED_IPV4].", canonical(messages))
        with patch.object(asyncio, "to_thread", inline_cpu_thread), self.assertRaises(CapturedAfterValidation):
            immediate(agent.persist_step(state))
        self.assertIsNotNone(store.captured)
        self.assertNotIn("192.0.2.44", canonical(store.captured))
        self.assertEqual(store.captured["redacted_memory"][0]["task"], state["task"])
        self.assertFalse(findings(canonical(store.captured)))


if __name__ == "__main__":
    unittest.main()
