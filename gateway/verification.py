"""Independent reference checks for the real Kafka-to-console demo."""
import math
import json


def completed_progress(records, topic, terminal_offset, expected_rows):
    cumulative = 0
    for record in records:
        cumulative += record["numInputRows"]
        if cumulative > expected_rows:
            raise RuntimeError("Spark progress consumed more input than the bounded replay")
        for source in record.get("sources", []):
            offsets = source.get("endOffset")
            if isinstance(offsets, str):
                offsets = json.loads(offsets)
            if isinstance(offsets, dict) and offsets.get(topic, {}).get("0") == terminal_offset and cumulative == expected_rows:
                return {"completed_progress": record, "cumulative_input_rows": cumulative,
                        "terminal_input_offset": terminal_offset}
    return None


def probability(value, name):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= 1):
        raise RuntimeError(f"Invalid {name}: {value!r}")
    return float(value)


def verify_stream(scores, reference, threshold):
    threshold = probability(threshold, "reference threshold")
    ids = [row["txnId"] for row in scores]
    if any(type(identity) is not int for identity in ids) or len(ids) != len(set(ids)) or set(ids) != set(reference):
        raise RuntimeError("Missing, extra or duplicate stream score IDs")
    maximum = 0.0
    for row in scores:
        expected = probability(reference[row["txnId"]], "Python reference probability")
        actual = probability(row["proba"], "stream probability")
        maximum = max(maximum, abs(actual - expected))
        if type(row["isAlert"]) is not bool or row["isAlert"] != (expected >= threshold):
            raise RuntimeError("Stream alert flag differs from the Python threshold decision")
    if maximum > 1e-10:
        raise RuntimeError(f"Python/Spark scoring mismatch: {maximum}")
    alerts = {f"transaction-stream:{identity}" for identity, value in reference.items() if value >= threshold}
    return maximum, alerts


def verify_alert_values(wire, console, reference):
    if {row["alert_id"] for row in wire} != set(reference) or set(console) != set(reference):
        raise RuntimeError("Alert IDs differ between producer references, Kafka and console")
    checked = 0
    for stage, rows, id_key, score_key in (
            ("Kafka", wire, "alert_id", "risk_score"),
            ("console", list(console.values()), "id", "riskScore")):
        for row in rows:
            expected = reference[row[id_key]]
            if row["source"] != expected["source"]:
                raise RuntimeError(f"{stage} alert source differs from its producer")
            for key, actual_key in (("risk_score", score_key), ("threshold", "threshold")):
                actual, wanted = row[actual_key], expected[key]
                if wanted is None:
                    if actual is not None:
                        raise RuntimeError(f"{stage} invented a nullable {key}")
                elif actual is None or abs(probability(actual, stage + " " + key) - probability(wanted, "reference " + key)) > 1e-10:
                    raise RuntimeError(f"{stage} {key} differs from its producer reference")
            checked += 1
    return checked
