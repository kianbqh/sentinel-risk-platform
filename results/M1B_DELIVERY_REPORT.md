# M1-B: observing requests and testing recovery

I added an optional operations setup so I could follow a prediction through Kafka into a case, then test database failures and application updates. Prometheus, Grafana and Jaeger run alongside the existing demo. A separate Helm exercise runs on single-node kind.

## Source and checks

- [PR #1](https://github.com/kianbqh/sentinel-risk-platform/pull/1) contains the implementation and evidence; its [checks page](https://github.com/kianbqh/sentinel-risk-platform/pull/1/checks) tracks the latest revision.
- The measured implementation is [d2a66ce](https://github.com/kianbqh/sentinel-risk-platform/commit/d2a66ce38d6e1d7caf343924e2a0295632ecee7f). Its [CI run](https://github.com/kianbqh/sentinel-risk-platform/actions/runs/41399450431) passed all jobs, including the original tests, service scans and integration flow.
- Three additive jobs cover ops validation, instrumented Compose integration and kind lifecycle checks. Existing jobs and earlier result files remain unchanged.
- This module adds no model weights or release assets. [CI artifact receipts](ops/ci-d2a66ce38d6e1d7caf343924e2a0295632ecee7f/artifact-receipt.json) record the downloaded archive digests and unchanged file hashes.

## Environment and reproduction

Local checks ran in WSL2 `CodexResearchATTAlf01`, with services in Docker Desktop `29.3.1`, on an Intel Core i7-12700KF. The recorded environment includes the RTX 3080 and driver information; these operations checks did not use the GPU. CI used GitHub-hosted Linux and single-node kind. Each linked result records its own CPU, versions, exact command, source/config hashes, timestamp and timer boundaries.

The local application images were built at `67b2cb9` and tested with the deployment configuration at `d2a66ce`. [Build provenance and image IDs](ops/local/app-build-provenance.json) and [component equivalence](ops/local/app-source-proof.json) show that the application and runner sources were unchanged between those commits. The dirty-worktree flag reflects new evidence files; the recorded tracked and staged source diffs were empty.

See the [ops commands and measurement protocol](../ops/README.md), [database runbook](../ops/RUNBOOK.md), [chart](../deploy/helm/sentinel/README.md), and local [configuration](ops/local/config.json). The local Desktop socket required an explicit [G-drive bind adapter](ops/local/compose.desktop-bind.yml); [actual mounts](ops/local/runtime-mounts.json) record the verified paths. The original data volumes and generated credentials were retained.

## Metrics, traces and database recovery

| Measurement | Local WSL2 / Desktop | GitHub-hosted CI |
|---|---:|---:|
| Metric endpoints scraped | 3 | 3 |
| Provisioned Grafana panels checked through its authenticated API | 8 | 8 |
| Alert-rule test groups / assertions passed | 9 / 15 | 9 / 15 |
| Spans in the tested cross-service trace | 4 | 4 |
| Spark input counter increase, including flush marker | 101.0 | 101.0 |
| Spark successful scoring counter increase | 100.0 | 100.0 |
| Outage drill unique messages / Kafka records | 20 / 21 | 20 / 21 |
| Time to observed firing alert, seconds | 32.48267450698768 | 18.512968724000018 |
| Backlog drain after database start returned, seconds | 5.228761434002081 | 2.3013265689999685 |
| Duplicate cases after recovery | 0 | 0 |
| Final alerts / cases / ingestion events | 20 / 20 / 20 | 20 / 20 / 20 |

Local evidence: [static](ops/local/static.json), [scrapes and Grafana](ops/local/smoke.json), [Spark](ops/local/spark.json), [trace](ops/local/trace.json), [outage timeline and raw offsets](ops/local/drill-db-outage.json). CI evidence: [static](ops/ci-d2a66ce38d6e1d7caf343924e2a0295632ecee7f/static.json), [scrapes](ops/ci-d2a66ce38d6e1d7caf343924e2a0295632ecee7f/smoke.json), [Spark](ops/ci-d2a66ce38d6e1d7caf343924e2a0295632ecee7f/spark.json), [trace](ops/ci-d2a66ce38d6e1d7caf343924e2a0295632ecee7f/trace.json), [outage](ops/ci-d2a66ce38d6e1d7caf343924e2a0295632ecee7f/drill-db-outage.json).

The outage checks observed readiness become false while liveness remained available. Committed offsets stayed at `1` throughout the recorded outage samples and reached `22`, matching the broker end offset, after restoration. The deliberately repeated alert ID produced one case. The alert timer includes the stop command, readiness check, publication and scrape/evaluation delay; it is not just Prometheus rule evaluation time.

The trace has the verified parent path `gateway.request → kafka.publish → kafka.consume → postgres.transaction` across the gateway and Go service. The test injects an external parent that it does not export, so Jaeger retains an expected missing-parent/clock-skew warning for that external boundary. The four exported spans have matching trace IDs and valid parent relationships.

Spark uses an attributed prefix from the [real OpenML card fixture](../gateway/fixtures/README.md). This checks instrumentation and counter deltas, not new model accuracy. Trace and outage inputs are explicitly synthetic model-directed feature vectors.

## Rolling updates, rollback and pod recovery

| Measurement | Local WSL2 / Desktop | GitHub-hosted CI |
|---|---:|---:|
| Measured requests | 10919 | 2815 |
| Failed requests | 0 | 0 |
| Requests overlapping upgrade | 9943 | 2561 |
| Failed overlapping requests | 0 | 0 |
| Rollout duration, seconds | 58.42751015900285 | 17.007745620999998 |
| Rollback succeeded | true | true |
| Rollback duration, seconds | 53.89692004499375 | 16.972280962000013 |
| Replacement pod Ready, seconds | 13.385384453009465 | 6.575219242000003 |
| Request p50, seconds | 0.002987818996189162 | 0.0044268480000084764 |
| Request p95, seconds | 0.004769770900020376 | 0.014127863600006646 |
| Request p99, seconds | 0.005909015428333077 | 0.02524575563998674 |

The [local run](ops/local/kind-lifecycle.json) and [CI run](ops/ci-d2a66ce38d6e1d7caf343924e2a0295632ecee7f/kind-lifecycle.json) retain raw requests, deployment observations and readiness/endpoint snapshots. The local update took longer; it is reported separately from CI. Both backend replicas in each fresh install completed their database/Kafka init checks and had application restart count `0`.

The measured HTTP loop uses concurrency `4`, excludes `10` warmups, validates every response body and retains every sample. Latency includes request creation, transport, response read and validation; per-worker sleeps are excluded. Percentiles use linear interpolation. Upgrade-overlap counts compare each request's actual start/end interval with the observed Helm upgrade interval.

Rollout and rollback timers include Helm through its readiness gate. Recovery starts at the pod-delete API call and ends when a replacement UID is observed Ready. Both image tags reference the same application build: this tests deployment mechanics. Ready EndpointSlice targets must match non-deleting Ready pods and the observed Deployment revision.

## What failed along the way

- Before the termination-drain change, [CI recorded](ops/ci-67b2cb9c341c18722412cb7c32df626313bf9a9a/kind-lifecycle.json) `36` failed requests out of `2850`; all failures overlapped the update. After the change, a [separate run](ops/ci-5a6147b5b0bc3bf8786ff78b55c1c0f9082a56f7/kind-lifecycle.json) recorded `0` failures out of `2827`, including `2492` overlapping requests. The current implementation's run is listed above. This is an observed result in bounded runs, not a production availability guarantee.
- The [first kind run](ops/history/ci-880275a/kind/ci-880275a83a6541d3ba3cb3cfe5d10dac7621a2db/kind-lifecycle.json) passed Helm upgrade but failed an assertion about an old image tag. That run lacks the exact pre-assert endpoint snapshot, so a terminating old pod is an inference. The later check records and validates actual serving endpoints.
- The [first security gate](ops/history/ci-880275a/security/trivy-summary.json) failed on fixable Go dependencies. Updating them preserved the existing gate; the [current scan](ops/ci-d2a66ce38d6e1d7caf343924e2a0295632ecee7f/security/trivy-summary.json) passed.
- The [initial local backend start](ops/local/startup-attempt-67b2cb9/diagnostics.json) failed while PostgreSQL's temporary Unix-socket initialization server had been mistaken for readiness. TCP probes and a backend database init gate fixed that startup sequence. Both backend replicas in the current CI install completed their init containers and had application restart count `0`.
- The local Desktop socket initially exposed empty directories for WSL bind paths, and an earlier backend container lacked its requested host-port mapping. The [retained diagnostics](ops/local/startup-attempt-d2a66ce-runtime/diagnostics.json), recorded bind adapter and recreation of that owned backend document the failure and restored access. No global firewall or port-reservation setting changed.

## Component validation

The local [Python suite](ops/local/service-tests/python-component-acceptance.json) passed `87` tests and `18` subtests, including the actual model load/unload/reload probe. Its original broad wrapper is still recorded as [failed](ops/local/service-tests/summary.json): an unrelated deployment runner changed concurrently. The supplemental record accepts only the byte-identical tested Python sources and preserves that qualification.

The full [Scala suite](ops/local/scala/provenance.json) passed `18` tests. Its [listener fixture](ops/local/scala/scala-metrics-smoke.json) recorded `8` metric families, `34` samples, `49` input rows including the flush marker, `48` scores and `2` batches, with `0` probability or threshold mismatches. The [Go suite](ops/local/service-tests/go-67b2cb9c341c/summary.json) passed `20` top-level tests with `-race` and real PostgreSQL. The current CI also reran the existing component suites successfully.

## Scope and supported claims

This supports describing instrumented FastAPI/Go/Spark services, W3C trace propagation through Kafka, dependency-aware readiness, verified offset/idempotency recovery, and Helm update/rollback exercises on local and CI kind.

It remains a single-node exercise with single-instance PostgreSQL and optional Kafka-compatible Redpanda. The chart does not install Spark or the monitoring stack, and no autoscaling behavior was measured. No managed-cloud deployment, production users, production SLO, EKS/GKE/AKS or AWS deployment is claimed. AWS Terraform remains validation-only. There are no required user-only setup steps left for this module; cloud deployment and autoscaling were outside its scope.

The [cleanup record](ops/local/cleanup.json) confirms that this exercise's Compose services were stopped and its kind node was removed, while its data volumes were retained.

## Existing results are unchanged

The [frozen manifest](../ops/frozen-baseline.json) contains the exact pre-module paths and hashes. [The audit](ops/local/frozen-final.json) verifies their bytes, the original workflow jobs and the empty diff below. Reproduce the requested diff from the repository root:

```bash
python3 - <<'PY'
import json, subprocess
baseline = json.load(open('ops/frozen-baseline.json'))
paths = [row['path'] for row in baseline['result_files']]
subprocess.run(['git', 'diff', '--stat', baseline['start_sha'], '--', *paths], check=True)
PY
```

Output: empty. Exit code: `0`. The audit retains the expanded Git command, exact old-path list and hash results.
