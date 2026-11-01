#!/usr/bin/env bash
# DAST: OWASP ZAP (Docker) baseline (passive) + time-boxed full scan (active) against both apps on localhost.
# Usage: scan/run_dast.sh [baseline|full|both]   env: PY (python with flask), ZAP_IMAGE, FULL_MINUTES (default 5)
set -euo pipefail
cd "$(dirname "$0")/.."
exec "${PY:-python3}" scan/run_dast.py "${1:-both}"
