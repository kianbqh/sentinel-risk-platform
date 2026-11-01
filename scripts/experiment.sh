#!/usr/bin/env bash
# One measured replay: create topics, start the streaming job, replay all transactions, report.
#   MODE=live    : stream starts first, producer replays at RATE events/s  -> end-to-end latency
#   MODE=backlog : producer writes everything first, then the stream drains it -> max throughput
# Usage: RUN_ID=live2k MODE=live RATE=2000 scripts/experiment.sh
set -euo pipefail
cd "$(dirname "$0")/.."
RUN_ID=${RUN_ID:-demo}; MODE=${MODE:-live}; RATE=${RATE:-2000}; MAX_OFFSETS=${MAX_OFFSETS:-0}
BOOT=${KAFKA_BOOTSTRAP:-127.0.0.1:19092}; export KAFKA_BOOTSTRAP=$BOOT
COMPOSE_FILES=${COMPOSE_FILES:--f docker-compose.yml}
RPK=(docker compose $COMPOSE_FILES exec -T redpanda rpk -X brokers=$BOOT)
EXPECTED=${EXPECTED:-283726}   # rows in data/replay.parquet
[[ $RUN_ID =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || { echo 'Invalid RUN_ID' >&2; exit 2; }
CHECKPOINT_ROOT=${CHECKPOINT_ROOT:-"$PWD/checkpoints"}
checkpoint_root=$(realpath -m "$CHECKPOINT_ROOT")
checkpoint=$(realpath -m "$checkpoint_root/$RUN_ID")
[[ $checkpoint == "$checkpoint_root/"* ]] || { echo 'Checkpoint outside configured root' >&2; exit 2; }
mkdir -p results
for t in transactions scores fraud-alerts; do "${RPK[@]}" topic create "$t-$RUN_ID" -p 1 >/dev/null; done
rm -rf -- "$checkpoint"
if [[ $MODE == live ]]; then
  rm -f "results/${RUN_ID}_progress.jsonl"
  scripts/run.sh stream --run-id "$RUN_ID" --expected "$EXPECTED" --checkpoint "$checkpoint" --idle-timeout-s 180 > "results/${RUN_ID}_stream.log" 2>&1 &
  SP=$!
  for _ in $(seq 1 720); do
    [[ -s "results/${RUN_ID}_progress.jsonl" ]] && break
    kill -0 "$SP" 2>/dev/null || { wait "$SP"; exit 1; }
    sleep 0.25
  done
  [[ -s "results/${RUN_ID}_progress.jsonl" ]] || { kill "$SP"; wait "$SP" || true; echo 'Stream readiness timeout' >&2; exit 1; }
  JVM_HEAP=2g scripts/run.sh produce --run-id "$RUN_ID" --rate "$RATE" 2>"results/${RUN_ID}_producer.log" | tail -1 | tee "results/${RUN_ID}_producer.json"
  wait $SP
else
  JVM_HEAP=2g scripts/run.sh produce --run-id "$RUN_ID" --rate 0 2>"results/${RUN_ID}_producer.log" | tail -1 | tee "results/${RUN_ID}_producer.json"
  scripts/run.sh stream --run-id "$RUN_ID" --expected "$EXPECTED" --checkpoint "$checkpoint" --max-offsets-per-trigger "$MAX_OFFSETS" --idle-timeout-s 180 > "results/${RUN_ID}_stream.log" 2>&1
fi
tail -1 "results/${RUN_ID}_stream.log"
scripts/run.sh report --run-id "$RUN_ID" --note-mode "$MODE" --note-target_rate "$RATE" 2>"results/${RUN_ID}_report.log" | tail -40
