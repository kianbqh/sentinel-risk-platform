"""Exercise a running service using only the Python standard library."""
import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"


def call(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(BASE + path, data=data, headers={"content-type": "application/json"})
    with urlopen(request, timeout=5) as response:
        return json.load(response)


for attempt in range(60):
    try:
        readiness = call("/health/ready")
        break
    except (OSError, ValueError):
        if attempt == 59:
            raise
        time.sleep(1)

single = json.loads((ROOT / "loadtest/sample_txn.json").read_text())
batch = json.loads((ROOT / "loadtest/sample_batch.json").read_text())
one = call("/score", single)
many = call("/score/batch", batch)
first = call("/score", {"features": batch["transactions"][0]})
assert readiness["status"] == "ready"
assert many["n"] == 64
assert abs(first["fraud_score"] - many["results"][0]["fraud_score"]) < 1e-12
assert 0 <= one["fraud_score"] <= 1
print(json.dumps({"base_url": BASE, "readiness": readiness, "single": one,
                  "batch_n": many["n"], "batch_n_flagged": many["n_flagged"],
                  "single_batch_parity": True}, indent=2))
