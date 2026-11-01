#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
DOCKER=${DOCKER_BIN:-docker}
image=${TRIVY_IMAGE:-aquasec/trivy:latest}
cache=${TRIVY_CACHE_DIR:-$PWD/.runtime/cache/trivy}
mkdir -p "$cache" results/security
"$DOCKER" pull "$image" > results/security/trivy-pull.log 2>&1
"$DOCKER" image inspect --format '{{json .RepoDigests}}' "$image" > results/security/trivy-image.json
"$DOCKER" run --rm "$image" version > results/security/trivy-version.txt
"$DOCKER" run --rm --network=host -v "$PWD:/src:ro" -v "$PWD/results/security:/reports" -v "$cache:/cache" -w /src "$image" \
 fs --cache-dir /cache --scanners vuln,secret --skip-files .env --skip-dirs .git --skip-dirs .runtime \
 --skip-dirs '**/node_modules' --skip-dirs '**/target' --skip-dirs '**/dist' \
 --skip-dirs security/fixtures --skip-dirs research --skip-dirs batch --skip-dirs docs \
 --format json --output /reports/trivy.json . > results/security/trivy.log 2>&1
python3 scripts/trivy_gate.py
