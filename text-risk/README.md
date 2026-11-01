# llm-risk-classifier

I wanted to see whether a small local language model could compete with a simple supervised classifier on short messages. This project compares two Qwen models with TF-IDF + logistic regression and regex rules, then serves the same choices through a FastAPI API with caching and fallback routing.

The service has:

- structured JSON output, validated with pydantic (`extra=forbid`, enum categories);
- an HTTP timeout with a **rule-based fallback** when the LLM times out, errors, or returns invalid JSON;
- a TTL/LRU **cache**, keyed on prompt version, model and normalised text;
- a **hybrid mode**: TF-IDF handles the confident cases and only uncertain ones (0.05 ≤ p ≤ 0.95) go to the LLM;
- **Prometheus** `/metrics`: requests by source, latency histograms, LLM errors by kind, cache hits and misses, tokens;
- a Dockerfile and CI.

The HTTP client uses the OpenAI-compatible chat-completions protocol. These measurements use local CUDA llama.cpp endpoints.

## Data

[UCI SMS Spam Collection](https://archive.ics.uci.edu/dataset/228/sms+spam+collection): Almeida & Gómez Hidalgo, 2011, licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The download is checked against its SHA-256.

- **Cleaning:** 5,574 messages, reduced to 5,171 after removing exact duplicate texts. The dedup step stops copies of the same spam leaking across the split.
- **Split:** stratified 80/20 with seed 42. Train has 4,136 messages (522 scam); test has 1,035 (131 scam).
- **Labels:** "spam" is mapped to `scam`.

The API maps the dataset's spam label to `scam` and ham to `legit`. Spam includes unsolicited marketing as well as prize, premium-rate and phishing messages; the results describe this SMS labeling task.

## Results

Measured on October 31, 2026, with **NVIDIA GeForce RTX 3080 12 GB**, driver 621.01, and official **llama.cpp b12024 / CUDA 12.8** binaries in WSL 2. The models offloaded **25/25** and **29/29** layers to CUDA, respectively. Each server used four CPU threads, one slot and a 4,096-token context. Both used prompt v1, four fixed examples, temperature 0, seed 42, max 80 output tokens, schema-constrained JSON and one untimed warm-up.

The stratified 400-message evaluation contains 51 spam messages and uses exactly the same texts and labels for both models. TF-IDF/rules latency below is from the paired 1.5B run; both complete paired reports are saved. On the full 1,035-message test set, TF-IDF achieved **F1 0.9575**, accuracy 0.9894, and PR-AUC 0.9891.

| System, 400 messages | Accuracy | Precision | Recall | F1 | p50 ms | p95 ms | Elapsed s / 1k messages |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TF-IDF + LR | 0.9850 | 0.9787 | 0.9020 | 0.9388 | 0.9011 | 1.0895 | 0.9207 |
| Rules | 0.9550 | 0.9714 | 0.6667 | 0.7907 | 0.0109 | 0.0271 | 0.0137 |
| Qwen2.5-0.5B Q4, CUDA | 0.6975 | 0.2308 | 0.5882 | 0.3315 | 73.0529 | 86.1212 | 74.0264 |
| Qwen2.5-1.5B Q4, CUDA | 0.8075 | 0.3818 | 0.8235 | 0.5217 | 97.8644 | 125.1869 | 102.3438 |
| Hybrid → 0.5B, replay | 0.9525 | 0.7963 | 0.8431 | 0.8190 | 0.9552 | 74.7921 | 9.3240 |
| Hybrid → 1.5B, replay | 0.9600 | 0.8182 | 0.8824 | 0.8491 | 0.9142 | 105.7636 | 12.8455 |

Elapsed time per 1,000 messages is mean sequential client wall time scaled to 1,000, including local HTTP, decoding and validation for LLM calls. The hybrid rows **replay** the confidence gate on paired predictions; their latency is a **derived** TF-IDF+LLM sum, with the LLM selected for 45/400 messages (11.25%). Actual routed HTTP measurements are below.

Both LLMs returned 400/400 successful schema-validated verdicts with no fallback in the offline evaluation. Reported prompt tokens per 1,000 messages were 380,090 for each model; completion tokens were 28,590 and 28,292. [Model revisions and verified hashes](results/model_provenance.json) and [the exact environment](results/environment.json) are recorded with the results.

### Service requests

One client sent 100 distinct normalized test messages per phase to a native uvicorn service using the 1.5B CUDA backend. Cache hits replay the first phase's messages; timeout and hybrid phases use separate test-message groups. A 5 ms deadline forces the timeout path.

| Phase | p50 ms | p95 ms | p99 ms | Observed sources |
| --- | ---: | ---: | ---: | --- |
| LLM, cache miss | 97.0037 | 121.5538 | 125.5520 | llm 100 |
| Same messages, cache hit | 0.7321 | 0.8778 | 1.0335 | cache 100 |
| Forced 5 ms timeout | 6.7206 | 7.1633 | 9.1258 | rules 100 |
| Hybrid, actual HTTP routing | 2.0268 | 102.4554 | 116.6515 | tfidf 86, llm 14 |

Every phase reconciled HTTP response sources against Prometheus request counts, latency observations, cache lookups, successful LLM calls and error counters using before/after deltas. The timeout phase recorded exactly 100 timeout errors and 100 rule fallbacks. [The report](results/loadtest.json) and [counter snapshots](results/loadtest_metrics.txt) retain the evidence.

TF-IDF was the strongest classifier on this SMS split. The 1.5B model produced 42 true positives and 68 false positives, versus the baseline's F1 0.9388. Hybrid replay improved on direct LLM classification but did not match the supervised baseline. Caching and timeout routing changed response latency as intended.

The **16 tests** cover schema validation, malformed responses, timeout fallback, TTL/LRU caching, hybrid routing, metrics, data splitting and baseline training. [Raw-prediction checks](results/evaluation_check.json) reproduce both confusion matrices. [CI](.github/workflows/ci.yml) runs the tests and baseline evaluation, then starts a CPU TF-IDF container and checks its real health, classification and metrics endpoints.

## Run it

```bash
pip install -r requirements-dev.txt
python -m riskclf.baseline
python -m pytest -q tests --capture=sys  # 16 tests

# With a CUDA llama.cpp installation and the GGUF files:
LLAMA_SERVER=/path/to/llama-server NGL=99 THREADS=4 bash scripts/start_llama.sh /path/to/qwen2.5-0.5b-instruct-q4_k_m.gguf 8090 &
LLAMA_SERVER=/path/to/llama-server NGL=99 THREADS=4 bash scripts/start_llama.sh /path/to/qwen2.5-1.5b-instruct-q4_k_m.gguf 8091 &
python eval/run_eval.py --llm qwen2.5-0.5b-q4=http://127.0.0.1:8090/v1,qwen2.5-0.5b-instruct-q4_k_m --n-llm 400 --tag eval_qwen0.5b
python eval/run_eval.py --llm qwen2.5-1.5b-q4=http://127.0.0.1:8091/v1,qwen2.5-1.5b-instruct-q4_k_m --n-llm 400 --tag eval_qwen1.5b
python scripts/loadtest.py --llm-url http://127.0.0.1:8091/v1 --model qwen2.5-1.5b-instruct-q4_k_m --n 100 --port 8008 --timeout-s 0.005

# Start the API and keep it running:
CLASSIFIER_MODE=hybrid LLM_BASE_URL=http://127.0.0.1:8091/v1 LLM_MODEL=qwen2.5-1.5b-instruct-q4_k_m uvicorn riskclf.service:app --port 8008
```

From another terminal:

```bash
curl -s localhost:8008/v1/classify -H 'content-type: application/json' -d '{"text":"WINNER! Claim your prize now"}'
```

The container provides a CPU baseline entry point:

```bash
docker build -t llm-risk-classifier .
docker run --rm -p 8008:8000 -e CLASSIFIER_MODE=tfidf llm-risk-classifier
```

GGUF weights: [Qwen2.5-0.5B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF) and [Qwen2.5-1.5B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF), Apache-2.0, file `*-q4_k_m.gguf`. The recorded runtime is from [llama.cpp b12024](https://github.com/ggml-org/llama.cpp/releases/tag/b12024).
