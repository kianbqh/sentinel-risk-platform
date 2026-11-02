# Database outage exercise

Run this only against the isolated `sentinel-m1b-*` Compose project. The runner checks container labels before stopping Postgres, and attempts to restore that same service even if a check fails. The original default demo and unrelated projects must be left alone.

1. Start the ops project using the commands in [README.md](README.md), then run `verify.py smoke`, `spark` and `trace` into a new output directory. Confirm all scrape targets are up and the initial consumer backlog is empty.
2. Set the same `COMPOSE_PROJECT_NAME`, `OPS_ENV_FILE` and `HOST_NETWORK` values used at startup. Run `SENTINEL_OUTPUT_DIR=results/ops/<new-run> bash ops/sentinelctl.sh drill db-outage`.
3. The expected symptoms are gateway/backend `/readyz` returning 503 while `/healthz` stays 200, a positive Kafka backlog, unchanged committed next offsets, and a real `SentinelServiceNotReady` firing alert in Prometheus. The runner records every offset/alert sample. Gateway inference can still publish while readiness prevents new orchestrated traffic from being routed to it.
4. The runner starts Postgres, waits for the consumer to commit the entire captured high-water mark, and verifies one case and one ingestion history event per unique alert ID. The deliberately repeated record must not create a second case. Readiness must recover.
5. Inspect the timeline and raw samples in `drill-db-outage.json`. Do not infer no data loss from a lag metric alone: verify the final Kafka offset and SQL counts. A failed or unfinished run is retained as failed evidence; choose a new output directory after fixing the cause.

If the runner is forcibly killed, restore only its project:

```bash
docker compose --env-file .runtime/ops/.env --project-name sentinel-m1b-ops \
  -f compose.yml -f ops/compose.ops.yml --profile ops start postgres
# Add -f compose.hostnet.yml -f ops/compose.ops.hostnet.yml before --profile
# if HOST_NETWORK=1 was used at startup.
```

For kind rollout problems, `bash ops/sentinelctl.sh status` selects the configured local context and namespace. `bash ops/sentinelctl.sh rollback` asks Helm for the previous revision and waits for readiness. Inspect `kubectl --context kind-sentinel-m1b -n sentinel-m1b get pods,events` and the owned pod logs before retrying. Do not change the existing `deploy/k8s/` recipes or claim an AWS deployment from these checks.
