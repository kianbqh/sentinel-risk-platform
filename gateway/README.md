# Shared risk gateway

One FastAPI process loads the credit-card model from `../serving/model/` and the SMS TF-IDF model from `../text-risk/models/`. The transaction model uses OpenML 1597; the text model uses UCI SMS Spam Collection.

- `POST /v1/transactions/score`: one engineered feature map.
- `POST /v1/transactions/score-batch`: up to 512 maps, with the same feature order and threshold.
- `POST /v1/text/classify`: SMS label and its actual score source.
- `GET /health/ready`, `GET /metrics`.
- `GET /healthz`: process liveness with no dependency calls.
- `GET /readyz`: live model, configured Kafka and configured PostgreSQL checks.

Requests publish flagged signals to `sentinel.alerts.v1` by default. A caller can set `publish:false` for inference only. A missing publisher or failed Kafka acknowledgement returns503; the response never reports a silently dropped alert as published. Caller-supplied transaction/message IDs make retries idempotent at the console.

An LLM-only result keeps `risk_score:null`; its label decides the alert. Hybrid results retain the TF-IDF score with explicit provenance, and a null probability threshold when the LLM/cache/rule route made the label decision. Default Compose uses the stronger TF-IDF baseline. The original hybrid, cache and timeout behavior remains in `riskclf`.

`python -m gateway.demo` is the Compose integration runner. It checks the real ordered credit-card prefix against the Python model, validates every producer's wire envelope, reconciles the console IDs, exercises the analyst workflow and retries one alert ID. Raw scores, wire records and the summary go to `../results/integration/`.

## Observability

The [ops profile](../ops/README.md) supplies `DATABASE_URL` and `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` (`http://jaeger:4318/v1/traces`) to enable the database readiness check and [OTLP HTTP/protobuf export](https://opentelemetry.io/docs/languages/python/exporters/). `OTEL_SERVICE_NAME` defaults to `sentinel-gateway`. Without the exporter endpoint, tracing does not contact a collector. Kafka readiness uses a broker metadata request for `ALERTS_TOPIC`, rather than a cached connection flag. A dependency failure returns 503 from `/readyz`; `/healthz` still returns 200. The original `/health/ready` model-information endpoint remains available to the original demo. An inference-only process without Kafka or a database configured checks only its loaded models.

`/metrics` refreshes `sentinel_service_ready` and `sentinel_dependency_ready{dependency}` from live probes. HTTP metrics use only registered route templates (or `unmatched`), a bounded method set and status: `sentinel_gateway_http_requests_total`, `sentinel_gateway_http_errors_total` and `sentinel_gateway_http_request_seconds`. Their timer includes scoring and acknowledged Kafka delivery. `sentinel_gateway_model_score_seconds{kind}` times only transaction prediction or text classification. The original model-request counters remain available.

`gateway.request` is the server span; `kafka.publish` is its producer child. Kafka headers carry W3C `traceparent` (and `tracestate` when present). The v1 envelope `trace_id` and the `trace-id` compatibility header keep the stable caller transaction/message ID. The response `X-Trace-ID` identifies the W3C trace. No case IDs, request IDs, message text or feature values become metric labels.

Run the bounded checks with `PYTHONPATH=.:text-risk python -m pytest --capture=sys -q gateway/tests`. The observability tests cover dependency failure/recovery, independent liveness, bounded route labels and actual W3C injection by the publisher with an in-memory exporter. The ops integration check separately queries Jaeger for the cross-service trace.

## Limitations

Dependency probes reflect the instant they run; they cannot guarantee the next Kafka publish or database transaction succeeds. PostgreSQL readiness is enabled when `DATABASE_URL` is configured, as it is in the ops deployment. A caller-supplied correlation ID is preserved alongside the W3C trace ID; these identifiers have different purposes. This is local/CI observability, with no managed-cloud deployment claim.
