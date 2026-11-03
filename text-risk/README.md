# Short-message risk classification

I compared TF-IDF/logistic regression, two small local Qwen models, and three QLoRA runs on UCI's SMS Spam Collection. I kept TF-IDF as the gateway default: it provides a strong baseline with much lower request latency in this local experiment.

The corpus has 5,574 messages and 5,171 unique texts. The fixed seed 42 split contains 4,136 training and 1,035 test messages; the retained paired subset contains 400 test messages, including 51 spam messages. Spam maps to the API label `scam`, including unsolicited promotions. QLoRA uses a fixed 414-message validation set drawn only from the training split; the three training seeds are 42, 43 and 44.

## QLoRA experiment

The November 2 run used Qwen2.5-1.5B-Instruct with actual NF4 quantization and PEFT LoRA on one RTX 3080. Prompt-only and adapter evaluations share the prompt, quantization and greedy decoding. The strict output contract accepts exactly `{"label":"scam"}` or `{"label":"legit"}`; invalid output uses the existing rules fallback.

| System | Full test F1 (1,035) | Paired subset F1 (400) |
|---|---:|---:|
| TF-IDF + logistic regression | 0.9575 | 0.9388 |
| Matched prompt-only NF4 + rules fallback | 0.7778 | 0.7907 |
| QLoRA seed 42 + rules fallback | 0.9807 | 0.9697 |
| QLoRA seed 43 + rules fallback | 0.9734 | 0.9703 |
| QLoRA seed 44 + rules fallback | 0.9807 | 0.9800 |
| QLoRA mean ± population std, three seeds | 0.9783 ± 0.0034 | 0.9733 ± 0.0047 |

The prompt-only model wrapped every first-repeat response in a code fence, so all 1,035 failed the strict parser and its primary scores above come entirely from rules fallback. Seed 42 had one schema failure; seeds 43 and 44 had none. Raw failures and valid-only scores are retained. The higher QLoRA point estimates do not establish a consistent statistically significant gain over TF-IDF: full-test paired F1 difference intervals include or touch zero, and exact McNemar p-values are 0.1460, 0.3438 and 0.1094.

Measured single-request p50/p95 latency was 0.901/1.144 ms for TF-IDF, 563/637 ms for prompt-only, and 486/642, 472/638, 482/640 ms for the three adapters. Each distribution contains three complete repeats after three warmups, with batch size and concurrency both 1. Quality uses the first repeat only. Loading and warmups are excluded; tokenization, generation, parsing and optional fallback are included for NF4.

See the [result report](results/qlora/20261102-5465aae5/RESULTS.md), [paired intervals and tests](results/qlora/20261102-5465aae5/comparison/table.md), and [training/reproduction instructions](finetune/README.md). Seed std describes variation between training runs; the separate 95% paired bootstrap intervals describe test-message sampling uncertainty for a fixed model.

### Separate posthoc format diagnostic

After observing the strict failures, I applied the same narrowly defined single-outer-fence removal to all four systems' saved first-repeat outputs, then reused the strict parser. This descriptive check changes the prompt-only full-test F1 to 0.3393, with 883 valid outputs and 152 retained rule fallbacks; valid-only F1 is 0.2904. The paired subset F1 is 0.3194, with 341 valid outputs and 59 fallbacks. All three QLoRA predictions remain unchanged. This separates format compliance from semantic classification. The primary table remains unchanged, and the diagnostic adds no inference, normalized latency or formal significance tests. See the separate [diagnostic report](results/qlora/20261102-5465aae5/posthoc-format/diagnostic-summary.json) and [exact protocol](results/qlora/20261102-5465aae5/posthoc-format/POSTHOC_FORMAT_DIAGNOSTIC_PROTOCOL.json).

## Retained GGUF comparison

The October 31 local evaluation used the same paired 400 messages. Both Qwen models ran through llama.cpp on the RTX 3080; hybrid rows replay the TF-IDF confidence gate over the paired predictions. These original prompts/runtime differ from the matched NF4 experiment.

| Historical system | Paired subset F1 (400) |
|---|---:|
| TF-IDF + logistic regression | 0.9388 |
| Rules | 0.7907 |
| Qwen2.5-0.5B Q4 | 0.3315 |
| Qwen2.5-1.5B Q4 | 0.5217 |
| Hybrid→0.5B | 0.8190 |
| Hybrid→1.5B | 0.8491 |

The original files under `results/` remain intact. Historical LLM/hybrid predictions cover the paired 400 messages only; no historical full-test result is inferred from that subset.

`riskclf.Classifier` retains validated output, TTL/LRU caching, timeout/error rule fallback and the hybrid 0.05≤p≤0.95 gate. The shared gateway exposes `POST /v1/text/classify`. LLM-only decisions have no probability; a hybrid response's probability comes from TF-IDF even when the LLM supplies its label. Tests cover the classifier and the finetune data, masking, provenance and statistics contracts; shared-gateway and real-service evidence are at the repository root.

This experiment uses one public SMS corpus and a fixed train/test split. Data: [UCI SMS Spam Collection](https://archive.ics.uci.edu/dataset/228/sms+spam+collection), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
