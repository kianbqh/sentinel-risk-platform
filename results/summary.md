| run | input | events | e2e p50 ms | e2e p95 ms | e2e p99 ms | Spark rows/s (sum of batch time) | median batch ms | parity (max abs Δp, alert mismatches) |
|---|---|---|---|---|---|---|---|---|
| live1k | 1000 ev/s live | 283,726 | 2,057 | 3,144 | 5,540 | 1,001 | 1,422 | 0.0, 0 |
| live5k | 5000 ev/s live | 283,726 | 3,575 | 6,525 | 7,650 | 4,886 | 2,403 | 0.0, 0 |
| live10k | 10000 ev/s live | 283,726 | 7,316 | 9,633 | 10,063 | 9,072 | 4,938 | 0.0, 0 |
| backlog20k | backlog drain (20k/batch) | 283,726 | — | — | — | 4,990 | 2,194 | 0.0, 0 |
| backlog | backlog drain (1 batch) | 283,726 | — | — | — | 7,850 | 36,145 | 0.0, 0 |

Spark input rows/s = consumed rows / summed durations of nonempty micro-batches. Each run consumes 283,726 transactions plus one flush marker.
Live latency = producer event-creation timestamp to score-topic Kafka record timestamp (default CreateTime). Backlog runs measure drain throughput; their raw latency fields include preloaded queueing.

Selected attempts are recorded in attempts.json. backlog20k was rerun once after synchronizing final progress recording; its original scores were complete but its duration log lacked the last batch. The other four runs are unchanged.
