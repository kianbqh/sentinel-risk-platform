"""Fail unless every full replay has complete, finite and matching output."""
import hashlib
import json
import math
from pathlib import Path


root = Path(__file__).resolve().parents[1]
results = root / "results"
expected_rows = 283726
cross_language = json.loads((results / "verify_against_pyspark.json").read_text())
expected_alerts = cross_language["full_stream"]["alerts"]
assert cross_language["rows_scala_batch"] == cross_language["rows_pyspark"] == cross_language["rows_joined"] == expected_rows
assert cross_language["txn_count_1h_mismatches"] == cross_language["alert_mismatches_vs_sklearn"] == 0
validated = {}
for run in ("live1k", "live5k", "live10k", "backlog20k", "backlog"):
    report = json.loads((results / f"{run}.json").read_text())
    producer = json.loads((results / f"{run}_producer.json").read_text())
    assert report["rows_in_scores_topic"] == report["distinct_txn_ids"] == producer["events"] == expected_rows, run
    for field in ("duplicates", "missing_in_stream", "extra_in_stream", "txn_count_mismatches",
                  "alert_decision_mismatches", "alert_topic_id_mismatches"):
        assert report[field] == 0, (run, field, report[field])
    for field, limit in (("max_abs_proba_diff", 1e-12), ("max_abs_logit_diff", 1e-9),
                         ("max_abs_amount_z_diff", 1e-9), ("max_abs_amount_ratio_diff", 1e-9)):
        value = report[field]
        assert value is not None and math.isfinite(value) and value <= limit, (run, field, value)
    assert report["records_in_alerts_topic"] == report["stream_alerts"] == expected_alerts, run
    progress = [json.loads(line) for line in (results / f"{run}_progress.jsonl").read_text().splitlines() if line.strip()]
    consumed = sum(batch["numInputRows"] for batch in progress)
    assert consumed == expected_rows + 1, (run, "input rows including flush marker", consumed)
    active = [batch for batch in progress if batch["numInputRows"] > 0]
    seconds = sum(batch["batchDuration"] for batch in active) / 1000
    assert math.isfinite(seconds) and seconds > 0
    validated[run] = {"scores": expected_rows, "alerts": expected_alerts, "consumed_with_flush_marker": consumed,
                      "nonempty_micro_batches": len(active), "spark_input_rows_per_second": consumed / seconds,
                      "report_sha256": hashlib.sha256((results / f"{run}.json").read_bytes()).hexdigest()}
output = {"validated_runs": validated, "cross_language_rows": expected_rows, "status": "all full replays passed"}
(results / "validation.json").write_text(json.dumps(output, indent=2) + "\n")
print(json.dumps(output, indent=2))
