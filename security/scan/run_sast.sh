#!/usr/bin/env bash
# SAST: bandit + semgrep (registry packs p/python, p/flask, p/secrets + local rules) on vulnerable vs fixed app.
set -euo pipefail
cd "$(dirname "$0")/.."
exec "${PY:-python3}" scan/run_sast.py
