#!/usr/bin/env bash
# Keep requests flowing through the Service while replacing API replicas.
set -euo pipefail
cd "$(dirname "$0")/.."
DUR=${1:-90s}; C=${2:-20}; OUT=${3:-results/k8s_hey_rolling_restart.txt}
mkdir -p "$(dirname "$OUT")"
kubectl -n fraud delete job hey-load --ignore-not-found >/dev/null
sed -e "s/DURATION/$DUR/" -e "s/CONCURRENCY/$C/" -e 's/DISABLE_KEEPALIVE/false/' loadtest/hey-job.yaml | kubectl apply -f - >/dev/null
kubectl -n fraud wait --for=jsonpath='{.status.active}'=1 job/hey-load --timeout=60s
kubectl -n fraud wait --for=condition=ready pod -l job-name=hey-load --timeout=60s
sleep 10
date -u +%FT%TZ > results/rollout_status.txt
kubectl -n fraud rollout restart deploy/fraud-api >> results/rollout_status.txt
kubectl -n fraud rollout status deploy/fraud-api --timeout=180s >> results/rollout_status.txt
date -u +%FT%TZ >> results/rollout_status.txt
ACTIVE=$(kubectl -n fraud get job hey-load -o jsonpath='{.status.active}')
echo "Load job active at rollout completion: ${ACTIVE:-0}" >> results/rollout_status.txt
if [[ "$ACTIVE" != "1" ]]; then
  echo "Load finished before the rollout; rerun with a longer duration." >&2
  exit 1
fi
kubectl -n fraud wait --for=condition=complete job/hey-load --timeout=600s
kubectl -n fraud logs job/hey-load > "$OUT"
kubectl -n fraud get pods,hpa -o wide >> results/rollout_status.txt
cat "$OUT"
