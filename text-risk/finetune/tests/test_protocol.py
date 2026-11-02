"""Small CPU-only checks; no downloads, model loading or fitting."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from finetune.artifacts import (OwnedRun, SELECTED_SOURCE_PATHS, exclusive_json, git_snapshot,
                               json_line, load_config, provenance, source_fingerprint)
from finetune.comparison import import_historical
from finetune.metrics import binary_metrics, exact_mcnemar, paired_bootstrap, percentile, seed_summary
from finetune.modeling import encode_example, parse_binary_json
from finetune.protocol import load_corpus, partition_record, partition_training, training_data

BASE = Path(__file__).resolve().parents[1]
CONFIG = load_config(BASE / "configs/qwen15_nf4.json")
TMP = BASE / "tests/.tmp"
TMP.mkdir(exist_ok=True)


class ProtocolTests(unittest.TestCase):
    def test_training_partition_is_fixed_and_excludes_all_test_ids(self):
        rows = [{"id": str(i), "y": int(i % 5 == 0), "text": str(i)} for i in range(100)]
        heldout = {str(i) for i in range(80, 100)}
        train, validation = partition_training(rows, heldout, 13, 1729)
        second = partition_training(rows, heldout, 13, 1729)
        self.assertEqual((train, validation), second)
        self.assertEqual(len(validation), 13)
        self.assertEqual(sum(r["y"] for r in validation), 3)
        self.assertFalse({r["id"] for r in train + validation} & heldout)
        self.assertFalse({r["id"] for r in train} & {r["id"] for r in validation})

    def test_no_validation_choice_depends_on_training_seed(self):
        rows = [{"id": str(i), "y": i % 2} for i in range(50)]
        first = partition_training(rows, set(), 10, CONFIG["data"]["validation_seed"])
        for seed in CONFIG["training"]["seeds"]:
            self.assertEqual(first, partition_training(rows, set(), 10, CONFIG["data"]["validation_seed"]))

    def test_real_frozen_membership_and_full_paired_order(self):
        directory = os.environ.get("SENTINEL_REPO")
        if not directory:
            self.skipTest("set SENTINEL_REPO for the existing public-repo fixture check")
        config = copy.deepcopy(CONFIG)
        config["data"]["manifest"] = str(BASE / "protocol/sms_split.json")
        rows, manifest = load_corpus(Path(directory), config)
        train, validation, _ = training_data(Path(directory), config)
        self.assertEqual((len(rows), len(train), len(validation)), (5171, 3722, 414))
        self.assertEqual(sum(r["y"] for r in train), 470)
        self.assertEqual(sum(r["y"] for r in validation), 52)
        record = partition_record(train, validation, config)
        self.assertEqual(len(set(record["train_ids"] + record["validation_ids"])), 4136)
        self.assertFalse(set(record["train_ids"] + record["validation_ids"]) & set(manifest["identifiers"]["full1035_ids"]))

    def test_historical_predictions_are_exact_paired400_only(self):
        directory = os.environ.get("SENTINEL_REPO")
        if not directory:
            self.skipTest("set SENTINEL_REPO for the retained historical CSV check")
        manifest = json.loads((BASE / "protocol/sms_split.json").read_text())
        systems, references = import_historical(Path(directory), manifest, CONFIG)
        self.assertEqual(len(systems), 4)
        self.assertEqual(len(references), 2)
        for predictions in systems.values():
            self.assertEqual(list(predictions), manifest["identifiers"]["paired400_ids"])
            self.assertEqual(len(predictions), 400)

    def test_config_refuses_test_tuning_or_changed_served_default(self):
        with tempfile.TemporaryDirectory(dir=TMP) as directory:
            for field, value in (("served_default", "qlora"), ("do_sample", True), ("seed_std_ddof", 1)):
                changed = copy.deepcopy(CONFIG)
                changed["evaluation"][field] = value
                path = Path(directory) / f"{field}.json"
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config(path)


class ArtifactTests(unittest.TestCase):
    def proof(self):
        return {"git_sha": "a" * 40, "config_sha256": "b" * 64, "protocol_sha256": "c" * 64,
                "source_files": [{"path": "example.py", "sha256": "d" * 64}], "versions": {"torch": "candidate"}}

    def test_exclusive_files_preserve_existing_bytes(self):
        with tempfile.TemporaryDirectory(dir=TMP) as directory:
            path = Path(directory) / "file.json"
            exclusive_json(path, {"first": 1})
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                exclusive_json(path, {"first": 2})
            self.assertEqual(path.read_bytes(), before)

    def test_run_refuses_existing_path_and_paths_outside_runtime(self):
        with tempfile.TemporaryDirectory(dir=TMP) as directory:
            config = {**CONFIG, "runtime_root": directory}
            run = OwnedRun(Path(directory) / "runs/one", config, self.proof(), "train", 42)
            with self.assertRaises(FileExistsError):
                OwnedRun(run.path, config, self.proof(), "train", 42)
            with self.assertRaises(ValueError):
                OwnedRun(Path(directory) / "elsewhere", config, self.proof(), "train", 42)
            run.close()

    def test_owned_resume_rejects_source_seed_versions_and_concurrency_changes(self):
        with tempfile.TemporaryDirectory(dir=TMP) as directory:
            config = {**CONFIG, "runtime_root": directory}
            run = OwnedRun(Path(directory) / "runs/one", config, self.proof(), "train", 42)
            with self.assertRaises(FileExistsError):
                OwnedRun(run.path, config, self.proof(), "train", 42, resume=True)
            run.close(RuntimeError("interrupted"))
            changed = {**self.proof(), "git_sha": "e" * 40}
            with self.assertRaises(ValueError):
                OwnedRun(run.path, config, changed, "train", 42, resume=True)
            with self.assertRaises(ValueError):
                OwnedRun(run.path, config, self.proof(), "train", 43, resume=True)
            changed = {**self.proof(), "versions": {"torch": "different"}}
            with self.assertRaises(ValueError):
                OwnedRun(run.path, config, changed, "train", 42, resume=True)
            resumed = OwnedRun(run.path, config, self.proof(), "train", 42, resume=True)
            resumed.result("summary.json", {"status": "trained"})
            resumed.close()
            with self.assertRaises(FileExistsError):
                OwnedRun(run.path, config, self.proof(), "train", 42, resume=True)

    def test_jsonl_has_exactly_one_line_and_preserves_newline_in_raw_output(self):
        value = {"raw": '{\n"label":"scam"\n}'}
        encoded = json_line(value)
        self.assertEqual(len(encoded.splitlines()), 1)
        self.assertEqual(json.loads(encoded), value)

    def test_git_snapshot_uses_explicit_backend_readonly_flags_and_separate_status_scopes(self):
        with tempfile.TemporaryDirectory(dir=TMP) as directory:
            executable = Path(directory) / "private-git"
            executable.write_bytes(b"test backend identity")
            observed = []
            def invoke(args, cwd=None, env=None, timeout_s=120):
                observed.append((args, env))
                output = "git version test" if args[-1] == "--version" else "a" * 40 if "rev-parse" in args else "" if "--" in args else "?? copilot/new-file.py"
                return {"returncode": 0, "stdout": output, "stderr": "", "argv": args}
            with patch.dict(os.environ, {"FINETUNE_GIT": str(executable)}), patch("finetune.artifacts.run_command", side_effect=invoke):
                snapshot = git_snapshot(Path(directory))
            self.assertFalse(snapshot["selected_source_dirty"])
            self.assertTrue(snapshot["whole_worktree_dirty"])
            self.assertEqual(snapshot["selected_source_scope"], list(SELECTED_SOURCE_PATHS))
            for args, env in observed:
                self.assertEqual(env["GIT_OPTIONAL_LOCKS"], "0")
                if "status" in args:
                    self.assertIn("--no-optional-locks", args)
            self.assertEqual(snapshot["git_sha"], "a" * 40)

    def test_git_snapshot_rejects_a_changed_head(self):
        with tempfile.TemporaryDirectory(dir=TMP) as directory:
            executable = Path(directory) / "private-git"
            executable.write_bytes(b"test backend")
            results = [{"returncode": 0, "stdout": value} for value in ("git version test", "a" * 40, "", "", "b" * 40)]
            with patch.dict(os.environ, {"FINETUNE_GIT": str(executable)}), patch("finetune.artifacts.run_command", side_effect=results):
                with self.assertRaisesRegex(RuntimeError, "HEAD changed"):
                    git_snapshot(Path(directory))

    def test_fingerprint_covers_actual_fallback_schema_and_data_source(self):
        with tempfile.TemporaryDirectory(dir=TMP) as directory:
            repo = Path(directory)
            risk = repo / "text-risk/riskclf"
            risk.mkdir(parents=True)
            for name in ("rules.py", "schema.py", "data.py", "__init__.py"):
                (risk / name).write_text("# current repository source\n")
            before = source_fingerprint(repo)
            self.assertEqual(len(before), 4)
            (risk / "rules.py").write_text("# changed fallback\n")
            self.assertNotEqual(before, source_fingerprint(repo))

    def test_provenance_allows_unrelated_dirty_files_but_rejects_selected_dirty_paths(self):
        config = copy.deepcopy(CONFIG)
        config["data"]["manifest"] = str(BASE / "protocol/sms_split.json")
        snapshot = {"git_sha": "a" * 40, "selected_source_scope": list(SELECTED_SOURCE_PATHS),
                    "selected_source_dirty": False, "selected_source_status": {"stdout": ""},
                    "whole_worktree_dirty": True, "whole_worktree_status": {"stdout": "?? copilot/draft.py"}}
        with tempfile.TemporaryDirectory(dir=TMP) as directory:
            with patch("finetune.artifacts.git_snapshot", return_value=snapshot), patch("finetune.artifacts.run_command", return_value={"returncode": 0, "stdout": "mock GPU inventory"}):
                value = provenance(Path(directory), BASE / "configs/qwen15_nf4.json", config)
            self.assertFalse(value["selected_source_dirty"])
            self.assertTrue(value["whole_worktree_dirty"])
            self.assertIn("copilot/draft.py", value["whole_worktree_status"])
            snapshot["selected_source_dirty"] = True
            with patch("finetune.artifacts.git_snapshot", return_value=snapshot):
                with self.assertRaisesRegex(RuntimeError, "selected training/baseline"):
                    provenance(Path(directory), BASE / "configs/qwen15_nf4.json", config)


class MetricsTests(unittest.TestCase):
    def test_known_f1_and_exact_mcnemar(self):
        y, reference, candidate = [0, 0, 1, 1], [0, 1, 0, 1], [0, 0, 1, 1]
        self.assertEqual(binary_metrics(y, reference)["f1"], 0.5)
        self.assertEqual(binary_metrics(y, candidate)["f1"], 1.0)
        test = exact_mcnemar(y, reference, candidate)
        self.assertEqual(test["discordant"], 2)
        self.assertEqual(test["p_value_two_sided_exact"], 0.5)
        self.assertEqual(exact_mcnemar(y, reference, reference)["p_value_two_sided_exact"], 1.0)

    def test_exact_mcnemar_handles_more_than_1024_discordances(self):
        y = [0] * 1035
        result = exact_mcnemar(y, y, [1] * 1035)
        self.assertGreaterEqual(result["p_value_two_sided_exact"], 0)
        self.assertLess(result["p_value_two_sided_exact"], 0.00001)

    def test_paired_bootstrap_identity_difference_is_always_zero(self):
        y, predictions = [0, 0, 1, 1], [0, 1, 0, 1]
        result = paired_bootstrap(y, predictions, predictions, resamples=100, seed=42)
        self.assertEqual(result["paired_f1_difference_ci"], [0, 0])
        self.assertEqual(result, paired_bootstrap(y, predictions, predictions, resamples=100, seed=42))

    def test_population_std_kept_separate(self):
        records = [{"seed": seed, "f1": i, "accuracy": i, "precision": i, "recall": i} for seed, i in zip((42, 43, 44), (0, 1, 2))]
        summary = seed_summary(records)
        self.assertEqual(summary["std_ddof"], 0)
        self.assertAlmostEqual(summary["f1"]["std"], (2 / 3) ** 0.5)
        self.assertEqual(percentile([0, 10], 0.95), 9.5)
        with self.assertRaises(ValueError):
            seed_summary(records[:2])


class PromptTests(unittest.TestCase):
    def test_strict_json_schema_and_duplicate_keys(self):
        self.assertEqual(parse_binary_json('{"label":"scam"}'), (1, None))
        self.assertEqual(parse_binary_json('{"label":"legit"}'), (0, None))
        for raw in ('{"label":"scam", "reason":"x"}', '[]', '{"label":1}', '{"label":["scam"]}'):
            self.assertEqual(parse_binary_json(raw), (None, "schema"))
        for raw in ('```json\n{"label":"scam"}\n```', '{"label":"scam","label":"legit"}'):
            self.assertEqual(parse_binary_json(raw), (None, "invalid_json"))

    def test_assistant_only_loss_mask_and_no_target_truncation(self):
        class Tokenizer:
            def encode(self, text, **kwargs):
                return list(range(len(text)))
            def decode(self, tokens, **kwargs):
                return "x" * len(tokens)
            def apply_chat_template(self, messages, **kwargs):
                prefix = [1, 2, 3, 4]
                return prefix if messages[-1]["role"] == "user" else prefix + [8, 9, 10]
        value, truncated = encode_example(Tokenizer(), {"text": "hello", "y": 1}, CONFIG)
        self.assertFalse(truncated)
        self.assertEqual(value["labels"], [-100, -100, -100, -100, 8, 9, 10])


if __name__ == "__main__":
    unittest.main()
