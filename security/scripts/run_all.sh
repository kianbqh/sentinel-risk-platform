#!/usr/bin/env bash
# Local equivalent of the CI pipeline: tests -> SAST -> detector evaluation -> DAST (needs Docker).
# Env: BIN (dir with python/bandit/semgrep, default: dir of `python3`), SKIP_DAST=1, FULL_MINUTES (ZAP full scan cap)
set -euo pipefail
cd "$(dirname "$0")/.."
BIN=${BIN:-$(dirname "$(command -v python3)")}
mkdir -p results
{
  echo "== $(date -Is)"
  "$BIN/python" -m pytest --capture=sys -q tests
  echo "== SAST"; BIN=$BIN ./scan/run_sast.sh
  echo "== detector"; OMP_NUM_THREADS=${OMP_NUM_THREADS:-2} "$BIN/python" -m detector.evaluate
} 2>&1 | tee results/run_all.log
if [[ -z "${SKIP_DAST:-}" ]]; then
  echo "== DAST (ZAP in Docker)"; PY="$BIN/python" ./scan/run_dast.sh both | tee -a results/run_all.log
fi
