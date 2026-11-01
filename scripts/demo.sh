#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .runtime/data .runtime/results/integration
chmod 777 .runtime/data .runtime/results .runtime/results/integration
files=(-f compose.yml)
if [ "${HOST_NETWORK:-0}" = 1 ]; then files+=(-f compose.hostnet.yml); fi
docker compose "${files[@]}" run --rm demo
python3 scripts/collect_run_results.py
