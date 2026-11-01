"""FastAPI service: POST /v1/classify, GET /healthz, GET /metrics.

CLASSIFIER_MODE:
  llm     - every request goes to the LLM (cache first; rules on timeout/error/invalid JSON)
  hybrid  - TF-IDF scores first; only uncertain cases (HYBRID_LOW <= p <= HYBRID_HIGH) go to the LLM
  tfidf   - baseline only, no LLM
"""
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from . import PROMPT_VERSION, rules
from .cache import TTLCache
from .llm import LlmClient, LlmError
from .schema import ClassifyRequest, ClassifyResponse

REQS = Counter("riskclf_requests_total", "Classify requests", ["source", "label"])
LAT = Histogram("riskclf_request_seconds", "End-to-end classify latency", ["source"],
                buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16))
LLM_LAT = Histogram("riskclf_llm_call_seconds", "LLM call latency (successful calls)",
                    buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 16))
LLM_ERR = Counter("riskclf_llm_errors_total", "LLM failures that triggered the rule fallback", ["kind"])
CACHE = Counter("riskclf_cache_total", "Cache lookups", ["result"])
TOKENS = Counter("riskclf_llm_tokens_total", "LLM tokens", ["type"])


class Classifier:
    def __init__(self, mode: str | None = None, llm: LlmClient | None = None, tfidf=None, cache: TTLCache | None = None):
        self.mode = mode or os.getenv("CLASSIFIER_MODE", "llm")
        self.low = float(os.getenv("HYBRID_LOW", "0.05"))
        self.high = float(os.getenv("HYBRID_HIGH", "0.95"))
        self.llm = llm if llm is not None else (LlmClient() if self.mode in ("llm", "hybrid") else None)
        if tfidf is None and self.mode in ("hybrid", "tfidf"):
            from .baseline import load
            tfidf = load()
        self.tfidf = tfidf
        self.cache = cache if cache is not None else TTLCache(int(os.getenv("CACHE_MAX_ITEMS", "10000")), float(os.getenv("CACHE_TTL_S", "3600")))

    def _tfidf(self, text: str) -> float:
        return float(self.tfidf.predict_proba([text])[0, 1])

    def classify(self, text: str) -> ClassifyResponse:
        t0 = time.perf_counter()
        norm = " ".join(text.split())
        p = self._tfidf(norm) if self.tfidf is not None else None
        model = "tfidf_lr"
        if self.mode == "tfidf" or (self.mode == "hybrid" and not (self.low <= p <= self.high)):
            label = "scam" if p >= 0.5 else "legit"
            resp = dict(label=label, category="other", reason=f"tfidf p={p:.3f}", source="tfidf")
        else:
            model = self.llm.model
            key = TTLCache.key(PROMPT_VERSION, model, norm)
            hit = self.cache.get(key)
            CACHE.labels("hit" if hit else "miss").inc()
            if hit:
                resp = dict(hit, source="cache")
            else:
                try:
                    r = self.llm.classify(norm)
                    LLM_LAT.observe(r.latency_ms / 1000)
                    TOKENS.labels("prompt").inc(r.prompt_tokens)
                    TOKENS.labels("completion").inc(r.completion_tokens)
                    resp = dict(r.verdict.model_dump(), source="llm")
                    self.cache.put(key, r.verdict.model_dump())
                except LlmError as e:
                    LLM_ERR.labels(e.kind).inc()
                    resp = dict(rules.classify(norm).model_dump(), source="rules")
                    model = "rules"
        out = ClassifyResponse(**resp, risk_score=p, model=model, latency_ms=(time.perf_counter() - t0) * 1000)
        REQS.labels(out.source, out.label).inc()
        LAT.labels(out.source).observe(out.latency_ms / 1000)
        return out


def create_app(classifier: Classifier | None = None) -> FastAPI:
    state = {}

    @asynccontextmanager
    async def lifespan(_app):
        state["clf"] = classifier or Classifier()
        yield

    app = FastAPI(title="llm-risk-classifier", lifespan=lifespan)

    @app.get("/healthz")
    def healthz():
        c = state["clf"]
        return {"status": "ok", "mode": c.mode, "llm_model": c.llm.model if c.llm else None, "cache_items": len(c.cache)}

    @app.post("/v1/classify", response_model=ClassifyResponse)
    async def classify(req: ClassifyRequest):
        return await run_in_threadpool(state["clf"].classify, req.text)

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
