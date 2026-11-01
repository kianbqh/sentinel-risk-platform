#!/usr/bin/env bash
# Start llama.cpp's OpenAI-compatible server for a GGUF model.
# Use a CUDA build and NGL=99 to offload all layers, or NGL=0 for CPU execution.
set -euo pipefail
MODEL=${1:?gguf path}; PORT=${2:-8090}
LLAMA_SERVER=${LLAMA_SERVER:-llama-server}
MODEL_FILENAME=${MODEL##*/}
MODEL_ALIAS=${MODEL_ALIAS:-${MODEL_FILENAME%.gguf}}
exec "$LLAMA_SERVER" -m "$MODEL" --port "$PORT" --host 127.0.0.1 -c 4096 -np 1 \
  -t "${THREADS:-4}" -ngl "${NGL:-0}" --alias "$MODEL_ALIAS" -lv "${LLAMA_LOG_VERBOSITY:-4}" --jinja --no-webui
