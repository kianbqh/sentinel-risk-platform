#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/verify_artifacts.py
if [ ! -f .env ]; then
  python3 - <<'PY'
from pathlib import Path
import secrets
Path('.env').write_text('POSTGRES_PASSWORD='+secrets.token_hex(24)+'\nJWT_SECRET='+secrets.token_hex(32)+'\n')
PY
  chmod 600 .env
fi
mkdir -p results/ato results/streaming
chmod 777 results/ato results/streaming
files=(-f compose.yml)
if [ "${HOST_NETWORK:-0}" = 1 ]; then files+=(-f compose.hostnet.yml); fi
network=()
if [ "${HOST_NETWORK:-0}" = 1 ]; then network+=(--network host); fi
# Build in sequence to keep memory and disk demand bounded on a local machine.
docker build "${network[@]}" -f gateway/Dockerfile -t sentinel-risk-gateway:local .
docker build "${network[@]}" -t sentinel-risk-console-api:local console/backend
docker build "${network[@]}" -t sentinel-risk-console:local console/frontend
if python3 scripts/stream_artifact.py check --inputs-only; then
  context=${STREAM_BUILD_CONTEXT:-$PWD/.runtime/stream-image}
  python3 scripts/stream_artifact.py package --destination "$context"
  docker build "${network[@]}" -t sentinel-risk-streaming:local "$context"
else
  docker build "${network[@]}" -f streaming/Dockerfile -t sentinel-risk-streaming:local .
fi
docker compose "${files[@]}" up -d --wait --wait-timeout 240
echo 'Analyst console: http://localhost:13040'
echo 'Risk gateway: http://localhost:18040/docs'
