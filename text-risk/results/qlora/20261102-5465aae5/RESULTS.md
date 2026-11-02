# SMS QLoRA results — November 2, 2026

The run trained Qwen2.5-1.5B-Instruct NF4 adapters with seeds 42/43/44 and evaluated them against a matched prompt-only NF4 model and the unchanged TF-IDF artifact. TF-IDF remains the served default.

Original source commit: `5465aae5af963879f6cfcb83230e962ca0cf9ee0`. Base revision: `989aa7980e4cf806f80c7fef2b1adb7bc71aa306`, Apache 2.0. Runtime: WSL2, RTX 3080, driver 621.01, Torch 2.15.0+cu132/CUDA 13.2, Transformers 5.22.1, PEFT 0.21.2, bitsandbytes 0.50.2. Exact commands, source/config hashes, versions, timezone offsets and timings are in the stage summaries. Publication can occur at a later commit without relabeling the original experiment's source revision.

## Primary quality and latency

The 4,136-message training partition contains a fixed 414-message train-only validation set. Training/checkpoint selection uses no held-out messages. Each system first evaluates all 1,035 test messages; paired subset (400) is then selected by the frozen exact IDs. The paired subset is not a second independent test set.

| System | Full test (1,035) F1 | Paired subset (400) F1 | Full strict valid | Latency p50/p95 ms |
|---|---:|---:|---:|---:|
| TF-IDF | 0.9575289575 | 0.9387755102 | n/a | 0.900702 / 1.143615 |
| Matched prompt-only NF4 + rules | 0.7777777778 | 0.7906976744 | 0/1035 | 563.209037 / 636.578058 |
| QLoRA 42 + rules | 0.9806949807 | 0.9696969697 | 1034/1035 | 486.223849 / 642.255195 |
| QLoRA 43 + rules | 0.9733840304 | 0.9702970297 | 1035/1035 | 472.440628 / 637.583727 |
| QLoRA 44 + rules | 0.9806949807 | 0.9800000000 | 1035/1035 | 481.896327 / 640.420672 |

All NF4 primary scores use the frozen strict JSON/schema parser and the declared rules fallback. Prompt-only produced code-fenced output on every first-repeat row, giving 1,035 invalid_json failures and valid-only=null. Its primary F1 is therefore the fallback endpoint score, not native prompt quality. Seed 42 had 1 schema failure; valid-only full F1 is 0.9844961240 on 1,034 rows and paired F1 is 0.9795918367 on 399 rows. Seeds 43/44 had no failures; their valid-only scores equal the primary scores. Across all 3,105 timed requests, failure counts are 3,105 invalid_json for prompt-only, 3 schema failures for 42, and 0 for 43/44. Final prediction/failure-category repeat disagreements are 0 for every system.

Latency uses batch size 1 and concurrency 1, three warmups and three full repeats: 3,105 actual request samples per system. NF4 timing synchronizes CUDA and includes tokenization/device copy/generation/decode/parse/fallback; TF-IDF includes one-message vectorization and predict_proba. Model loading/warmups are excluded. Raw samples are in each stage's `latency.jsonl` and `comparison/tfidf-latency.jsonl`. Quality uses repeat 0, not pooled repeats.

Three-seed F1 mean ± population std (ddof=0) is **0.9782579973 ± 0.0034464150** on full test (1,035) and **0.9733313331 ± 0.0047218186** on paired subset (400). These are training-seed statistics, not confidence intervals. [comparison/table.md](comparison/table.md) and [comparison/summary.json](comparison/summary.json) retain separate 95% paired row-bootstrap intervals (10,000 draws, seed 20261102), paired F1 difference intervals, and unadjusted exact two-sided McNemar tests versus TF-IDF and matched prompt-only.

The full-test TF-IDF difference intervals for seeds 42/43/44 are [-0.003656837,0.050484576],[-0.007162214,0.040257871],[0,0.048417295]; McNemar p-values are 0.14599609375,0.34375,0.109375. Point estimates favor QLoRA, but this does not establish a consistent statistically significant gain over TF-IDF. No test-set seed selection was performed.

## Separate descriptive posthoc format diagnostic

The diagnostic protocol was declared after seeing strict-format failures and primary fallback F1, before calculating normalized quality. It strips surrounding whitespace and at most one complete outer fence opened exactly with three backticks or three backticks plus `json`, then uses the same strict parser. It applies uniformly to saved first-repeat outputs from prompt-only and all 3 adapters; unparseable rows retain their already-recorded fallback. There is no new inference, rule rerun, normalized latency, formal posthoc CI/p-value or parameter selection.

| Prompt-only descriptive diagnostic | Full test (1,035) | Paired subset (400) |
|---|---:|---:|
| F1 including retained fallback | 0.3392857143 | 0.3193916350 |
| Valid rows | 883 | 341 |
| Still-invalid rows / retained fallback | 152 | 59 |
| Valid-only F1 | 0.2903752039 | 0.2761506276 |

All 3 QLoRA predictions remain unchanged, including seed 42's one schema failure. The primary strict table remains unchanged. The diagnostic exposes the distinction between fallback endpoint quality, format adherence and semantic classification. Exact protocol, source hashes, row-level diagnostic decisions and output manifest are under [posthoc-format/](posthoc-format/).

## Training and resource records

Each seed completed 3 epochs / 699 optimizer steps, with no resume or training/validation target truncation. Checkpoints were selected only by minimum train-only validation loss.

| Seed | Trainer.train wall s | Entire stage s | Selected step | Val loss | Peak allocated / reserved bytes |
|---:|---:|---:|---:|---:|---:|
| 42 | 2631.823869 | 2670.228128 | 466 | 0.0062991357 | 3529354240 / 7967080448 |
| 43 | 3249.787171 | 4306.738048 | 466 | 0.0061855605 | 3529349632 / 10846470144 |
| 44 | 2684.806525 | 2756.643643 | 233 | 0.0071999463 | 3529771008 / 4395630592 |

Train wall includes scheduled validation/checkpoint saves/best reload; stage wall includes startup and final operations. Memory values are PyTorch allocator counters, not whole-system physical VRAM. Authorized CPU preparation/download/checksum activity during training is recorded in `coordination.jsonl`; real wall times include that activity. Before latency measurement, root verified the KITTI downloader/checksum threads were actually suspended and other heavy preparation had stopped. Small source/remoteCI activity remained possible; this was not whole-machine isolation. Seed 43's startup/I/O delays are retained, without claiming a controlled causal diagnosis. Seed control does not imply bitwise determinism: captured SDPA backward warnings remain in the logs.

## Evidence and scope

[CONTINUATION_COMPLETE.json](CONTINUATION_COMPLETE.json) records 108 small-file hashes; original training summaries record adapter/checkpoint hashes. [adapter-rehash.json](adapter-rehash.json) is an additional one-time sequential actual rehash of the 3 final adapters, all matching. Binary weights/checkpoints are excluded from Git. `train-*`, `prompt-only`, `eval-*`, `comparison`, `stages` and `logs` preserve raw small evidence; `audit` preserves independent acceptance sources/receipts. Original source code and the tested lock remain under [finetune](../../../finetune/README.md).

Historical GGUF/hybrid scores and paired subset (400) bootstrap comparisons remain in the comparison report. Their original prompt/runtime/fallback history differs, and their retained predictions cover only 400 messages; historical full-test predictions are unavailable and their timing remains historical. Existing result files are preserved. This experiment uses one public SMS corpus, one fixed train/test split, one GPU and three seeds.
