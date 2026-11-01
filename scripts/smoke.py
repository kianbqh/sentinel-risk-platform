"""Check the running container's real baseline protocol and metrics."""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx
from prometheus_client.parser import text_string_to_metric_families

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from riskclf.schema import ClassifyResponse


def request_count(text):
    return sum(sample.value for family in text_string_to_metric_families(text)
               for sample in family.samples if sample.name == "riskclf_requests_total")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8008")
    parser.add_argument("--out", default="results/container_smoke.json")
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=10) as client:
        for _ in range(60):
            try:
                health = client.get("/healthz")
                if health.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Container did not become healthy")
        assert health.json()["mode"] == "tfidf"
        before = client.get("/metrics"); before.raise_for_status()
        replies = []
        for text in ("Congratulations! Claim your free cash prize now", "See you at lunch tomorrow"):
            response = client.post("/v1/classify", json={"text": text}); response.raise_for_status()
            result = ClassifyResponse.model_validate(response.json())
            assert result.source == "tfidf" and 0 <= result.risk_score <= 1
            replies.append({"label": result.label, "source": result.source, "model": result.model})
        after = client.get("/metrics"); after.raise_for_status()
        assert request_count(after.text) - request_count(before.text) == 2
    output = {"origin": "CI Docker container, CPU TF-IDF mode", "health": health.json(),
              "classify_responses": replies, "metrics_request_delta": 2, "result": "passed"}
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
