"""Bounded HTTP metrics, explicit dependency probes and W3C trace propagation."""
import os

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter("sentinel_gateway_http_requests_total", "Completed HTTP requests", ["method", "route", "status"])
HTTP_ERRORS = Counter("sentinel_gateway_http_errors_total", "HTTP responses with status >=400", ["method", "route", "status"])
HTTP_LATENCY = Histogram("sentinel_gateway_http_request_seconds", "HTTP handler wall time including scoring and Kafka acknowledgement", ["method", "route"])
MODEL_LATENCY = Histogram("sentinel_gateway_model_score_seconds", "Model scoring wall time excluding Kafka publishing", ["kind"])
SERVICE_READY = Gauge("sentinel_service_ready", "Last live dependency probe succeeded")
DEPENDENCY_READY = Gauge("sentinel_dependency_ready", "Last live dependency probe succeeded", ["dependency"])


def make_tracer_provider():
    provider = TracerProvider(resource=Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "sentinel-gateway")}))
    # Keep the default demo independent of an observability service. The ops
    # profile explicitly supplies this endpoint and uses OTLP HTTP/protobuf.
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if endpoint:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, timeout=3)))
    return provider


def database_ready():
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=2) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() == (1,)
