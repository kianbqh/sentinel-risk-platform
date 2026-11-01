#!/usr/bin/env bash
# Usage: scripts/k8s_loadtest.sh <duration> <concurrency> <outfile> [disable_keepalive: true|false]
set -euo pipefail
DUR=${1:-60s}; C=${2:-50}; OUT=${3:-results/k8s_hey.txt}
NO_KEEPALIVE=${4:-false}
[[ "$NO_KEEPALIVE" == "true" || "$NO_KEEPALIVE" == "false" ]] || { echo 'Fourth argument must be true or false' >&2; exit 2; }
cd "$(dirname "$0")/.."
mkdir -p "$(dirname "$OUT")"
kubectl -n fraud delete job hey-load --ignore-not-found >/dev/null
sed -e "s/DURATION/$DUR/" -e "s/CONCURRENCY/$C/" -e "s/DISABLE_KEEPALIVE/$NO_KEEPALIVE/" loadtest/hey-job.yaml | kubectl apply -f - >/dev/null
kubectl -n fraud wait --for=condition=complete job/hey-load --timeout=600s >/dev/null
kubectl -n fraud logs job/hey-load > "$OUT"
cat "$OUT"
