"""One FastAPI process serving the transaction model and SMS-spam classifier."""
import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, ConfigDict, Field

from .contracts import Alert, ModelReference
from .observability import (DEPENDENCY_READY, HTTP_ERRORS, HTTP_LATENCY, HTTP_REQUESTS,
                            MODEL_LATENCY, SERVICE_READY, database_ready, make_tracer_provider)
from opentelemetry import trace
from opentelemetry.propagators.textmap import DefaultGetter
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

ROOT = Path(__file__).resolve().parents[1]
REQUESTS = Counter("sentinel_gateway_requests_total", "Gateway model requests", ["kind"])
LATENCY = Histogram("sentinel_gateway_seconds", "Gateway model request time", ["kind"])


class Transaction(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    features: dict[str, float]
    transaction_id: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=1, max_length=150)
    publish: bool = True


class TransactionBatch(BaseModel):
    transactions: list[Transaction] = Field(min_length=1, max_length=512)


class TextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=1, max_length=150)
    publish: bool = True


class Publisher:
    def __init__(self, bootstrap):
        from kafka import KafkaProducer
        self.producer = KafkaProducer(bootstrap_servers=bootstrap.split(","), acks="all", retries=3,
                                      max_block_ms=10000, request_timeout_ms=10000,
                                      value_serializer=lambda value: json.dumps(value, allow_nan=False).encode(),
                                      key_serializer=lambda value: value.encode())
        from kafka.admin import KafkaAdminClient
        self.admin = KafkaAdminClient(bootstrap_servers=bootstrap.split(","),
                                     request_timeout_ms=2000, api_version_auto_timeout_ms=2000)
        self.admin_lock = threading.Lock()

    def alert(self, alert):
        # The v1 envelope trace_id remains the caller's stable correlation ID.
        # W3C context travels in Kafka headers, so existing producers/consumers
        # can continue using the unchanged contract.
        tracer = getattr(self, "tracer", trace.get_tracer("sentinel.gateway"))
        with tracer.start_as_current_span("kafka.publish", kind=SpanKind.PRODUCER,
                                          attributes={"messaging.system": "kafka", "messaging.destination.name": os.getenv("ALERTS_TOPIC", "sentinel.alerts.v1")}):
            carrier = {}
            TraceContextTextMapPropagator().inject(carrier)
            headers = [(key, value.encode()) for key, value in carrier.items()]
            headers.append(("trace-id", alert.trace_id.encode()))
            self.producer.send(os.getenv("ALERTS_TOPIC", "sentinel.alerts.v1"), headers=headers,
                               key=alert.alert_id, value=alert.model_dump(mode="json")).get(timeout=15)

    def ready(self):
        if not self.admin_lock.acquire(timeout=.1):
            return False
        try:
            topics = self.admin.describe_topics([os.getenv("ALERTS_TOPIC", "sentinel.alerts.v1")])
        finally:
            self.admin_lock.release()
        return bool(topics) and all(
            topic.get("error_code", 0) == 0 and topic.get("partitions") and all(
                partition.get("error_code", 0) == 0 and partition.get("leader", -1) >= 0
                for partition in topic["partitions"])
            for topic in topics)

    def close(self):
        self.producer.close(timeout=10)
        self.admin.close()


class Models:
    def __init__(self):
        folder = Path(os.getenv("TRANSACTION_MODEL_DIR", ROOT / "serving/model"))
        self.metadata = json.loads((folder / "model_meta.json").read_text())
        self.transaction = joblib.load(folder / "model.joblib")
        self.version = hashlib.sha256((folder / "model.joblib").read_bytes()).hexdigest()
        from riskclf.service import Classifier
        self.text = Classifier(mode=os.getenv("CLASSIFIER_MODE", "tfidf"))

    def ready(self):
        metadata = getattr(self, "metadata", None)
        if not isinstance(metadata, dict):
            return False
        features, threshold = metadata.get("features"), metadata.get("threshold")
        if not isinstance(features, list) or not features or not all(isinstance(name, str) and name for name in features):
            return False
        if len(set(features)) != len(features):
            return False
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not np.isfinite(threshold) or not 0 <= threshold <= 1:
            return False
        transaction, text = getattr(self, "transaction", None), getattr(self, "text", None)
        return (isinstance(metadata.get("model"), str) and bool(metadata["model"])
                and callable(getattr(transaction, "predict_proba", None))
                and getattr(transaction, "n_features_in_", None) == len(features)
                and callable(getattr(text, "classify", None)))

    def score(self, transactions):
        features = self.metadata["features"]
        for transaction in transactions:
            missing = set(features) - transaction.features.keys()
            if missing:
                raise HTTPException(422, f"Missing model features: {sorted(missing)}")
        matrix = np.array([[transaction.features[name] for name in features] for transaction in transactions])
        return self.transaction.predict_proba(matrix)[:, 1]


def create_app(models=None, publisher=None, readiness_checks=None, tracer_provider=None):
    state = {}

    @asynccontextmanager
    async def lifespan(_):
        state["tracer_provider"] = tracer_provider or make_tracer_provider()
        state["tracer"] = state["tracer_provider"].get_tracer("sentinel.gateway")
        state["models"] = models or Models()
        bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS")
        state["publisher"] = publisher or (Publisher(bootstrap) if bootstrap else None)
        if isinstance(state["publisher"], Publisher):
            state["publisher"].tracer = state["tracer"]
        yield
        if state["publisher"] and publisher is None:
            state["publisher"].close()
        if tracer_provider is None:
            state["tracer_provider"].shutdown()

    app = FastAPI(title="Sentinel risk gateway", lifespan=lifespan)

    @app.middleware("http")
    async def observe_request(request: Request, call_next):
        started = time.perf_counter()
        method = request.method if request.method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} else "OTHER"
        parent = TraceContextTextMapPropagator().extract(dict(request.headers), getter=DefaultGetter())
        with state["tracer"].start_as_current_span("gateway.request", context=parent, kind=SpanKind.SERVER) as span:
            status = 500
            try:
                response = await call_next(request)
                status = response.status_code
                response.headers["X-Trace-ID"] = format(span.get_span_context().trace_id, "032x")
                return response
            finally:
                route = getattr(request.scope.get("route"), "path", "unmatched")
                span.set_attributes({"http.request.method": method, "http.route": route, "http.response.status_code": status})
                if status >= 500:
                    span.set_status(Status(StatusCode.ERROR))
                HTTP_REQUESTS.labels(method, route, str(status)).inc()
                if status >= 400:
                    HTTP_ERRORS.labels(method, route, str(status)).inc()
                HTTP_LATENCY.labels(method, route).observe(time.perf_counter() - started)

    def dependencies():
        model = state.get("models")
        checks = {"model": lambda: callable(getattr(model, "ready", None)) and model.ready()}
        if os.getenv("KAFKA_BOOTSTRAP_SERVERS"):
            checks["kafka"] = lambda: state.get("publisher") is not None and state["publisher"].ready()
        if os.getenv("DATABASE_URL"):
            checks["database"] = database_ready
        checks.update(readiness_checks or {})
        outcomes = {}
        for name, check in checks.items():
            try:
                outcomes[name] = bool(check())
            except Exception:
                outcomes[name] = False
            DEPENDENCY_READY.labels(name).set(outcomes[name])
        SERVICE_READY.set(all(outcomes.values()))
        return outcomes

    @app.get("/healthz")
    def live():
        return {"status": "alive"}

    @app.get("/readyz")
    def dependency_ready():
        checks = dependencies()
        return Response(json.dumps({"status": "ready" if all(checks.values()) else "not ready", "checks": checks}),
                        status_code=200 if all(checks.values()) else 503, media_type="application/json")

    def emit(alert):
        require_publisher()
        try:
            state["publisher"].alert(alert)
        except Exception as error:
            raise HTTPException(503, "Alert delivery failed") from error

    def require_publisher():
        if state["publisher"] is None:
            raise HTTPException(503, "Alert publisher unavailable; use publish=false for inference only")

    @app.get("/health/ready")
    def ready():
        model = state["models"]
        return {"status": "ready", "transaction_model": model.metadata["model"],
                "threshold": model.metadata["threshold"], "text_mode": model.text.mode,
                "alert_publisher": state["publisher"] is not None}

    def score_many(transactions):
        started = time.perf_counter()
        if any(transaction.publish for transaction in transactions):
            require_publisher()
        model = state["models"]
        with MODEL_LATENCY.labels("transaction").time():
            probabilities = model.score(transactions)
        threshold = model.metadata["threshold"]
        results = []
        for transaction, probability in zip(transactions, probabilities):
            probability = float(probability)
            flagged = probability >= threshold
            if flagged and transaction.publish:
                emit(Alert(alert_id="transaction-api:" + transaction.transaction_id,
                           event_time=datetime.now(timezone.utc), source="transaction-api",
                           entity_type="transaction", entity_id=transaction.transaction_id,
                           risk_score=probability, threshold=threshold, severity="high",
                           summary="Transaction model threshold exceeded",
                           model=ModelReference(name=model.metadata["model"], version=model.version),
                           evidence={"features": transaction.features}, trace_id=transaction.transaction_id))
            results.append({"transaction_id": transaction.transaction_id, "fraud_score": probability,
                            "flagged": flagged, "threshold": threshold,
                            "alert_published": flagged and transaction.publish})
        REQUESTS.labels("transaction").inc(len(results))
        LATENCY.labels("transaction").observe(time.perf_counter() - started)
        return results

    @app.post("/v1/transactions/score")
    async def score(transaction: Transaction):
        return (await run_in_threadpool(score_many, [transaction]))[0]

    @app.post("/v1/transactions/score-batch")
    async def score_batch(batch: TransactionBatch):
        results = await run_in_threadpool(score_many, batch.transactions)
        return {"n": len(results), "n_flagged": sum(row["flagged"] for row in results), "results": results}

    @app.post("/v1/text/classify")
    async def classify(request: TextRequest):
        if request.publish:
            require_publisher()
        with MODEL_LATENCY.labels("text").time():
            result = await run_in_threadpool(state["models"].text.classify, request.text)
        if result.label == "scam" and request.publish:
            alert = Alert(alert_id="text-risk:" + request.message_id, event_time=datetime.now(timezone.utc),
                          source="text-risk", entity_type="message", entity_id=request.message_id,
                          risk_score=result.risk_score,
                          threshold=0.5 if result.source == "tfidf" else None,
                          severity="medium", summary="SMS spam classifier signal",
                          model=ModelReference(name=result.model, version="sms-seed42-v1"),
                          evidence={"dataset_target": "SMS spam", "classifier_source": result.source,
                                    "score_source": "tfidf_lr" if result.risk_score is not None else "none",
                                    "decision_rule": "classifier label = scam", "label": result.label,
                                    "category": result.category, "reason": result.reason}, trace_id=request.message_id)
            await run_in_threadpool(emit, alert)
        REQUESTS.labels("text").inc()
        return {**result.model_dump(), "message_id": request.message_id, "dataset_target": "SMS spam",
                "score_source": "tfidf_lr" if result.risk_score is not None else "none",
                "alert_published": result.label == "scam" and request.publish}

    @app.get("/metrics")
    def metrics():
        dependencies()
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
