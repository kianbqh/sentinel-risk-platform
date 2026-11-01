"""Cross-check the Scala batch reference against the original stripe-fraud-ml PySpark features + sklearn model.

Usage: python scripts/verify_against_pyspark.py --repo ../stripe-fraud-ml --features ../stripe-fraud-ml/data/features
(needs the stripe-fraud-ml pipeline to have been run so data/features/{train,test} exist)
"""
import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--repo", required=True)
ap.add_argument("--features", required=True)
a = ap.parse_args()
root = Path(__file__).resolve().parents[1]
b = pd.read_parquet(root / "data/batch_scores.parquet")
r = pd.read_parquet(root / "data/replay.parquet")
py = pd.concat([pd.read_parquet(Path(a.features) / s).assign(_split=s) for s in ("train", "test")])
if not (len(b) == len(r) == len(py) == 283726):
    raise ValueError(f"Expected 283726 rows in Scala batch/replay/PySpark; got {len(b)}/{len(r)}/{len(py)}")
m = b.merge(r[["txn_id", "v"]], left_on="txnId", right_on="txn_id", validate="one_to_one")
m["V1"], m["V2"] = m.v.str[0], m.v.str[1]
j = m.merge(py, left_on=["time", "amount", "V1", "V2"], right_on=["Time", "Amount", "V1", "V2"], validate="one_to_one")
if len(j) != len(b):
    raise ValueError("Cross-language join did not cover every transaction")
pipe = joblib.load(Path(a.repo) / "models/model.joblib")
meta = json.loads((Path(a.repo) / "models/model_meta.json").read_text())
p = pipe.predict_proba(j[meta["features"]].to_numpy())[:, 1]
test = b[b.txnId.isin(j.loc[j._split == "test", "txnId"])]
out = {
    "rows_scala_batch": len(b), "rows_pyspark": len(py), "rows_joined": len(j),
    "txn_count_1h_mismatches": int((j.txnCount1h != j.txn_count_1h).sum()),
    "max_abs_amount_z_1h_diff": float(np.abs(j.amountZ1h - j.amount_z_1h).max()),
    "max_abs_amount_ratio_1h_diff": float(np.abs(j.amountRatio1h - j.amount_ratio_1h).max()),
    "max_abs_proba_diff_vs_sklearn_on_pyspark_features": float(np.abs(p - j.proba).max()),
    "alert_mismatches_vs_sklearn": int(((p >= meta["threshold"]) != j.isAlert).sum()),
    "full_stream": {"rows": len(b), "fraud": int(b.label.sum()), "alerts": int(b.isAlert.sum()),
                    "true_positives": int((b.isAlert & (b.label == 1)).sum())},
    "test_period": {"rows": len(test), "fraud": int(test.label.sum()), "alerts": int(test.isAlert.sum()),
                    "true_positives": int((test.isAlert & (test.label == 1)).sum()),
                    "time_min": float(test.time.min()), "time_max": float(test.time.max())},
}
checks = [out["max_abs_amount_z_1h_diff"], out["max_abs_amount_ratio_1h_diff"],
          out["max_abs_proba_diff_vs_sklearn_on_pyspark_features"]]
if not np.isfinite(checks).all() or max(checks[:2]) > 1e-9 or checks[2] > 1e-12:
    raise ValueError(f"Feature/scorer parity failed: {out}")
if out["txn_count_1h_mismatches"] or out["alert_mismatches_vs_sklearn"]:
    raise ValueError(f"Count/alert parity failed: {out}")
(root / "results/verify_against_pyspark.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2))
