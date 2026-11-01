#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .runtime/data results/integration
chmod 777 .runtime/data results/integration
files=(-f compose.yml)
if [ "${HOST_NETWORK:-0}" = 1 ]; then files+=(-f compose.hostnet.yml); fi
docker compose "${files[@]}" run --rm demo
