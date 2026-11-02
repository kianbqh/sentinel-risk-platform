#!/usr/bin/env bats
setup() {
  REPO_ROOT=$(cd "$BATS_TEST_DIRNAME/../.." && pwd)
  TEST_BIN="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$TEST_BIN"
  export OPS_TEST_CALLS="$BATS_TEST_TMPDIR/calls"
  for tool in helm kubectl python3; do
    printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$*" >> "$OPS_TEST_CALLS"\n' > "$TEST_BIN/$tool"
    chmod +x "$TEST_BIN/$tool"
  done
  export PATH="$TEST_BIN:$PATH"
}
@test "help has no external side effects" {
  run bash "$REPO_ROOT/ops/sentinelctl.sh" --help
  [ "$status" -eq 0 ]
  [[ "$output" == Usage:* ]]
  [ ! -e "$OPS_TEST_CALLS" ]
}
@test "invalid commands and shell-like image tags are rejected" {
  run bash "$REPO_ROOT/ops/sentinelctl.sh" rollout 'bad;tag'
  [ "$status" -eq 2 ]
  [ ! -e "$OPS_TEST_CALLS" ]
  run bash "$REPO_ROOT/ops/sentinelctl.sh" drill production-outage
  [ "$status" -eq 2 ]
  [ ! -e "$OPS_TEST_CALLS" ]
}
@test "rollout scopes the release namespace and context" {
  run bash "$REPO_ROOT/ops/sentinelctl.sh" rollout m1b-b
  [ "$status" -eq 0 ]
  run cat "$OPS_TEST_CALLS"
  [[ "$output" == *'upgrade m1b deploy/helm/sentinel --namespace sentinel-m1b --kube-context kind-sentinel-m1b'* ]]
  [[ "$output" == *'images.gateway.tag=m1b-b,images.backend.tag=m1b-b,images.frontend.tag=m1b-b'* ]]
}
@test "rollback selects the actual previous Helm revision" {
  run bash "$REPO_ROOT/ops/sentinelctl.sh" rollback
  [ "$status" -eq 0 ]
  run cat "$OPS_TEST_CALLS"
  [[ "$output" == 'rollback m1b 0 --namespace sentinel-m1b --kube-context kind-sentinel-m1b --wait --timeout 5m' ]]
}
@test "drill delegates to the real evidence runner" {
  export SENTINEL_OUTPUT_DIR=results/ops/custom-run
  run bash "$REPO_ROOT/ops/sentinelctl.sh" drill db-outage
  [ "$status" -eq 0 ]
  run cat "$OPS_TEST_CALLS"
  [[ "$output" == 'ops/verify.py drill --config ops/config.json --output results/ops/custom-run/drill-db-outage.json' ]]
}
