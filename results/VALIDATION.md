# Validation origins

- Local WSL2/private Python: fresh UCI download with archive SHA-256 check, TF-IDF retraining, 16 tests and dependency consistency check.
- Local CUDA: official llama.cpp b12024 CUDA 12.8 runtime, four threads, one slot per model, 4096 context, full 25/25 and 29/29 layer offload. Each model scored the same 400 held-out messages, with a warm-up excluded. Details and model hashes are in environment.json/model_provenance.json.
- Local HTTP: native uvicorn with 1.5B CUDA backend, four phases of 100 requests, complete Prometheus before/after counter reconciliation. Successful verdicts, timeouts and cache behavior are separately counted.
- Offline hybrid rows: replayed decisions on paired predictions and derived latency, not an independently timed routed run. The separate HTTP hybrid phase is measured routing.
- Timing units: elapsed sequential wall time per 1,000 messages, not process CPU time. CPU-only reference outputs are archived outside this repository.
- Independent acceptance: evaluation_check.json recomputes both 400-row confusion matrices from raw prediction CSVs and confirms identical texts/labels.
- Container validation: the GitHub Actions workflow builds the image, starts CPU TF-IDF mode and asserts real HTTP health/classification/metrics before cleanup. Its CI artifact is separate from local GPU evidence.
