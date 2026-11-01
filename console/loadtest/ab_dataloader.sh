#!/usr/bin/env bash
# A/B the dataloader: alternate N+1 (DISABLE_DATALOADER=1) and batched runs, same binary, same data.
# Usage: loadtest/ab_dataloader.sh <server-binary> [rounds]
set -euo pipefail
cd "$(dirname "$0")/.."
BIN=$1; ROUNDS=${2:-3}
export URL=${URL:-http://localhost:8080/query}
SCRATCH=${BENCH_SCRATCH:-$PWD/.tmp/ab}
HEY=${HEY:-hey}
mkdir -p "$SCRATCH" results
SERVER_PID=
start() {
  DISABLE_DATALOADER=$1 BCRYPT_COST=10 "$BIN" >"$SCRATCH/server.log" 2>&1 &
  SERVER_PID=$!
  python3 loadtest/graphql_probe.py health
  kill -0 "$SERVER_PID"
}
stop() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    SERVER_PID=
  fi
}
trap stop EXIT
start 0
TOK=$(python3 loadtest/graphql_probe.py seed)
stop
mkdir -p results; : > results/ab_dataloader.tsv
for r in $(seq 1 "$ROUNDS"); do for mode in 1 0; do
  start $mode
  python3 loadtest/graphql_probe.py verify "$TOK"
  REPORT="results/ab_${mode}_round_${r}.txt"
  "$HEY" -z 20s -c 50 -m POST -T application/json -H "Authorization: Bearer $TOK" -D loadtest/list_query.json "$URL" > "$REPORT"
  python3 loadtest/graphql_probe.py verify "$TOK"
  ps -p "$SERVER_PID" -o pid,etime,%cpu,rss,args >> "$SCRATCH/cpu.log"
  stop
  rps=$(awk '/Requests\/sec/{print $2}' "$REPORT"); p50=$(awk '/50%/{print $3}' "$REPORT"); p95=$(awk '/95%/{print $3}' "$REPORT")
  ok=$(awk '$1 == "[200]" && $3 == "responses" {print $2}' "$REPORT")
  printf "round=%s\tdataloader=%s\trps=%s\tp50=%s\tp95=%s\tok=%s\n" "$r" "$([ "$mode" = 0 ] && echo on || echo off)" "$rps" "$p50" "$p95" "$ok" | tee -a results/ab_dataloader.tsv
done; done
