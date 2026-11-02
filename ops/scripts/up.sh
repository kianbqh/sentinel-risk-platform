#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
export COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-sentinel-m1b-ops}
[[ $COMPOSE_PROJECT_NAME =~ ^sentinel-m1b-[a-z0-9-]+$ ]] || { echo 'Use a new sentinel-m1b-* project.' >&2; exit 2; }
export OPS_ENV_FILE=${OPS_ENV_FILE:-$PWD/.runtime/ops/.env}
python3 scripts/verify_artifacts.py
mkdir -p .runtime/ops/spark "$(dirname "$OPS_ENV_FILE")"
chmod 777 .runtime/ops/spark
if [[ ! -f $OPS_ENV_FILE ]]; then
  python3 - "$OPS_ENV_FILE" <<'PY'
from pathlib import Path
import secrets, sys
path = Path(sys.argv[1])
path.write_text("POSTGRES_PASSWORD=" + secrets.token_hex(24) + "\nJWT_SECRET=" + secrets.token_hex(32) + "\nGRAFANA_PASSWORD=" + secrets.token_hex(24) + "\n")
path.chmod(0o600)
PY
fi
files=(-f compose.yml -f ops/compose.ops.yml)
network=()
if [[ ${HOST_NETWORK:-0} == 1 ]]; then
  files+=(-f compose.hostnet.yml -f ops/compose.ops.hostnet.yml)
  network+=(--network host)
fi
if [[ ${OPS_BUILD:-0} == 1 ]]; then
  docker build "${network[@]}" -f gateway/Dockerfile -t sentinel-risk-gateway:local .
  docker build "${network[@]}" -t sentinel-risk-console-api:local console/backend
  docker build "${network[@]}" -t sentinel-risk-console:local console/frontend
  docker build "${network[@]}" -f streaming/Dockerfile -t sentinel-risk-streaming:local .
fi
docker compose --env-file "$OPS_ENV_FILE" --project-name "$COMPOSE_PROJECT_NAME" "${files[@]}" --profile ops \
  up -d --wait --wait-timeout 300
echo 'Ops project started. Next: bash ops/sentinelctl.sh smoke'
