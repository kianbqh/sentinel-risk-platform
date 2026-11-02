# Local operations and failure drills

I wanted to inspect the same request at the API, Kafka and database boundaries, then check what happens when the database disappears. This directory adds optional Prometheus, Grafana and Jaeger services to the existing Compose flow, plus a Helm lifecycle exercise on single-node kind.

## Start the optional services

The default `bash scripts/up.sh` demo keeps its original services and behavior. The ops profile is a separate, explicitly named Compose project with its own credentials and volumes. Its service ports match the original demo, so stop an earlier Sentinel demo before starting this project. Other Docker applications can remain running.

```bash
# Linux or WSL2 with a reachable Docker engine, from the repository root.
export COMPOSE_PROJECT_NAME=sentinel-m1b-ops
OPS_BUILD=1 bash ops/scripts/up.sh
```

The complete bridge-network command used by that script is:

```bash
docker compose --env-file .runtime/ops/.env --project-name sentinel-m1b-ops \
  -f compose.yml -f ops/compose.ops.yml --profile ops up -d --wait --wait-timeout 300
```

For an existing Linux engine configured without a bridge, use `HOST_NETWORK=1`. The equivalent command includes both final overrides:

```bash
HOST_NETWORK=1 OPS_BUILD=1 bash ops/scripts/up.sh
# Equivalent after the build and credential generation:
docker compose --env-file .runtime/ops/.env --project-name sentinel-m1b-ops \
  -f compose.yml -f ops/compose.ops.yml -f compose.hostnet.yml \
  -f ops/compose.ops.hostnet.yml --profile ops up -d --wait --wait-timeout 300
```

The generated `.runtime/ops/.env` is ignored. It never replaces the original `.env`. Prometheus is at `http://localhost:19090`, Grafana at `http://localhost:13041` (user `sentinel`, generated `GRAFANA_PASSWORD`), and Jaeger at `http://localhost:16686`. Ports are bound to loopback. Jaeger's in-memory data disappears when it restarts.

The recorded local run connected directly from WSL to Docker Desktop's socket. In that setup, Desktop saw the WSL `/mnt/g` bind paths as empty directories. The [recorded adapter](../results/ops/local/compose.desktop-bind.yml) maps the same G-drive files through `/run/desktop/mnt/host/g`; [mount evidence](../results/ops/local/runtime-mounts.json) retains their hashes. After building the images and generating `.runtime/ops/.env`, the exact local startup was:

```bash
docker compose --env-file .runtime/ops/.env --project-name sentinel-m1b-ops \
  -f compose.yml -f ops/compose.ops.yml \
  -f results/ops/local/compose.desktop-bind.yml --profile ops \
  up -d --wait --wait-timeout 300
```

That adapter records this checkout's absolute G-drive location. For another checkout, use its actual shared host path or Docker Desktop's normal WSL integration. The [local config](../results/ops/local/config.json) records the startup command and measured endpoints.

Gateway and Go readiness check the configured database and Kafka; gateway readiness also checks its loaded models. Liveness has no dependency checks. Prometheus scrapes both APIs and Spark every five seconds. Spark uses an HTTP endpoint on port 9108, enabled only by `STREAM_METRICS_ENABLED=true`; it does not use a Pushgateway. Consumer lag comes from broker end offsets minus the group's committed next offsets. Alert thresholds are local exercise defaults in [rules.yml](prometheus/rules.yml).

## Reproduce checks and evidence

All runners read [config.json](config.json), preserve exact commands and environment details, and refuse to overwrite a recorded JSON. The service/drill and kind runners reserve an attempt before side effects, flush raw observations to a sibling `.events.jsonl`, and retain those observations in failed reports. Use a new output directory for another run. A WSL installation without Git can pass `SENTINEL_GIT_SHA` and `SENTINEL_GIT_DIRTY` from a native Git check; the result identifies that source explicitly.

```bash
python3 ops/scripts/install_tools.py --directory "$PWD/.runtime/ops/tools"
export PATH="$PWD/.runtime/ops/tools:$PATH"
python3 ops/static_check.py --output results/ops/local/static.json
python3 ops/verify.py smoke --output results/ops/local/smoke.json
python3 ops/verify.py spark --output results/ops/local/spark.json
python3 ops/verify.py trace --output results/ops/local/trace.json
HOST_NETWORK=1 bash ops/sentinelctl.sh drill db-outage # omit HOST_NETWORK for bridge networking
```

The Spark check sends a bounded real prefix from the bundled, attributed [OpenML fixture](../gateway/fixtures/README.md) and reads actual listener metrics. It requires new input/scoring counter deltas to match this invocation's input and flush marker, so prior traffic cannot satisfy the gate. It is an instrumentation check, not a new accuracy measurement. Trace and outage checks use a model-directed **synthetic** feature vector, require the real gateway to publish the prediction, and inspect the resulting Kafka/database work. The scorer-stalled rule uses a separate activity timestamp refreshed by progress and legitimate idle events; an idle query is not treated as stalled.

The trace check queries Jaeger's HTTP API and requires a parent path `gateway.request → kafka.publish → kafka.consume → postgres.transaction` in one trace across both service names. Merely finding four span names is insufficient. The outage check stops only the PostgreSQL container whose Compose labels match this project. It checks readiness and liveness, polls the real Prometheus alerts API, reads Kafka offsets without joining the group, restores Postgres in a `finally` block, and reconciles alerts, cases and ingestion history after the backlog drains. It deliberately includes a repeated alert ID.

## kind and Helm

```bash
# Uses an isolated kubeconfig; builds and loads local images into sentinel-m1b.
bash ops/scripts/kind.sh
# Keep the owned cluster for inspection:
OPS_KIND_KEEP=1 bash ops/scripts/kind.sh
bash ops/sentinelctl.sh status
bash ops/sentinelctl.sh rollout m1b-b
bash ops/sentinelctl.sh rollback
```

The runner installs the [chart](../deploy/helm/sentinel/README.md), warms an in-cluster HTTP scoring loop, upgrades the image tags under concurrent load, rolls back to revision one, then deletes a gateway pod and observes a replacement UID become Ready. Both image tags intentionally refer to the same source build: this tests deployment mechanics, not a functional application change. Every timed HTTP response is parsed and checked; raw samples, failed request counts and percentile protocol accompany the result. Cluster deletion is scoped to the configured `sentinel-m1b*` name and can be disabled with `OPS_KIND_KEEP=1`.

## Results and protocol

New measurements live under [`results/ops/`](../results/ops/). The [module report](../results/M1B_DELIVERY_REPORT.md) links the accepted runs and exact values. Local JSON identifies WSL2 and its Docker engine; CI JSON identifies the GitHub-hosted runner. Tool downloads retain official URLs and actual SHA-256 hashes in the ignored tools directory. Existing model, load, security and integration results are preserved.

The HTTP load timer includes request creation, HTTP transport, response read and body validation; warmups and per-worker sleeps are excluded. Every sample keeps wall-clock start/end timestamps, and the runner reports requests whose intervals actually overlap the observed upgrade interval. Rollout and rollback timers include the Helm operation through its `--wait` readiness gate. Pod recovery covers the delete API call through a replacement UID becoming Ready, polled every 250 ms. Database alert time includes the stop command, readiness observation, bounded publication and Prometheus scrape/evaluation delay; recovery time starts after the database start command returns.

## Limitations

This is a local/CI single-node kind exercise, not EKS, GKE, AKS or a managed production cluster. The chart's Postgres and optional Redpanda are single instances; Kafka uses ephemeral storage, and Postgres persistence is optional. The chart does not install Spark or the monitoring stack. No HPA scaling behavior, cloud deployment, production traffic or production SLO is claimed. Jaeger's HTTP query API is internal and pinned to the exercised version. The synthetic ops input does not measure model quality.

References: [Prometheus rule tests](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/), [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/), [Jaeger OTLP and query APIs](https://www.jaegertracing.io/docs/1.76/architecture/apis/), [Kubernetes rolling updates](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/), [kind configuration](https://kind.sigs.k8s.io/docs/user/configuration/).
