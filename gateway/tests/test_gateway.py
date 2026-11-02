from pathlib import Path
from types import SimpleNamespace
import json

import jsonschema
import numpy as np
import pytest
from fastapi.testclient import TestClient
from gateway.contracts import Alert
from gateway.main import create_app

ROOT = Path(__file__).resolve().parents[2]

class Text:
    mode = "test"
    def __init__(self, source="tfidf", score=.9):
        self.source, self.score = source, score
    def classify(self, _):
        values = dict(label="scam", category="other", reason="fixture", source=self.source,
                      risk_score=self.score, model="test", latency_ms=0)
        return SimpleNamespace(**values, model_dump=lambda: values)

class Models:
    metadata = {"model": "fixture-lr", "threshold": .8}
    version = "fixture-v1"
    def __init__(self, source="tfidf", score=.9): self.text = Text(source, score)
    def score(self, rows): return np.array([row.features["p"] for row in rows])
    def ready(self): return True

class Publisher:
    def __init__(self): self.events = []
    def alert(self, event): self.events.append(event.model_dump(mode="json"))

@pytest.fixture
def schema(): return json.loads((ROOT / "contracts/alert-v1.schema.json").read_text())

def test_shared_fixture(schema):
    event = json.loads((ROOT / "contracts/examples/alert-v1.json").read_text())
    jsonschema.validate(event, schema); Alert.model_validate(event)
    event["evidence"]["synthetic"] = False
    with pytest.raises(ValueError): Alert.model_validate(event)

def test_batch_publishes_only_flagged_and_validates_contract(schema):
    publisher = Publisher()
    with TestClient(create_app(Models(), publisher)) as client:
        r = client.post("/v1/transactions/score-batch", json={"transactions":[
            {"features":{"p":.9},"transaction_id":"high"}, {"features":{"p":.1},"transaction_id":"low"}]})
        assert r.status_code == 200
        assert r.json()["n_flagged"] == 1
    assert len(publisher.events) == 1
    jsonschema.validate(publisher.events[0], schema)
    assert publisher.events[0]["alert_id"] == "transaction-api:high"

@pytest.mark.parametrize("source,score,threshold", [("tfidf",.9,.5),("llm",None,None),("llm",.1,None),("cache",.2,None),("rules",None,None)])
def test_text_decision_keeps_probability_provenance(schema, source, score, threshold):
    publisher = Publisher()
    with TestClient(create_app(Models(source, score), publisher)) as client:
        r = client.post("/v1/text/classify", json={"text":"fixture","message_id":"msg"})
        assert r.status_code == 200 and r.json()["alert_published"]
    event = publisher.events[0]; jsonschema.validate(event, schema)
    assert event["risk_score"] == score and event["threshold"] == threshold
    assert event["evidence"]["score_source"] == ("none" if score is None else "tfidf_lr")
    assert event["evidence"]["decision_rule"] == "classifier label = scam"

@pytest.mark.parametrize("path,body", [("/v1/transactions/score",{"features":{"p":.1}}),("/v1/text/classify",{"text":"hello"})])
def test_publish_requires_delivery(path, body, monkeypatch):
    monkeypatch.delenv("KAFKA_BOOTSTRAP_SERVERS", raising=False)
    with TestClient(create_app(Models())) as client:
        assert client.post(path, json=body).status_code == 503
        assert client.post(path, json={**body,"publish":False}).status_code == 200

def test_delivery_failure_is_not_success():
    class Broken:
        def alert(self, _): raise TimeoutError("broker offline")
    with TestClient(create_app(Models(), Broken())) as client:
        assert client.post("/v1/transactions/score", json={"features":{"p":.9}}).status_code == 503

def test_contract_rejects_wrong_entity():
    event = json.loads((ROOT / "contracts/examples/alert-v1.json").read_text())
    event["entity_type"] = "message"
    with pytest.raises(ValueError): Alert.model_validate(event)
