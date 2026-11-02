"""Build immutable fixed query/gallery IDs from original ground truth."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from copilot.scripts.provenance import RunRecord, sha256
from .evidence import ATO_FEATURES, fixed_split, record, validate_protocol


def build(config_path):
    run = RunRecord(config_path)
    config, root = run.config, run.root
    try:
        import pandas as pd
        sys.path.insert(0, str(root))
        from ato.datagen import generate
        from ato.features import window_features
        fixture = root / "gateway/fixtures/creditcard-prefix-10000.parquet"
        if sha256(fixture) != config["fixture_sha256"]:
            raise ValueError("Real card fixture hash mismatch")
        frame = pd.read_parquet(fixture)
        card_features = transaction_features(frame)
        records = [record(f"card:{int(row.txn_id)}", "card", f"card:{int(row.txn_id)}", values, int(row.label))
            for row, values in zip(frame.itertuples(), card_features)]
        # This is the existing labeled generator, not classifier predictions.
        ato_config = config["ato_generator"]
        windows = window_features(generate(**ato_config))
        for row in windows.to_dict("records"):
            entity = hashlib.sha256(row["ip"].encode()).hexdigest()
            records.append(record(f"ato:{entity}:{int(row['win'])}", "ato", f"ato:{entity}",
                {name: row[name] for name in ATO_FEATURES}, int(row["label"])))
        selected = fixed_split(records, config["seed"], config["query_per_cell"], config["gallery_per_cell"])
        counts = validate_protocol(selected)
        protocol = {"schema_version": "1.0", "seed": config["seed"], "records": selected,
            "source_hashes": {str(p.relative_to(root)): sha256(p) for p in [fixture, root/"ato/datagen.py", root/"ato/features.py", root/"gateway/demo.py"]},
            "ground_truth": {"card": "bundled real OpenML fixture label", "ato": "synthetic generator label aggregated max per IP/window"},
            "counts": counts, "ato_generator": ato_config,
            "embedding_excludes": ["gold_label", "label", "kind", "risk_score", "severity", "flagged", "summary", "model", "case_id", "alert_id", "entity_id", "ip", "username"]}
        run.write("protocol.json", protocol)
        run.finish({"status": "complete", "counts": counts, "protocol_sha256": sha256(run.output/"protocol.json")})
    except Exception as exc:
        run.fail(type(exc).__name__)
        raise


def transaction_features(frame):
    """Same inclusive window definition as gateway.demo, without service imports."""
    import math
    import numpy as np
    times, amounts = frame.time.to_numpy(float), frame.amount.to_numpy(float)
    starts = np.searchsorted(times, times-3600, side="left")
    ends = np.searchsorted(times, times, side="right")
    result = []
    for i, row in enumerate(frame.itertuples(index=False)):
        window = amounts[starts[i]:ends[i]]
        mean, std = float(np.mean(window)), float(np.std(window))
        values = {f"V{j+1}": float(v) for j,v in enumerate(row.v)}
        values.update(Amount=float(row.amount), log_amount=math.log1p(row.amount),
            hour_of_day=math.floor((row.time % 86400)/3600), txn_count_1h=len(window),
            amount_ratio_1h=row.amount/(mean+1), amount_z_1h=(row.amount-mean)/std if std>0 else 0.)
        result.append(values)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    build(parser.parse_args().config)
