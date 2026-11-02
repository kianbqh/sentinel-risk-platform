#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
usage() {
  echo 'Usage: ops/sentinelctl.sh smoke | status | rollout <tag> | rollback | drill db-outage'
}
mode=${1:-help}
release=${SENTINEL_RELEASE:-m1b}
namespace=${SENTINEL_NAMESPACE:-sentinel-m1b}
context=${SENTINEL_KUBE_CONTEXT:-kind-sentinel-m1b}
output=${SENTINEL_OUTPUT_DIR:-results/ops/local}
case "$mode" in
  smoke)
    [[ $# == 1 ]] || { usage >&2; exit 2; }
    python3 ops/verify.py smoke --config "${SENTINEL_CONFIG:-ops/config.json}" --output "$output/smoke.json"
    ;;
  status)
    [[ $# == 1 ]] || { usage >&2; exit 2; }
    helm status "$release" --namespace "$namespace" --kube-context "$context"
    kubectl --context "$context" -n "$namespace" get deploy,statefulset,pods,pdb
    ;;
  rollout)
    [[ $# == 2 && $2 =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$ ]] || { usage >&2; exit 2; }
    helm upgrade "$release" deploy/helm/sentinel --namespace "$namespace" --kube-context "$context" \
      --reuse-values --set-string "images.gateway.tag=$2,images.backend.tag=$2,images.frontend.tag=$2" \
      --wait --timeout 5m
    ;;
  rollback)
    [[ $# == 1 ]] || { usage >&2; exit 2; }
    helm rollback "$release" 0 --namespace "$namespace" --kube-context "$context" --wait --timeout 5m
    ;;
  drill)
    [[ $# == 2 && $2 == db-outage ]] || { usage >&2; exit 2; }
    python3 ops/verify.py drill --config "${SENTINEL_CONFIG:-ops/config.json}" --output "$output/drill-db-outage.json"
    ;;
  help|--help|-h) usage ;;
  *) usage >&2; exit 2 ;;
esac
