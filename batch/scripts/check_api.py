"""Check a running API against batch scores from the packaged model."""
import argparse
import json
import urllib.error
import urllib.request

import pandas as pd

from fraudml.batch_score import score
from fraudml.config import FEATURE_COLS, FEATURES_DIR, REPORTS_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    with urllib.request.urlopen(base + "/health", timeout=30) as response:
        health = json.load(response)
    assert health["status"] == "ok", health

    batch = score(pd.read_parquet(FEATURES_DIR / "test"))
    selected = batch.loc[[batch["fraud_score"].idxmin(), batch["fraud_score"].idxmax()]]
    comparisons = []
    for _, row in selected.iterrows():
        payload = {"features": {name: float(row[name]) for name in reversed(FEATURE_COLS)}}
        request = urllib.request.Request(
            base + "/score", data=json.dumps(payload, allow_nan=False).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            actual = json.load(response)
        assert abs(actual["fraud_score"] - row["fraud_score"]) < 1e-12
        assert actual["flagged"] == bool(row["flagged"])
        comparisons.append(actual)

    incomplete = urllib.request.Request(
        base + "/score", data=b'{"features": {"V1": 0.0}}',
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(incomplete, timeout=30)
    except urllib.error.HTTPError as error:
        missing_status = error.code
    else:
        raise AssertionError("The API accepted an incomplete feature vector")
    assert missing_status == 422
    report = {
        "health": health,
        "rows_checked": len(comparisons),
        "api_batch_agree": True,
        "missing_features_status": missing_status,
        "responses": comparisons,
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "api_check.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
