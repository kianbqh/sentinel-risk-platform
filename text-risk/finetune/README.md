# SMS QLoRA comparison

I use this experiment to test whether a small local language model benefits from task-specific adaptation on the SMS Spam Collection. TF-IDF remains the served default. The matched prompt-only baseline and each adapter use the same pinned Qwen model, NF4 preparation, binary JSON prompt, truncation, greedy decoding and failure policy.

No QLoRA quality, training-time or inference-speed result has been measured by this implementation yet. The dependency file is a candidate set, not a tested compatibility lock. Results belong in a new `copilot/results/` directory after the actual runs finish.

## Data and protocol

[UCI SMS Spam Collection](https://archive.ics.uci.edu/dataset/228/sms+spam+collection) is licensed CC BY 4.0. This experiment uses the existing exact-text deduplication and seed-42 80/20 split. Spam maps to the API label `scam`; ham maps to `legit`. [The frozen manifest](protocol/sms_split.json) records the source hashes and ordered full-test and paired-test IDs. An ID is SHA-256 of the exact UTF-8 message, without trimming or normalization.

The original training membership contains 4,136 unique messages. A fixed train-only validation partition contains 414 messages, chosen by largest-remainder class stratification and SHA-256 ranking with validation seed 1729. The remaining 3,722 messages fit each adapter. This partition is identical across training seeds 42, 43 and 44. Checkpoint selection uses only the lowest validation causal-LM loss among the configured epochs. No full-test or paired-test score selects a checkpoint, prompt, epoch or hyperparameter.

Training supervises only the assistant's true binary label JSON; it creates no category or explanation targets. Prompts are masked with `-100`, including padding; the assistant response and chat end token remain supervised. The fixed config truncates the user message to 384 tokenizer tokens before chat rendering, for both training and inference, and fails if the prompt or complete target exceeds the sequence budget. It records truncation counts. The label schema is deliberately smaller than the existing serving schema because the dataset supplies only a binary label.

Inference validates exactly `{"label":"scam"}` or `{"label":"legit"}`. Duplicate keys, extra fields, code fences and malformed JSON fail. Both matched NF4 systems use the existing rules classifier on failure, and report JSON failures, schema failures, validity rate and valid-only metrics alongside the resulting classifier score. Historical GGUF predictions already use their shipped failure behavior and their original, different prompt/runtime.

## Setup

Use WSL2 with CUDA and an isolated environment under `/opt/codex-resume/all-modules-v2/m1a`, on the G-backed Linux disk. The shared ML environment is read-only. Put all caches, model files, checkpoints and logs beneath that private prefix. Resolve [requirements.candidate.txt](requirements.candidate.txt), run `pip check`, record the resolved environment, and pass the actual NF4 smoke before calling the stack compatible. Commit the reviewed experiment source before measured runs; the harness rejects dirty experiment/baseline source.

Set `FINETUNE_GIT` explicitly when Git is absent from WSL's PATH. The prepared task runtime uses `/opt/codex-resume/all-modules-v2/m1a/tools/git-native/usr/bin/git`, extracted into the task prefix from a hash-checked Ubuntu package, without a global installation or Git-identity edit. The code also provides Windows `.exe` path translation through `wslpath` when that executable and working WSL interop are available; that branch is not the tested backend on this machine. All Git invocations use list arguments, `--no-optional-locks` and `GIT_OPTIONAL_LOCKS=0` to avoid optional index writes. Provenance records the actual backend, executable/hash, version and commands.

`selected_source_scope` is explicitly the finetune and riskclf paths plus the raw SMS file and TF-IDF artifact. Selected paths must be clean. Separate `whole_worktree_status`/`whole_worktree_dirty` fields report unrelated work, so concurrent uncommitted copilot files do not pretend the whole repository is clean. Fingerprints cover the finetune tree and the current riskclf Python source, including the actual rules fallback, schema and data code. Git HEAD, source fingerprints and config/protocol identities must remain unchanged across smoke, training and evaluation.

[Qwen2.5-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct) is pinned at `989aa7980e4cf806f80c7fef2b1adb7bc71aa306`, under Apache-2.0. [The model reference](protocol/qwen_reference.json) lists upstream sizes and hashes. Downloads verify the LFS SHA-256 of the safetensors file and the Git blob identities of tokenizer/config/license files. Base weights and adapters stay outside Git.

```bash
cd /mnt/g/ResumeProject/platforms/sentinel-risk-platform
M1A_RUNTIME=/opt/codex-resume/all-modules-v2/m1a
export HF_HOME="$M1A_RUNTIME/cache/huggingface"
export PIP_CACHE_DIR="$M1A_RUNTIME/cache/pip"
export XDG_CACHE_HOME="$M1A_RUNTIME/cache/xdg"
export TORCH_HOME="$M1A_RUNTIME/cache/torch"
export TMPDIR="$M1A_RUNTIME/tmp"
export CUBLAS_WORKSPACE_CONFIG=:4096:8
export FINETUNE_GIT="$M1A_RUNTIME/tools/git-native/usr/bin/git"
export PYTHONDONTWRITEBYTECODE=1
PY="$M1A_RUNTIME/train-venv/bin/python"
CFG=text-risk/finetune/configs/qwen15_nf4.json
# Choose a new immutable group name for each execution.
GROUP="$M1A_RUNTIME/runs/qlora-YYYYMMDDTHHMMSSZ"
```

The following steps are intended for a reserved GPU window after environment setup. The download step needs internet; the model runs are offline. There is no paid API.

```bash
"$PY" text-risk/finetune/run.py download --config "$CFG" --run-dir "$GROUP/download"
export HF_HUB_OFFLINE=1
PYTHONHASHSEED=42 "$PY" text-risk/finetune/run.py smoke --config "$CFG" --run-dir "$GROUP/smoke"
for SEED in 42 43 44; do
  PYTHONHASHSEED="$SEED" "$PY" text-risk/finetune/run.py train --config "$CFG" \
    --seed "$SEED" --smoke-run "$GROUP/smoke" --run-dir "$GROUP/train-$SEED"
done
PYTHONHASHSEED=42 "$PY" text-risk/finetune/run.py evaluate --config "$CFG" \
  --smoke-run "$GROUP/smoke" --run-dir "$GROUP/prompt-only"
for SEED in 42 43 44; do
  PYTHONHASHSEED=42 "$PY" text-risk/finetune/run.py evaluate --config "$CFG" \
    --smoke-run "$GROUP/smoke" --adapter-run "$GROUP/train-$SEED" --run-dir "$GROUP/eval-$SEED"
done
"$PY" text-risk/finetune/run.py compare --config "$CFG" --run-dir "$GROUP/comparison" \
  --prompt-run "$GROUP/prompt-only" --seed-run "$GROUP/eval-42" \
  --seed-run "$GROUP/eval-43" --seed-run "$GROUP/eval-44"
```

The smoke checks actual CUDA `Linear4bit` layers with materialized NF4 state, finite forward loss, nonzero adapter gradients, an optimizer update, adapter save/reload and matching logits. All parameters must stay on CUDA. Prompt-only and adapter inference apply the same PEFT k-bit preparation casts. Only LoRA parameters may train. NF4 is never substituted by GGUF, unquantized weights or CPU execution.

Each action requires a unique run path beneath the private `runs` directory and refuses an existing path. `--resume` applies only to an interrupted owned training directory with matching source/config/protocol/environment and an intact Trainer checkpoint. It retains journals for all attempts and refuses a completed summary or saved final adapter. A process lock prevents concurrent ownership. After a hard crash, inspect the owning PID and preserve the lock/journals before coordinator-managed recovery; the script never guesses that a lock is stale.

## Evaluation and evidence

Each model predicts the frozen 1,035 messages once for quality; the paired 400 are selected by their exact IDs from those predictions. Three complete timed repeats follow the same full-test order, after three untimed warmup messages, with batch size one and concurrency one. Raw warmups, per-message times, prompt/completion tokens, outputs, failures and repeat disagreements are retained. The timer includes tokenization, device transfer, generation, decoding, parsing and optional rules fallback, with CUDA synchronization, and excludes model load. Percentiles use linear interpolation.

The comparison replays the unchanged served TF-IDF artifact and verifies its paired probabilities against both retained CSVs. Historical Qwen 0.5B/1.5B and their documented confidence-gated hybrids appear only on the paired 400. Their hybrid predictions are reconstructed from the retained paired probabilities/labels and checked against the retained rounded F1. No historical full-1,035 LLM or hybrid predictions are available.

The report contains F1 and paired F1 differences with 95% percentile bootstrap intervals, using 10,000 paired row resamples with fixed seed 20261102. It computes exact two-sided McNemar tests on correctness discordances against TF-IDF, plus matched prompt-only comparisons for each adapter. P-values are unadjusted per-comparison tests. Sampling intervals condition on one fitted model. Across the three training seeds, mean and population standard deviation (`ddof=0`) are separate fields and never presented as sampling confidence intervals.

Every result JSON carries git SHA, config/protocol hashes, source hashes, package versions, CPU, GPU, driver/CUDA information, exact command, environment, timestamp/time zone and wall time. Training records actual wall time and peak allocated/reserved PyTorch VRAM; adapter/checkpoint manifests record bytes and SHA-256. Checkpoints/weights never enter Git. Publish only newly generated small evidence files under a new result path, with coordinator-managed release assets and SHA-256 if weights are shared. Do not run the old evaluator: it writes frozen `text-risk/results/` files.

## CPU checks

The protocol/statistics/isolation tests use only the standard library and do not load a model. Supplying `SENTINEL_REPO` also verifies the real immutable SMS file, exact held-out identities, train-only partition and both historical prediction CSVs.

```bash
PYTHONPATH=text-risk SENTINEL_REPO="$PWD" "$PY" -m unittest discover -s text-risk/finetune/tests -v
```

## Limitations

This is one small public SMS corpus and one fixed split. Binary label generation is a classification experiment, separate from the copilot's triage and tool evaluation. Rules fallback can influence F1, so inspect schema-valid and valid-only scores. Historical GGUF runs differ in prompt, quantization and runtime; the NF4 prompt-only run is the matched adaptation control. Three training seeds offer a limited view of optimizer variation. PyTorch allocator peaks are not whole-machine GPU peaks. Deterministic algorithms are requested with warnings rather than a promise of cross-version bitwise identity.

## Implementation references

- [Transformers bitsandbytes integration](https://huggingface.co/docs/transformers/main/quantization/bitsandbytes)
- [PEFT quantization and all-linear QLoRA](https://huggingface.co/docs/peft/v0.21.0/developer_guides/quantization)
- [Transformers chat templates](https://huggingface.co/docs/transformers/main/chat_templating)
- [Transformers Trainer](https://huggingface.co/docs/transformers/main_classes/trainer) and [the pinned TrainingArguments source](https://github.com/huggingface/transformers/blob/v5.22.1/src/transformers/training_args.py)
- [bitsandbytes installation matrix](https://huggingface.co/docs/bitsandbytes/installation)
