from types import SimpleNamespace

import numpy as np
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from gateway.main import Models as LoadedModels, Publisher, create_app


class Models:
    metadata = {"model": "test", "threshold": .8}
    text = SimpleNamespace(mode="test")
    version = "test"

    def score(self, rows):
        return np.array([row.features["p"] for row in rows])

    def ready(self):
        return self.text is not None


def test_readiness_transitions_leave_liveness_independent(monkeypatch):
    monkeypatch.delenv("KAFKA_BOOTSTRAP_SERVERS", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    available = {"database": True, "kafka": True}
    checks = {name: lambda name=name: available[name] for name in available}
    models = Models()
    with TestClient(create_app(models, readiness_checks=checks)) as client:
        assert client.get("/readyz").status_code == 200
        for dependency in available:
            available[dependency] = False
            assert client.get("/healthz").status_code == 200
            response = client.get("/readyz")
            assert response.status_code == 503
            assert response.json()["checks"][dependency] is False
            metrics = client.get("/metrics").text
            assert "sentinel_service_ready 0.0" in metrics
            assert f'sentinel_dependency_ready{{dependency="{dependency}"}} 0.0' in metrics
            available[dependency] = True
            assert client.get("/readyz").status_code == 200
        models.text = None
        assert client.get("/readyz").status_code == 503
        assert client.get("/healthz").status_code == 200
        models.text = SimpleNamespace(mode="test")
        assert client.get("/readyz").status_code == 200


def test_loaded_model_components_and_metadata_control_readiness(monkeypatch):
    monkeypatch.delenv("KAFKA_BOOTSTRAP_SERVERS", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    models = LoadedModels.__new__(LoadedModels)
    transaction = SimpleNamespace(predict_proba=lambda _: None, n_features_in_=1)
    text = SimpleNamespace(classify=lambda _: None, mode="test")
    models.transaction, models.text = transaction, text
    models.metadata = {"model": "test", "features": ["p"], "threshold": .8}
    with TestClient(create_app(models)) as client:
        assert client.get("/readyz").status_code == 200
        for component, loaded in (("transaction", transaction), ("text", text)):
            setattr(models, component, None)
            assert client.get("/readyz").status_code == 503
            assert client.get("/healthz").status_code == 200
            setattr(models, component, loaded)
            assert client.get("/readyz").status_code == 200
        for field, invalid in (("features", []), ("features", ["p", "p"]), ("features", ["wrong", 1]),
                               ("threshold", float("nan")), ("threshold", 1.1), ("threshold", True)):
            original = models.metadata[field]
            models.metadata[field] = invalid
            assert client.get("/readyz").status_code == 503
            models.metadata[field] = original
            assert client.get("/readyz").status_code == 200
        transaction.n_features_in_ = 2
        assert client.get("/readyz").status_code == 503


def test_http_error_metrics_bound_routes_and_score_timer(monkeypatch):
    monkeypatch.delenv("KAFKA_BOOTSTRAP_SERVERS", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(create_app(Models())) as client:
        assert client.post("/v1/transactions/score", json={"features": {"p": .9}}).status_code == 503
        assert client.post("/v1/transactions/score", json={"features": {"p": .9}, "publish": False}).status_code == 200
        client.get("/missing-user-specific-id")
        metrics = client.get("/metrics").text
        assert 'sentinel_gateway_http_errors_total{method="POST",route="/v1/transactions/score",status="503"}' in metrics
        assert 'sentinel_gateway_model_score_seconds_count{kind="transaction"}' in metrics
        assert 'route="unmatched"' in metrics
        assert "missing-user-specific-id" not in metrics


def test_gateway_publish_span_injects_w3c_without_changing_v1_id(monkeypatch):
    monkeypatch.delenv("KAFKA_BOOTSTRAP_SERVERS", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    records = []

    class Producer:
        def send(self, topic, **record):
            records.append(record)
            return SimpleNamespace(get=lambda timeout: None)

    publisher = Publisher.__new__(Publisher)
    publisher.producer = Producer()
    with TestClient(create_app(Models(), publisher, tracer_provider=provider)) as client:
        response = client.post("/v1/transactions/score", json={"features": {"p": .9}, "transaction_id": "stable-v1-id"},
                               headers={"traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"})
        assert response.status_code == 200
        assert response.headers["X-Trace-ID"] == "0123456789abcdef0123456789abcdef"
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert spans["kafka.publish"].parent.span_id == spans["gateway.request"].context.span_id
    assert spans["gateway.request"].parent.span_id == int("0123456789abcdef", 16)
    assert records[0]["value"]["trace_id"] == "stable-v1-id"
    headers = {name: value.decode() for name, value in records[0]["headers"]}
    propagated = TraceContextTextMapPropagator().extract(headers)
    from opentelemetry.trace import get_current_span
    context = get_current_span(propagated).get_span_context()
    assert context.trace_id == spans["gateway.request"].context.trace_id
    assert context.span_id == spans["kafka.publish"].context.span_id
    assert headers["trace-id"] == "stable-v1-id"
    provider.shutdown()
