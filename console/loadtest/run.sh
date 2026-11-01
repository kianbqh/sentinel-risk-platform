#!/usr/bin/env bash
# Authenticated GraphQL read benchmark with hey (go install github.com/rakyll/hey@latest).
# Usage: loadtest/run.sh [duration] [concurrency]   (stack must be up; seeds 50 applications for a fresh user)
set -euo pipefail
cd "$(dirname "$0")/.."
export URL=${URL:-http://localhost:8080/query}
DUR=${1:-30s}; C=${2:-50}; HEY=${HEY:-hey}
SCRATCH=${BENCH_SCRATCH:-$PWD/.tmp/compose}
mkdir -p "$SCRATCH"
python3 loadtest/graphql_probe.py health
TOK=$(python3 loadtest/graphql_probe.py seed)
mkdir -p results
"$HEY" -z "$DUR" -c "$C" -m POST -T application/json -H "Authorization: Bearer $TOK" -D loadtest/list_query.json "$URL" | tee results/hey_list_query_docker.txt \
  | grep -E "Requests/sec|50%|95%|99%|\[[0-9]{3}\][[:space:]]+[0-9]+ responses"
# write path: createApplication (INSERT application + status event in one transaction)
python3 loadtest/graphql_probe.py verify "$TOK"
python3 loadtest/graphql_probe.py create-body "$SCRATCH/create.json" "$TOK"
"$HEY" -z 15s -c "$C" -m POST -T application/json -H "Authorization: Bearer $TOK" -D "$SCRATCH/create.json" "$URL" | tee results/hey_create_mutation.txt \
  | grep -E "Requests/sec|50%|95%|99%|\[[0-9]{3}\][[:space:]]+[0-9]+ responses"
python3 loadtest/graphql_probe.py verify "$TOK"
