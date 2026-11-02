"""Test evidence gates, rather than mirroring service instrumentation."""
import importlib.util
from pathlib import Path
import sys

import pytest

OPS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OPS))
from verify import validate_trace
from evidence import write_json
import evidence
from evidence import RunJournal


def trace_fixture():
    spans = []
    for index, name in enumerate(["gateway.request", "kafka.publish", "kafka.consume", "postgres.transaction"]):
        spans.append({"spanID": str(index), "traceID": "a" * 32, "operationName": name,
                      "processID": "g" if index < 2 else "b",
                      "references": [] if index == 0 else [{"refType": "CHILD_OF", "spanID": str(index - 1)}]})
    return {"spans": spans, "processes": {"g": {"serviceName": "sentinel-gateway"}, "b": {"serviceName": "sentinel-console"}}}


def test_four_names_without_parent_links_fail():
    fixture = trace_fixture()
    names = [span["operationName"] for span in fixture["spans"]]
    assert validate_trace(fixture, names, ["sentinel-gateway", "sentinel-console"])["span_count"] == 4
    fixture["spans"][-1]["references"] = []
    with pytest.raises(AssertionError, match="No parent path"):
        validate_trace(fixture, names, ["sentinel-gateway", "sentinel-console"])


def test_missing_service_or_mixed_trace_is_not_accepted():
    fixture = trace_fixture()
    with pytest.raises(AssertionError, match="missing spans/services"):
        validate_trace(fixture, [], ["missing"])
    fixture["spans"][-1]["traceID"] = "b" * 32
    with pytest.raises(AssertionError, match="one trace"):
        validate_trace(fixture, [], [])


def test_evidence_cannot_overwrite_legacy_results():
    with pytest.raises(ValueError, match="Ops outputs"):
        write_json(OPS.parent / "results/integration/summary.json", {"test": True})


def test_attempt_is_reserved_before_any_fault_and_keeps_raw_events(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "ROOT", tmp_path)
    path = tmp_path / "results/ops/attempt.json"
    journal = RunJournal(path)
    journal.record("outage_offset_sample", committed=4, end=25)
    # A concurrent/repeated invocation must fail before it can inject a fault.
    with pytest.raises(FileExistsError):
        RunJournal(path)
    journal.close()
    import json
    row = json.loads(journal.path.read_text())
    assert row["committed"] == 4 and row["end"] == 25
    assert row["event"] == "outage_offset_sample"


def test_existing_output_is_rejected_before_attempt_is_created(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "ROOT", tmp_path)
    path = tmp_path / "results/ops/attempt.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"status":"passed"}')
    with pytest.raises(FileExistsError):
        RunJournal(path)
    assert not path.with_suffix(".events.jsonl").exists()
