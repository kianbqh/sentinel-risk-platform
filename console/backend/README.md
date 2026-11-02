# Alert consumer and GraphQL service

The service stores each alert, initial case and ingestion event in one PostgreSQL transaction, then commits its Kafka offset. Repeated alert IDs retain one case. JWT authorization and request-scoped GraphQL dataloaders share the existing analyst workflow.

## Run and check

Use the root Compose scripts for the existing demo, or the [ops profile](../../ops/README.md) for Prometheus and Jaeger. `DATABASE_URL` and `JWT_SECRET` are required. `KAFKA_BOOTSTRAP_SERVERS`, `ALERTS_TOPIC` and `ALERTS_GROUP` configure consumption. The HTTP port is `PORT` (default 8080).

`/healthz` reports process liveness without dependencies. `/readyz` pings PostgreSQL and, when Kafka is configured, asks the broker for alert-topic metadata with a bounded timeout. `/metrics` refreshes those checks and exports `sentinel_service_ready` and `sentinel_dependency_ready{dependency}`. No dependency error details or credentials are returned.

Run `TEST_DATABASE_URL=... go test -race -count=1 ./...` against a disposable PostgreSQL database. Tests create and remove an isolated database, and require a role allowed to create databases. They cover the existing auth/idempotency/dataloader behavior, readiness transitions, independent liveness, failed-persistence offset retention, W3C extraction and a real PostgreSQL child span.

## Metrics and traces

| Metric | Meaning |
|---|---|
| `sentinel_consumer_messages_total` | Fetched Kafka records, including repeated alert IDs |
| `sentinel_consumer_duplicates_total` | Duplicate alert IDs suppressed by the database |
| `sentinel_consumer_persistence_errors_total` | Failed persistence attempts that retained their offset |
| `sentinel_db_transaction_seconds{operation="ingest",outcome}` | Ingestion transaction wall time through commit or rollback, including failed attempts |
| `sentinel_consumer_committed_offset{topic,partition}` | Consumer-group committed next offset |
| `sentinel_consumer_end_offset{topic,partition}` | Broker partition end next offset |
| `sentinel_consumer_lag{topic,partition}` | Nonnegative end minus committed offset |
| `sentinel_consumer_offsets_up` | Whether the last broker offset poll succeeded |
| `sentinel_consumer_offsets_observed_timestamp_seconds` | Last successful offset poll timestamp |

The consumer polls broker metadata, `OffsetFetch` and `ListOffsets` while processing or retrying a record. It does not use `Reader.Lag()`, which is [unavailable for a group reader](https://pkg.go.dev/github.com/segmentio/kafka-go#hdr-Consumer_Groups). A never-committed group starts at the retained partition's first offset. Offset metrics have only configured topic and broker partition labels.

`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://jaeger:4318/v1/traces` enables OTLP HTTP/protobuf export. `OTEL_SERVICE_NAME` defaults to `sentinel-console`. `kafka.consume` extracts W3C context from Kafka headers; `postgres.transaction` is its database child. Older v1 records without W3C headers still ingest and start a fresh trace. The original envelope `trace_id` remains a correlation field.

## Limitations

Broker offset polling is sampled, so lag can briefly trail a commit; `offsets_up` and the observation timestamp expose polling failures. The transaction histogram measures ingestion, rather than every GraphQL query. Dependency probes describe current connectivity, while offset commit remains gated on the actual transaction. The ops deployment uses local/CI kind and single-node services.
