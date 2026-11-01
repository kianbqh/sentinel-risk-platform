"""Export the stripe-fraud-ml logistic-regression pipeline to plain JSON for the Scala scorer.

The selected model is StandardScaler + LogisticRegression, so scoring is
p = sigmoid(intercept + sum_i coef_i * (x_i - mean_i) / scale_i).
Exporting the coefficients lets the Spark job score in-process (no network hop,
no Python worker). The script also writes a parity fixture: 500 real feature
rows with sklearn's predict_proba, which the ScalaTest suite checks.

Usage: python scripts/export_model.py --repo ../stripe-fraud-ml
"""
import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--repo", required=True, help="path to a clone of kianbqh/stripe-fraud-ml")
ap.add_argument("--features", default=None, help="optional features parquet dir for the parity fixture")
args = ap.parse_args()
repo = Path(args.repo)
out = Path(__file__).resolve().parents[1] / "model"
out.mkdir(exist_ok=True)

pipe = joblib.load(repo / "models" / "model.joblib")
meta = json.loads((repo / "models" / "model_meta.json").read_text())
scaler = pipe.named_steps["standardscaler"]
lr = pipe.named_steps["logisticregression"]
assert lr.coef_.shape == (1, len(meta["features"]))

export = {
    "source": "kianbqh/stripe-fraud-ml models/model.joblib",
    "model": meta["model"],
    "features": meta["features"],
    "mean": scaler.mean_.tolist(),
    "scale": scaler.scale_.tolist(),
    "coef": lr.coef_[0].tolist(),
    "intercept": float(lr.intercept_[0]),
    "threshold": float(meta["threshold"]),
}
(out / "lr_model.json").write_text(json.dumps(export, indent=1))
print(f"wrote {out/'lr_model.json'} ({len(export['features'])} features, threshold={export['threshold']})")

# Parity fixture: real rows if a features parquet is supplied, else random rows around the scaler means.
if args.features:
    df = pd.read_parquet(args.features)
    fr = df[df["label"] == 1].head(100)
    X = pd.concat([fr, df.sample(400, random_state=7)])[meta["features"]].to_numpy()
else:
    rng = np.random.default_rng(7)
    X = scaler.mean_ + rng.standard_normal((500, len(meta["features"]))) * scaler.scale_
p = pipe.predict_proba(X)[:, 1]
fix = pd.DataFrame(X, columns=meta["features"])
fix["sklearn_proba"] = p
fix.to_csv(out / "sklearn_parity.csv", index=False, float_format="%.17g")
print(f"wrote {out/'sklearn_parity.csv'} ({len(fix)} rows, {int((p >= export['threshold']).sum())} above threshold)")
