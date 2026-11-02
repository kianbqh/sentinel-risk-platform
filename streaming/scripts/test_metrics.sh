#!/usr/bin/env bash
set -euo pipefail

# Keep evidence outside existing results paths. The caller supplies its new run directory.
export STREAM_METRICS_EVIDENCE_DIR="${1:?usage: bash scripts/test_metrics.sh <new-evidence-directory>}"
: "${STREAM_METRICS_GIT_SHA:?set STREAM_METRICS_GIT_SHA to the source commit; local edits are identified by source hashes}"
mkdir -p "$STREAM_METRICS_EVIDENCE_DIR"
export STREAM_METRICS_ENVIRONMENT="${STREAM_METRICS_ENVIRONMENT:-WSL2 Linux}"
case "${2:-}" in
  '') test_command=test ;;
  --metrics-only) test_command='testOnly fraudstream.StreamMetricsSpec' ;;
  *) echo 'unknown option: expected --metrics-only' >&2; exit 2 ;;
esac
export STREAM_METRICS_TEST_COMMAND="bash scripts/test_metrics.sh $STREAM_METRICS_EVIDENCE_DIR${2:+ $2}"
export STREAM_METRICS_CPU_MODEL="${STREAM_METRICS_CPU_MODEL:-$(awk -F ': ' '/^model name/ {print $2; exit}' /proc/cpuinfo)}"
sbt -batch "$test_command" 2>&1 | tee "$STREAM_METRICS_EVIDENCE_DIR/scala-tests.log"
