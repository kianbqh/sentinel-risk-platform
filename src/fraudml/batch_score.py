"""Batch-score a parquet file of transactions with the packaged model."""
import argparse
import json
import time

import joblib
import pandas as pd

from .config import FEATURES_DIR, MODELS_DIR, REPORTS_DIR


def score(df: pd.DataFrame, model=None, meta=None) -> pd.DataFrame:
    model = model or joblib.load(MODELS_DIR / "model.joblib")
    meta = meta or json.loads((MODELS_DIR / "model_meta.json").read_text())
    out = df.copy()
    out["fraud_score"] = model.predict_proba(df[meta["features"]].to_numpy())[:, 1]
    out["flagged"] = out["fraud_score"] >= meta["threshold"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(FEATURES_DIR / "test"))
    ap.add_argument("--output", default=str(REPORTS_DIR / "batch_scores.parquet"))
    args = ap.parse_args()
    t0 = time.perf_counter()
    df = pd.read_parquet(args.input)
    scored = score(df)
    scored[["Time", "Amount", "fraud_score", "flagged"] + (["label"] if "label" in scored else [])].to_parquet(args.output)
    secs = time.perf_counter() - t0
    summary = {"rows_scored": len(scored), "flagged": int(scored["flagged"].sum()),
               "seconds": round(secs, 2), "rows_per_second": int(len(scored) / secs)}
    (REPORTS_DIR / "batch_scoring.json").write_text(json.dumps(summary, indent=2))
    print("[batch]", summary)


if __name__ == "__main__":
    main()
