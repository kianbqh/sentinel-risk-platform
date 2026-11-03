"""Fail-closed policy regressions using the masked finding from actual CI."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "platform_trivy_gate", ROOT / "scripts/trivy_gate.py"
)
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)
FIXTURE = Path(__file__).parent / "fixtures/trivy-pyjwt-doc-example.json"


class TrivyGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / GATE.DOC_EXAMPLE_TARGET).read_bytes()
        cls.temp_parent = ROOT / ".runtime/trivy-gate-tests"
        cls.temp_parent.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=self.temp_parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / GATE.DOC_EXAMPLE_TARGET
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(self.source)
        self.report = {"Results": [json.loads(FIXTURE.read_bytes())]}

    def summary(self):
        return GATE.build_summary(self.report, self.root)

    def assert_blocked(self):
        summary = self.summary()
        self.assertFalse(summary["policy_passed"])
        self.assertTrue(summary["blocking_secrets"])

    def test_actual_ci_example_retained_and_classified_without_mutation(self):
        before = copy.deepcopy(self.report)
        summary = self.summary()
        self.assertTrue(summary["policy_passed"])
        self.assertEqual(len(summary["secrets"]), 1)
        self.assertEqual(summary["blocking_secrets"], [])
        finding = summary["secrets"][0]
        self.assertEqual(finding["disposition"], "reviewed_public_example")
        self.assertIn("PyJWT 2.15.1", finding["reason"])
        self.assertEqual(finding["source_sha256"], GATE.DOC_EXAMPLE_FILE_SHA256)
        self.assertEqual(finding["token_sha256"], GATE.DOC_EXAMPLE_TOKEN_SHA256)
        self.assertEqual(self.report, before)
        self.assertEqual(self.path.read_bytes(), self.source)

    def test_changed_token_is_blocked_even_with_original_scanner_descriptor(self):
        source = bytearray(self.source)
        source[GATE.DOC_EXAMPLE_OFFSET + GATE.DOC_EXAMPLE_LENGTH - 1] ^= 1
        self.path.write_bytes(source)
        self.assert_blocked()

    def test_any_other_source_byte_change_is_blocked(self):
        self.path.write_bytes(self.source + b"\n")
        self.assert_blocked()

    def test_missing_source_is_blocked(self):
        self.path.unlink()
        self.assert_blocked()

    def test_other_paths_and_noncanonical_path_spellings_are_blocked(self):
        target = self.report["Results"][0]
        for path in (
            "other/environment/pip-inspect.json",
            "./" + GATE.DOC_EXAMPLE_TARGET,
            GATE.DOC_EXAMPLE_TARGET.replace("/", "\\"),
            GATE.DOC_EXAMPLE_TARGET.upper(),
        ):
            with self.subTest(path=path):
                target["Target"] = path
                self.assert_blocked()

    def test_all_scanner_descriptor_changes_are_blocked(self):
        target = self.report["Results"][0]
        original = copy.deepcopy(target["Secrets"][0])
        changes = {
            "RuleID": "different-secret-rule", "Category": "other",
            "Severity": "HIGH", "Title": "changed",
            "StartLine": GATE.DOC_EXAMPLE_LINE - 1,
            "EndLine": GATE.DOC_EXAMPLE_LINE + 1,
            "Offset": GATE.DOC_EXAMPLE_OFFSET + 1,
            "Match": original["Match"] + "*", "Code": {"Lines": []},
            "UnexpectedField": "new scanner descriptor requires review",
        }
        for key, value in changes.items():
            with self.subTest(field=key):
                target["Secrets"][0] = copy.deepcopy(original)
                target["Secrets"][0][key] = value
                self.assert_blocked()
        target["Secrets"][0] = original
        target["Class"] = "other"
        self.assert_blocked()

    def test_removed_scanner_fields_are_blocked(self):
        target = self.report["Results"][0]
        original = copy.deepcopy(target["Secrets"][0])
        for key in original:
            with self.subTest(field=key):
                changed = copy.deepcopy(original)
                del changed[key]
                # Required display fields remain schema errors, never a pass.
                if key in ("RuleID", "Severity"):
                    target["Secrets"][0] = changed
                    with self.assertRaises(KeyError):
                        self.summary()
                else:
                    target["Secrets"][0] = changed
                    self.assert_blocked()

    def test_duplicate_reviewed_finding_cannot_consume_a_second_exception(self):
        self.report["Results"][0]["Secrets"] *= 2
        summary = self.summary()
        self.assertFalse(summary["policy_passed"])
        self.assertEqual(len(summary["secrets"]), 2)
        self.assertEqual(len(summary["blocking_secrets"]), 1)

    def test_new_secret_in_same_file_still_blocks_and_both_findings_remain(self):
        new = copy.deepcopy(self.report["Results"][0]["Secrets"][0])
        new.update({"RuleID": "generic-api-key", "Offset": 5})
        self.report["Results"][0]["Secrets"].append(new)
        summary = self.summary()
        self.assertFalse(summary["policy_passed"])
        self.assertEqual(len(summary["secrets"]), 2)
        self.assertEqual(len(summary["blocking_secrets"]), 1)
        self.assertEqual(summary["blocking_secrets"][0]["rule"], "generic-api-key")

    def test_new_secret_in_other_target_still_blocks(self):
        new = copy.deepcopy(self.report["Results"][0])
        new["Target"] = "service/config.py"
        self.report["Results"].append(new)
        self.assert_blocked()

    def test_vulnerability_gate_and_observation_retention_are_unchanged(self):
        for severity, fixed, blocked in (
            ("HIGH", "2.0", True), ("CRITICAL", "2.0", True),
            ("HIGH", "", False), ("CRITICAL", "", False),
            ("MEDIUM", "2.0", False), ("UNKNOWN", "", False),
        ):
            with self.subTest(severity=severity, fixed=fixed):
                self.report["Results"][0]["Vulnerabilities"] = [{
                    "VulnerabilityID": "test-advisory", "PkgName": "test-package",
                    "Severity": severity, "InstalledVersion": "1.0",
                    "FixedVersion": fixed,
                }]
                summary = self.summary()
                self.assertEqual(summary["policy_passed"], not blocked)
                self.assertEqual(len(summary["vulnerabilities"]), 1)
                self.assertEqual(len(summary["fixable_high_critical"]), int(blocked))

    def test_missing_results_is_an_error(self):
        for report in ({}, {"Results": None}, {"Results": {}}):
            with self.subTest(report=report):
                with self.assertRaises(RuntimeError):
                    GATE.build_summary(report, self.root)

    def test_cli_keeps_raw_report_bytes_and_writes_explicit_disposition(self):
        report_dir = self.root / "results/security"
        report_dir.mkdir(parents=True)
        raw_path = report_dir / "trivy.json"
        raw = json.dumps(self.report, indent=4).encode("utf-8") + b"\n"
        raw_path.write_bytes(raw)
        with contextlib.redirect_stdout(io.StringIO()):
            GATE.main(self.root)
        self.assertEqual(raw_path.read_bytes(), raw)
        summary = json.loads((report_dir / "trivy-summary.json").read_bytes())
        self.assertTrue(summary["policy_passed"])
        self.assertEqual(summary["secrets"][0]["disposition"], "reviewed_public_example")
        self.path.write_bytes(self.source + b"\n")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            GATE.main(self.root)
        self.assertEqual(raw_path.read_bytes(), raw)
        self.assertFalse(json.loads(
            (report_dir / "trivy-summary.json").read_bytes()
        )["policy_passed"])


if __name__ == "__main__":
    unittest.main()
