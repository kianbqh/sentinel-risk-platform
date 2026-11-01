import json

import httpx
import pytest
from fastapi.testclient import TestClient

from riskclf import rules
from riskclf.cache import TTLCache
from riskclf.llm import LlmClient, LlmError, parse_verdict
from riskclf.service import Classifier, create_app


def fake_llm(content: str | None = None, status: int = 200, raise_timeout: bool = False, calls: list | None = None):
    def handler(req: httpx.Request):
        body = json.loads(req.content)
        if calls is not None:
            calls.append(body)
        if raise_timeout:
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}],
                                            "usage": {"prompt_tokens": 50, "completion_tokens": 20}})
    return LlmClient(base_url="http://fake/v1", model="fake", api_key="", timeout_s=1, transport=httpx.MockTransport(handler))


GOOD = json.dumps({"label": "scam", "category": "prize_or_lottery", "reason": "prize bait"})


class FakeTfidf:
    def __init__(self, p): self.p = p
    def predict_proba(self, xs): return __import__("numpy").array([[1 - self.p, self.p] for _ in xs])


def client(clf):
    return TestClient(create_app(clf))


def test_valid_llm_json_is_returned_and_request_uses_json_schema():
    calls = []
    with client(Classifier(mode="llm", llm=fake_llm(GOOD, calls=calls))) as c:
        r = c.post("/v1/classify", json={"text": "You WON a prize!"})
    assert r.status_code == 200
    body = r.json()
    assert body["label"] == "scam" and body["source"] == "llm" and body["model"] == "fake"
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[0]["temperature"] == 0
    assert calls[0]["seed"] == 42


def test_second_identical_request_is_served_from_cache():
    calls = []
    with client(Classifier(mode="llm", llm=fake_llm(GOOD, calls=calls))) as c:
        c.post("/v1/classify", json={"text": "Free  entry   now"})
        r = c.post("/v1/classify", json={"text": "Free entry now"})  # whitespace-normalised key
    assert r.json()["source"] == "cache"
    assert len(calls) == 1


@pytest.mark.parametrize("llm,kind", [
    (fake_llm("not json at all"), "invalid_json"),
    (fake_llm(json.dumps({"label": "maybe", "category": "other", "reason": "x"})), "schema"),
    (fake_llm(json.dumps({"label": "scam", "category": "other", "reason": "x", "extra": 1})), "schema"),
    (fake_llm(GOOD, status=500), "http"),
    (fake_llm(raise_timeout=True), "timeout"),
])
def test_failures_fall_back_to_rules_and_are_counted(llm, kind):
    text = "URGENT! claim your £1000 cash prize, txt WIN to 87121"
    with client(Classifier(mode="llm", llm=llm)) as c:
        r = c.post("/v1/classify", json={"text": text})
        metrics = c.get("/metrics").text
    assert r.status_code == 200
    assert r.json()["source"] == "rules" and r.json()["label"] == rules.classify(text).label == "scam"
    assert f'riskclf_llm_errors_total{{kind="{kind}"}}' in metrics


def test_failed_llm_answers_are_not_cached():
    calls = []
    with client(Classifier(mode="llm", llm=fake_llm("oops", calls=calls))) as c:
        c.post("/v1/classify", json={"text": "hello"}); c.post("/v1/classify", json={"text": "hello"})
    assert len(calls) == 2


def test_malformed_completion_envelopes_fall_back_to_rules():
    for payload in ("not json", '{}', '{"choices":[]}', '{"choices":[{"message":{"content":null}}]}'):
        transport = httpx.MockTransport(lambda request, payload=payload: httpx.Response(200, text=payload))
        llm = LlmClient(base_url="http://fake/v1", model="fake", transport=transport)
        assert Classifier(mode="llm", llm=llm).classify("hello").source == "rules"


def test_hybrid_only_calls_llm_for_uncertain_scores():
    calls = []
    sure = Classifier(mode="hybrid", llm=fake_llm(GOOD, calls=calls), tfidf=FakeTfidf(0.99))
    unsure = Classifier(mode="hybrid", llm=fake_llm(GOOD, calls=calls), tfidf=FakeTfidf(0.5))
    assert sure.classify("x").source == "tfidf" and len(calls) == 0
    out = unsure.classify("x")
    assert out.source == "llm" and out.risk_score == 0.5 and len(calls) == 1


def test_input_validation_and_health():
    with client(Classifier(mode="llm", llm=fake_llm(GOOD))) as c:
        assert c.post("/v1/classify", json={"text": ""}).status_code == 422
        assert c.post("/v1/classify", json={"text": "x" * 2001}).status_code == 422
        h = c.get("/healthz").json()
    assert h["status"] == "ok" and h["mode"] == "llm"


def test_parse_verdict_accepts_fenced_json_and_rejects_garbage():
    assert parse_verdict("```json\n" + GOOD + "\n```").label == "scam"
    with pytest.raises(LlmError):
        parse_verdict("{label: scam}")


def test_ttl_cache_expires_and_evicts():
    c = TTLCache(max_items=2, ttl_s=0.0)
    assert Classifier(mode="llm", llm=fake_llm(GOOD), cache=c).cache is c
    c.put("a", 1)
    assert c.get("a") is None  # ttl 0 -> expired
    c = TTLCache(max_items=2, ttl_s=60)
    c.put("a", 1); c.put("b", 2); c.put("c", 3)
    assert c.get("a") is None and c.get("c") == 3


def test_rules_examples():
    assert rules.classify("Congratulations you won a FREE prize! Call 09061234567 to claim").label == "scam"
    assert rules.classify("see you at lunch tomorrow").label == "legit"
