import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = json.loads((ROOT / "loadtest" / "sample_txn.json").read_text())
BATCH = json.loads((ROOT / "loadtest" / "sample_batch.json").read_text())


@pytest.fixture(scope="module")
def client():
    main.load_model(ROOT / "model")
    with TestClient(main.app) as c:
        yield c


def test_liveness(client):
    assert client.get("/health/live").json() == {"status": "alive"}


def test_readiness_reports_model(client):
    r = client.get("/health/ready").json()
    assert r["status"] == "ready" and r["model"] == "logreg_balanced"


def test_score_single(client):
    r = client.post("/score", json=SAMPLE)
    assert r.status_code == 200
    body = r.json()
    assert 0.0 <= body["fraud_score"] <= 1.0 and isinstance(body["flagged"], bool)


def test_missing_feature_is_422(client):
    assert client.post("/score", json={"features": {"V1": 0.1}}).status_code == 422


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_feature_is_422(client, value):
    sample = {"features": {**SAMPLE["features"], "V1": value}}
    assert client.post("/score", json=sample).status_code == 422
    assert client.post("/score/batch", json={"transactions": [sample["features"]]}).status_code == 422


def test_batch_matches_single(client):
    b = client.post("/score/batch", json=BATCH).json()
    assert b["n"] == len(BATCH["transactions"])
    first = client.post("/score", json={"features": BATCH["transactions"][0]}).json()
    assert abs(first["fraud_score"] - b["results"][0]["fraud_score"]) < 1e-12


def test_batch_limit(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_BATCH", 2)
    assert client.post("/score/batch", json=BATCH).status_code == 413


def test_empty_batch_rejected(client):
    assert client.post("/score/batch", json={"transactions": []}).status_code == 422


def test_metrics_exposed(client):
    client.post("/score", json=SAMPLE)
    text = client.get("/metrics").text
    assert "fraud_requests_total" in text and "fraud_request_seconds_bucket" in text
