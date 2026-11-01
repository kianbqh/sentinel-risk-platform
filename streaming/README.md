# fraud-streaming-scala

[![Scala and replay tests](https://github.com/kianbqh/fraud-streaming-scala/actions/workflows/ci.yml/badge.svg)](https://github.com/kianbqh/fraud-streaming-scala/actions/workflows/ci.yml)

I ported the trailing-hour features from [stripe-fraud-ml](https://github.com/kianbqh/stripe-fraud-ml) to Scala to see where a streaming version would disagree with the batch calculation. The interesting edge case was tied timestamps: a row has to wait until its timestamp is complete before it can receive the same window statistics.

This project replays all 283,726 deduplicated credit-card transactions through Redpanda and Spark Structured Streaming. It computes trailing-hour count, mean and population standard deviation, scores each event with the exported logistic-regression model, and writes scores and threshold alerts back to Kafka. Every streamed transaction is then checked against a separate Scala batch reference.

```text
ordered replay → transactions topic → Spark state + window features + LR scorer
                                      ├─ scores topic
                                      └─ fraud-alerts topic
batch reference + Kafka outputs → row-by-row parity and latency report
```

## Results from the local run

Measured on 2026-10-31 with an Intel Core i7-12700KF under WSL2, Spark `local[4]`, and Redpanda v25.2.9 on the same host. The broker uses two scheduler cores and a 2 GiB process-memory setting; there is one Kafka partition per topic and one stateful key. Java is Temurin 17.0.21, Spark 4.3.0, Scala 2.13.18, and sbt 1.13.1. Full settings are in [`results/environment.json`](results/environment.json).

| Run | Input | Timestamp delay p50 | p95 | p99 | Spark input rows/s |
|---|---|---:|---:|---:|---:|
| live1k | 1,000 events/s | 2,057 ms | 3,144 ms | 5,540 ms | 1,001 |
| live5k | 5,000 events/s | 3,575 ms | 6,525 ms | 7,650 ms | 4,886 |
| live10k | 10,000 events/s | 7,316 ms | 9,633 ms | 10,063 ms | 9,072 |
| backlog20k | Preloaded, 20k input rows/batch | — | — | — | 4,990 |
| backlog | Preloaded, one input batch | — | — | — | 7,850 |

Every run produced 283,726 unique scores and 439 matching alerts, with **zero missing rows, extras, or duplicates**. All streamed window features, logits and probabilities were bit-identical to the Scala batch reference. The progress audit accounts for all 283,727 inputs per run, including the flush marker.

Timestamp delay rises with the offered rate in this configuration: p95 is 3.144 s at 1k/s and 9.633 s at 10k/s. The 10k replay's batch-time throughput is 9,072 input rows/s. Each run uses fresh topics/checkpoints and a separate JVM; live production waits for initial query progress, while backlog queries start with preloaded input.

`backlog20k` uses a second attempt after fixing a shutdown race that skipped the final progress-log entry. Its original score output was complete; the throughput above uses the complete rerun. [`results/attempts.json`](results/attempts.json) records the selection and hashes. The other four runs were retained.

The generated table is in [`results/summary.md`](results/summary.md), with raw JSON reports and `StreamingQueryProgress` logs alongside it. [`results/validation.json`](results/validation.json) records the full-run acceptance checks, and [`model/provenance.json`](model/provenance.json) ties the exported coefficients and real-row fixture to the source model and data hashes.

The latency fields measure the producer's event-creation timestamp to the Kafka score record's timestamp, using Kafka's default `CreateTime` behavior. That endpoint is the output producer's record timestamp. Spark throughput is input rows divided by the summed duration of nonempty micro-batches, including one flush marker per run. The raw `throughput_events_per_s` field uses score count divided by its output timestamp span. Backlog runs report drain throughput because their latency fields include preloaded queueing.

## Correctness

The current source model was freshly exported from the local `stripe-fraud-ml` run. The cross-language check covers all 283,726 rows with one-to-one joins:

| Check | Local result |
|---|---:|
| Scala batch vs PySpark trailing-hour count | 0 mismatches |
| Maximum absolute difference in amount z-score | 1.208e-13 |
| Maximum absolute difference in amount/mean ratio | 5.684e-14 |
| Scala probability vs sklearn on PySpark features | maximum difference 1.332e-14 |
| Alert decisions vs sklearn | 0 mismatches |

The full data produces 439 alert decisions, including 362 labeled frauds. In the source model's held-out time split, 59 of 56,744 transactions are flagged and 50 are true frauds: 84.7% precision and 67.6% recall across the 74 fraud cases. The configured threshold comes from that model's validation procedure.

All **11 ScalaTest tests** passed across three suites. They cover the 474-row sklearn fixture, sigmoid extremes, a brute-force window reference, tied timestamps, window eviction, independence from micro-batch cuts, Spark batch windows, a `MemoryStream` through the stateful operator, and JSON round trips. The replay pipeline also fails on incomplete counts, duplicate/missing/extra IDs, nonfinite or mismatched scores/features, inconsistent alert IDs, and asynchronous producer delivery errors.

## How the state works

The feature definition is a closed interval: `t - 3600 <= Time <= t`, including every transaction with the same timestamp. The state holds the newest timestamp until a later one arrives; an explicit end marker flushes the last group. Entries older than one hour are evicted. Sorting by event time and transaction ID makes the state update deterministic across micro-batch boundaries.

There is one global key because this dataset has no card or merchant identifier. Input is replayed in event-time order through one Kafka partition, so the stateful stage has one task. Late rows are counted in the state; this replay supplies ordered input. The model uses only its 34 numeric features. Labels travel with the payload so the final report can evaluate alerts.

`flatMapGroupsWithState` keeps the window in a checkpointed RocksDB state store. `foreachBatch` caches its output once and writes the scores and alerts topics. These Kafka writes have at-least-once delivery semantics; the reports explicitly check duplicates and exact alert-topic transaction IDs. The producer collects asynchronous callback errors and fails if any record was not delivered.

The scorer runs entirely on the JVM: standardize each feature, take the coefficient dot product, and apply a stable sigmoid. It uses the same means, scales, coefficients, intercept and validation threshold as the Python pipeline.

## Run it

Use Java 17, sbt 1.13.1, Docker with Compose, and Python 3 for the summary/validation scripts. The project pins Spark 4.3.0 and Scala 2.13.18. I run it under Linux through WSL2 on Windows.

```bash
# Raw OpenML 1597 data; the small exported model and parity fixture are included.
mkdir -p data
curl -fL --retry 3 -o data/creditcard.parquet https://data.openml.org/datasets/0000/1597/dataset_1597.pq

sbt -batch test
docker compose up -d --wait redpanda

scripts/run.sh prepare
scripts/run.sh batch-reference

RUN_ID=live1k     MODE=live    RATE=1000  scripts/experiment.sh
RUN_ID=live5k     MODE=live    RATE=5000  scripts/experiment.sh
RUN_ID=live10k    MODE=live    RATE=10000 scripts/experiment.sh
RUN_ID=backlog20k MODE=backlog MAX_OFFSETS=20000 scripts/experiment.sh
RUN_ID=backlog    MODE=backlog scripts/experiment.sh
python3 scripts/summarize.py
```

For the full PySpark/sklearn cross-check, first run the linked source project, then use its Python environment:

```bash
python scripts/export_model.py --repo ../stripe-fraud-ml --features ../stripe-fraud-ml/data/features/test
python scripts/verify_against_pyspark.py --repo ../stripe-fraud-ml --features ../stripe-fraud-ml/data/features
python scripts/validate_runs.py
```

The default broker address is `127.0.0.1:19092`; `KAFKA_BOOTSTRAP` overrides it. `SPARK_MASTER` defaults to `local[4]`. Each experiment needs a fresh run ID and topic set. Its checkpoint lives under `checkpoints/`, or a supplied `CHECKPOINT_ROOT`. The live script waits for stream progress before producing events.

For a Docker daemon using host networking without bridge/iptables, start the broker and run experiments with the override:

```bash
export COMPOSE_FILES='-f docker-compose.yml -f docker-compose.hostnet.yml'
docker compose $COMPOSE_FILES up -d --wait redpanda
```

`scripts/run.sh` resolves the runtime classpath with sbt, then invokes Java directly with Spark's Java 17 module flags. Source changes invalidate the saved classpath/compile step. GitHub Actions runs the 11 tests and a full 283,726-row Kafka backlog smoke replay, including coverage and finite-parity checks.
