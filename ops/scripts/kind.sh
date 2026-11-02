#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
config=${SENTINEL_CONFIG:-ops/config.json}
output=${SENTINEL_OUTPUT_DIR:-results/ops/local}
cluster=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["kind"]["cluster"])' "$config")
[[ $cluster == sentinel-m1b* ]] || { echo 'Use a sentinel-m1b* cluster.' >&2; exit 2; }
export KUBECONFIG=${KUBECONFIG:-$PWD/.runtime/ops/kubeconfig}
mkdir -p "$(dirname "$KUBECONFIG")" "$output"
node_image=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["kind"]["node_image"])' "$config")
tag_a=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["kind"]["initial_tag"])' "$config")
tag_b=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["kind"]["upgrade_tag"])' "$config")
if [[ ${OPS_KIND_BUILD:-1} == 1 ]]; then
  docker build -f gateway/Dockerfile -t "sentinel-risk-gateway:$tag_a" .
  docker build -t "sentinel-risk-console-api:$tag_a" console/backend
  docker build -t "sentinel-risk-console:$tag_a" console/frontend
fi
for image in sentinel-risk-gateway sentinel-risk-console-api sentinel-risk-console; do
  docker tag "$image:$tag_a" "$image:$tag_b"
done
created=0
cleanup() {
  if [[ $created == 1 && ${OPS_KIND_KEEP:-0} != 1 ]]; then kind delete cluster --name "$cluster"; fi
}
trap cleanup EXIT
kind create cluster --name "$cluster" --image "$node_image" --kubeconfig "$KUBECONFIG" --wait 180s
created=1
for image in sentinel-risk-gateway sentinel-risk-console-api sentinel-risk-console; do
  kind load docker-image --name "$cluster" "$image:$tag_a" "$image:$tag_b"
done
python3 ops/kind_run.py --config "$config" --output "$output/kind-lifecycle.json"
