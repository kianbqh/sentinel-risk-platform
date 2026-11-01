#!/usr/bin/env bash
# End-to-end run: download -> Spark features -> train (MLflow) -> batch score -> tests
set -euo pipefail
cd "$(dirname "$0")"
if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
fi
export PYTHONPATH=src
export SPARK_LOCAL_IP="${SPARK_LOCAL_IP:-127.0.0.1}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-8}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-8}"
mkdir -p .tmp
export TMPDIR="$(pwd)/.tmp"
export XDG_CACHE_HOME="$(pwd)/.cache"
export MPLCONFIGDIR="$XDG_CACHE_HOME/matplotlib"
export SPARK_LOCAL_DIRS="$TMPDIR/spark"
python -m fraudml.data
python -m fraudml.spark_features
python -m fraudml.train
python -m fraudml.batch_score
# Python-level capture also works when TMPDIR is on a WSL-mounted drive.
python -m pytest -q --capture=sys tests
echo "Done. Metrics: reports/metrics.json | MLflow UI: mlflow ui --backend-store-uri sqlite:///mlflow.db"
