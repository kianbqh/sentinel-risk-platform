"""Send one transaction from the test feature table to the running API."""
import argparse
import json
import urllib.request

import pandas as pd

from fraudml.config import FEATURE_COLS, FEATURES_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--row", type=int, default=0)
    args = parser.parse_args()
    row = pd.read_parquet(FEATURES_DIR / "test").iloc[args.row]
    payload = {"features": {name: float(row[name]) for name in FEATURE_COLS}}
    request = urllib.request.Request(
        args.url.rstrip("/") + "/score",
        data=json.dumps(payload, allow_nan=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        print(json.dumps(json.load(response), indent=2))


if __name__ == "__main__":
    main()
