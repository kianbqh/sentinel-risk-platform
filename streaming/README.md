# Stateful transaction scoring

Scala and Spark Structured Streaming replay the OpenML1597 credit-card transactions through Kafka. The source contains284,807 rows; deduplication leaves283,726. The integrated demo uses the first10,000 rows in the stable Time,V1…V28,Amount order.

The scorer uses the same thirty-four features and exported logistic-regression coefficients as the batch/API model. The dataset has no card/account IDs, so the inclusive trailing-hour window describes the global stream. All transactions at one timestamp share a window; the newest timestamp is held until a later timestamp or the explicit flush marker arrives. Input must follow the replay's time order.

The platform sets `INPUT_TOPIC=sentinel.transactions.v1`, `SCORES_TOPIC=sentinel.scores.v1`, `ALERTS_TOPIC=sentinel.alerts.v1` and `ALERT_FORMAT=sentinel-v1`. Scores retain evaluation labels for the replay check; canonical analyst alerts exclude ground-truth labels and include the model-file SHA256. The existing standalone scored-alert format remains available when the platform variables are absent.

The eleven original tests check state eviction, timestamp ties, random microbatch boundaries, Spark batch parity and474 real sklearn probability fixtures. `sbt -batch test` runs them. The Dockerfile builds the application with Java17 and sbt1.13.1, whose release archive checksum is pinned.

Root `scripts/demo.sh` runs the actual Kafka→Spark→alerts→console chain. Every returned score is compared with the Python model on the same inclusive-window features. Raw records and reconciliation are in `../results/integration/`; Spark progress is in `../results/streaming/`. Kafka writes are at least once; the consumer's database transaction de-duplicates alert IDs before committing its offset.
