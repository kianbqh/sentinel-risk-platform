# Sentinel Helm chart

This chart runs the transaction/SMS gateway, Go consumer/GraphQL API, React/nginx console and PostgreSQL on local/CI Kubernetes. It includes optional single-node Redpanda; set `kafka.enabled=false` and `kafka.bootstrapServers` to use a reachable external broker instead.

Create an ignored values file with `auth.postgresPassword` and a `auth.jwtSecret` of at least 32 bytes. Never put live credentials in Git or on a command line saved to public logs. The runner creates this file automatically.

```bash
helm lint deploy/helm/sentinel -f .runtime/ops/kind-secret-values.json
helm template m1b deploy/helm/sentinel -f .runtime/ops/kind-secret-values.json | kubeconform -strict -summary -kubernetes-version 1.34.0
helm install m1b deploy/helm/sentinel -n sentinel-m1b --create-namespace \
  -f .runtime/ops/kind-secret-values.json --wait --timeout 5m
# The full install/upgrade/rollback/recovery exercise:
bash ops/scripts/kind.sh
```

The three application Deployments default to two replicas, `maxUnavailable: 0`, one surge pod, resource requests/limits and a PDB retaining one replica. Gateway/backend probes use dependency-free `/healthz` and dependency-aware `/readyz`. Frontend probes use `/`. ConfigMap and Secret checksums trigger a rolling update when settings change. Postgres is a simple StatefulSet; enable `postgres.persistence.enabled` for a PVC. `tracing.endpoint` optionally supplies an existing OTLP HTTP traces endpoint.

The [ops report](../../../results/M1B_DELIVERY_REPORT.md) links measured results. The [ops protocol](../../../ops/README.md) explains timers and raw samples. The old `deploy/k8s/` files remain unchanged.

## Limitations

The accepted deployment is single-node kind on a desktop/CI runner. Redpanda storage is ephemeral and neither Postgres nor Kafka is highly available. This chart does not deploy Spark, Prometheus, Grafana or Jaeger. It does not claim managed-cloud deployment, autoscaling behavior, production traffic or a production availability SLO. Resource limits and PDB settings are examples exercised in this local scope.
