# Stateful transaction scoring

Scala and Spark Structured Streaming replay the OpenML1597 credit-card transactions through Kafka. The source contains284,807 rows; deduplication leaves283,726. The integrated demo uses the first10,000 rows in the stable Time,V1…V28,Amount order.

The scorer uses the same thirty-four features and exported logistic-regression coefficients as the batch/API model. The dataset has no card/account IDs, so the inclusive trailing-hour window describes the global stream. All transactions at one timestamp share a window; the newest timestamp is held until a later timestamp or the explicit flush marker arrives. Input must follow the replay's time order.

The platform sets `INPUT_TOPIC=sentinel.transactions.v1`, `SCORES_TOPIC=sentinel.scores.v1`, `ALERTS_TOPIC=sentinel.alerts.v1` and `ALERT_FORMAT=sentinel-v1`. Scores retain evaluation labels for the replay check; canonical analyst alerts exclude ground-truth labels and include the model-file SHA256. The existing standalone scored-alert format remains available when the platform variables are absent.

The original tests check state eviction, timestamp ties, random microbatch boundaries, Spark batch parity and474 real sklearn probability fixtures. `sbt -batch test` runs them. The Dockerfile builds the application with Java17 and sbt1.13.1, whose release archive checksum is pinned.

Root `scripts/demo.sh` runs the actual Kafka→Spark→alerts→console chain. Every returned score is compared with the Python model on the same inclusive-window features. Raw records and reconciliation are in `../results/integration/`; Spark progress is in `../results/streaming/`. Kafka writes are at least once; the consumer's database transaction de-duplicates alert IDs before committing its offset.

## Optional Prometheus endpoint

The stream command can expose a pull endpoint at `/metrics`. It uses the JDK HTTP server and Prometheus text format; it does not use Pushgateway or require another JVM dependency. Enable it in the ops profile with:

```bash
export STREAM_METRICS_ENABLED=true
export STREAM_METRICS_HOST=0.0.0.0
export STREAM_METRICS_PORT=9108
# Run the existing stream command with its usual Kafka/model/checkpoint options.
curl --fail http://localhost:9108/metrics
```

`STREAM_METRICS_ENABLED` defaults to `false`. With that default, the scorer opens no metrics socket and does not collect per-row timings. The endpoint runs in the Spark driver's process and closes when the stream command exits. Expose the port only on the trusted ops network; the endpoint has no authentication.

| Metric | Type | Meaning |
|---|---|---|
| `sentinel_spark_batch_duration_seconds` | Histogram | Spark `triggerExecution` wall time, including state processing and sinks |
| `sentinel_spark_batch_input_rows` | Gauge | Input rows in the last completed batch |
| `sentinel_spark_input_rows_total` | Counter | Input rows across distinct completed batch progress events |
| `sentinel_spark_batches_total` | Counter | Distinct completed batch progress events |
| `sentinel_spark_scoring_duration_seconds` | Histogram | Actual executor time for each successful feature-vector/model scoring call |
| `sentinel_spark_last_progress_timestamp_seconds` | Gauge | Driver wall clock when the last distinct batch progress event arrived |
| `sentinel_spark_last_activity_timestamp_seconds` | Gauge | Driver wall clock at monitored query start, any progress event or idle callback |
| `sentinel_spark_query_active` | Gauge | Whether the monitored query is active |

A `StreamingQueryListener` takes batch durations and row counts directly from Spark progress and snapshots the executor scoring accumulator. The scoring timer surrounds feature-vector construction, logistic-regression evaluation and score-record construction. It excludes window aggregation, executor scheduling and Kafka writes. Input row counts include the replay flush marker; scoring observations exclude it. Histograms have fixed buckets and only the `le` label. There are no transaction, entity, query-run or batch labels.

The activity timestamp starts when the monitored query starts and refreshes on progress (including repeated batch IDs) and Spark idle callbacks. It distinguishes a healthy query waiting for input from a stalled query. Idle activity does not increment batch/row counters or change the last distinct progress timestamp. Stalled-query alerts should gate on `sentinel_spark_query_active` and use the activity timestamp rather than the distinct progress timestamp.

## Metrics check

Run the full Scala suite with `sbt -batch test`. A small synthetic `MemoryStream` test exercises the real stateful scorer, listener and HTTP endpoint without Kafka. It checks raw Spark progress against the exported batch metrics, exact probabilities and threshold decisions against the untimed scorer, and endpoint lifecycle/configuration. The fixture is driven by [`metrics-smoke.json`](metrics-smoke.json).

To retain the actual progress, scrape and machine-readable smoke result, choose a new evidence directory:

```bash
export STREAM_METRICS_ENVIRONMENT='WSL2 Linux, local[2]'
export STREAM_METRICS_GIT_SHA='<source commit>'
bash scripts/test_metrics.sh /path/on/G/new-scala-evidence
```

The script saves `scala-tests.log`, `scala-progress.jsonl`, `scala-metrics.prom` and `scala-metrics-smoke.json`. The JSON records the source commit, per-file source hashes, config hash, runtime versions, CPU, exact command and wall time. `STREAM_METRICS_SOURCE_STATE` can describe any local edits. Add `--metrics-only` after the directory to run just the metrics suite. The check is a correctness smoke test with no warm-up and one run; it is not a latency benchmark.

The local ops evidence retains the [smoke summary](../results/ops/local/scala-metrics-smoke.json), [actual scrape](../results/ops/local/scala-metrics.prom) and [raw Spark progress](../results/ops/local/scala-progress.jsonl). `recentProgress` includes idle snapshots that can reuse the next real batch ID. The fixture's duration check selects its nonempty input batches; the exporter observes `onQueryProgress` events and does not count idle snapshots as completed batches.

## Limitations

The listener updates metrics after completed batches; an in-flight or stuck batch has no completion sample. The scoring histogram uses a Spark transformation accumulator, so task retries or recomputation can count a scoring call again. It measures observed calls rather than unique delivered transactions and resets on process restart. Per-row timing adds overhead when enabled. The smoke fixture verifies local Spark and HTTP behavior; it does not establish Kafka/Compose scraping, production throughput or cluster behavior.
