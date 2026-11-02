"""Runtime entry points. Imports CUDA/ML packages only inside requested actions."""
from __future__ import annotations

import collections
import gc
import json
import os
import time
from pathlib import Path

from .artifacts import artifact_manifest, json_line
from .metrics import binary_metrics, percentile
from .modeling import (AssistantCollator, EncodedDataset, check_model_files, load_nf4,
                       parse_binary_json, prompt_ids, seed_everything)
from .protocol import evaluation_data, partition_record, sha256_file, training_data


def cuda_memory(torch):
    return {"peak_allocated_bytes": torch.cuda.max_memory_allocated(0),
            "peak_reserved_bytes": torch.cuda.max_memory_reserved(0),
            "end_allocated_bytes": torch.cuda.memory_allocated(0),
            "end_reserved_bytes": torch.cuda.memory_reserved(0),
            "scope": "PyTorch allocator on device 0; not whole-system or nvidia-smi peak"}


def assert_cache_roots(config, offline=True):
    root = Path(config["runtime_root"]).resolve()
    for variable in ("HF_HOME", "PIP_CACHE_DIR", "TMPDIR", "XDG_CACHE_HOME", "TORCH_HOME"):
        value = os.environ.get(variable)
        if not value or not Path(value).resolve().is_relative_to(root):
            raise RuntimeError(f"set {variable} explicitly beneath private runtime root")
    if offline and os.environ.get("HF_HUB_OFFLINE") != "1":
        raise RuntimeError("set HF_HUB_OFFLINE=1 for measured offline model runs")


def smoke(repo, config, run):
    import torch
    assert_cache_roots(config)
    seed_everything(42)
    files = check_model_files(repo, config)
    train, validation, _ = training_data(repo, config)
    examples = [next(row for row in train if row["y"] == label) for label in (0, 1)]
    torch.cuda.reset_peak_memory_stats(0)
    model, tokenizer, model_info = load_nf4(config, training=True)
    dataset = EncodedDataset(tokenizer, examples, config)
    batch = {k: v.to("cuda") for k, v in AssistantCollator(tokenizer.pad_token_id)(dataset.rows).items()}
    parameters = [(name, p) for name, p in model.named_parameters() if p.requires_grad]
    before = {name: p.detach().clone() for name, p in parameters}
    optimizer = torch.optim.AdamW([p for _, p in parameters], lr=config["training"]["learning_rate"])
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    loss = model(**batch).loss
    if not torch.isfinite(loss):
        raise RuntimeError("nonfinite NF4 forward loss")
    loss.backward()
    gradients = {name: float(p.grad.detach().float().norm().item()) for name, p in parameters if p.grad is not None}
    if not gradients or not all(torch.isfinite(torch.tensor(v)) for v in gradients.values()) or not any(v > 0 for v in gradients.values()):
        raise RuntimeError("LoRA gradients are missing, zero or nonfinite")
    optimizer.step()
    optimizer.zero_grad(set_to_none=True)
    changed = [name for name, p in parameters if not torch.equal(before[name], p.detach())]
    if not changed:
        raise RuntimeError("optimizer did not update adapters")
    measured_loss = float(loss.detach().float().cpu())
    model.eval()
    model.config.use_cache = True
    with torch.inference_mode():
        reference = model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]).logits[:, -1, :].float().cpu()
    adapter = run.path / "adapter"
    adapter.mkdir(exist_ok=False)
    model.save_pretrained(adapter, safe_serialization=True)
    tokenizer.save_pretrained(adapter)
    memory = cuda_memory(torch)
    del optimizer, batch, model, parameters, before, loss
    gc.collect()
    torch.cuda.empty_cache()
    model, tokenizer, reload_info = load_nf4(config, adapter=adapter)
    batch = {k: v.to("cuda") for k, v in AssistantCollator(tokenizer.pad_token_id)(dataset.rows).items()}
    with torch.inference_mode():
        reloaded = model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]).logits[:, -1, :].float().cpu()
    max_diff = float((reference - reloaded).abs().max().item())
    torch.testing.assert_close(reference, reloaded, rtol=0.001, atol=0.001)
    torch.cuda.synchronize()
    run.result("summary.json", {"status": "smoke_passed", "kind": "smoke", "quality_result": "not a quality measurement",
               "model_files": files, "model": model_info, "reload_model": reload_info,
               "forward_backward_save_reload_seconds": time.perf_counter() - t0,
               "training_examples": [row["id"] for row in examples], "train_only_partition": partition_record(train, validation, config),
               "loss": measured_loss, "adapter_gradient_norms": gradients,
               "changed_adapter_tensors": changed, "reload_max_abs_logit_difference": max_diff,
               "reload_tolerance": {"rtol": 0.001, "atol": 0.001}, "memory": memory,
               "adapter_files": artifact_manifest(adapter)})


def verify_smoke(smoke_run: Path, run):
    summary = json.loads((smoke_run / "summary.json").read_text(encoding="utf-8"))
    if summary["status"] != "smoke_passed":
        raise ValueError("actual NF4 smoke has not passed")
    for name in ("git_sha", "config_sha256", "protocol_sha256", "versions", "source_files"):
        if summary["provenance"][name] != run.provenance[name]:
            raise ValueError(f"smoke provenance mismatch: {name}")
    return {"summary_sha256": sha256_file(smoke_run / "summary.json"), "path": str(smoke_run)}


def train(repo, config, run, seed, smoke_run, resume=False):
    import torch
    from transformers import Trainer, TrainingArguments
    assert_cache_roots(config)
    smoke_reference = verify_smoke(smoke_run, run)
    seed_everything(seed)
    files = check_model_files(repo, config)
    training_rows, validation_rows, _ = training_data(repo, config)
    if not resume:
        run.result("partition.json", partition_record(training_rows, validation_rows, config))
    else:
        previous = json.loads((run.path / "partition.json").read_text(encoding="utf-8"))
        for key, value in partition_record(training_rows, validation_rows, config).items():
            if previous[key] != value:
                raise ValueError("resume partition mismatch")
    torch.cuda.reset_peak_memory_stats(0)
    model, tokenizer, model_info = load_nf4(config, training=True)
    training_set = EncodedDataset(tokenizer, training_rows, config)
    validation_set = EncodedDataset(tokenizer, validation_rows, config)
    settings = config["training"]
    checkpoint_root = run.path / "checkpoints"
    if not resume:
        checkpoint_root.mkdir(exist_ok=False)
    checkpoints = sorted(checkpoint_root.glob("checkpoint-*"), key=lambda p: int(p.name.split("-")[-1]))
    if resume and (not checkpoints or not (checkpoints[-1] / "trainer_state.json").exists()):
        raise ValueError("no complete Trainer checkpoint available for owned resume")
    args = TrainingArguments(output_dir=str(checkpoint_root),
           num_train_epochs=settings["epochs"], learning_rate=settings["learning_rate"],
           per_device_train_batch_size=settings["per_device_train_batch_size"],
           per_device_eval_batch_size=settings["per_device_eval_batch_size"],
           gradient_accumulation_steps=settings["gradient_accumulation_steps"],
           warmup_steps=settings["warmup_steps"], weight_decay=settings["weight_decay"],
           max_grad_norm=settings["max_grad_norm"], optim=settings["optim"],
           lr_scheduler_type="linear", bf16=config["model"]["compute_dtype"] == "bfloat16",
           fp16=config["model"]["compute_dtype"] == "float16", tf32=False,
           gradient_checkpointing=settings["gradient_checkpointing"],
           gradient_checkpointing_kwargs={"use_reentrant": False},
           eval_strategy="epoch", save_strategy="epoch", save_total_limit=3,
           load_best_model_at_end=True, metric_for_best_model="eval_loss", greater_is_better=False,
           report_to="none", logging_strategy="steps", logging_steps=10,
           seed=seed, data_seed=seed, dataloader_num_workers=0,
           remove_unused_columns=False, label_names=["labels"], use_cache=False, prediction_loss_only=True)
    trainer = Trainer(model=model, args=args, train_dataset=training_set, eval_dataset=validation_set,
                      processing_class=tokenizer, data_collator=AssistantCollator(tokenizer.pad_token_id))
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    training_error = None
    try:
        output = trainer.train(resume_from_checkpoint=str(checkpoints[-1]) if resume else None)
    except BaseException as error:
        training_error = repr(error)
        raise
    finally:
        torch.cuda.synchronize()
        train_wall = time.perf_counter() - t0
        run.result(f"training-attempt-{run.attempt:04d}.json", {"train_wall_seconds": train_wall,
                   "training_error": training_error, "memory": cuda_memory(torch)})
    validation_result = trainer.evaluate()
    memory = cuda_memory(torch)
    adapter = run.path / "adapter"
    adapter.mkdir(exist_ok=False)
    trainer.save_model(str(adapter))
    tokenizer.save_pretrained(adapter)
    run.result("summary.json", {"status": "trained", "kind": "train", "seed": seed, "smoke": smoke_reference,
               "model_files": files, "model": model_info, "train_wall_seconds_this_attempt": train_wall,
               "train_wall_seconds_all_attempts": sum(json.loads(p.read_text())["train_wall_seconds"] for p in run.path.glob("training-attempt-*.json")),
               "train_wall_scope": "Trainer.train including scheduled validation/checkpoint saves/best reload; excludes model load, tokenization and final evaluate/save",
               "resumed": resume, "trainer_metrics": output.metrics, "validation_metrics": validation_result,
               "best_checkpoint": trainer.state.best_model_checkpoint, "best_validation_loss": trainer.state.best_metric,
               "selection": settings["selection"], "log_history": trainer.state.log_history,
               "truncated_train_messages": training_set.truncated_messages,
               "truncated_validation_messages": validation_set.truncated_messages,
               "memory": memory, "determinism": "seeds fixed; deterministic algorithms warn_only=True; inspect captured warnings",
               "adapter_files": artifact_manifest(adapter),
               "checkpoints": artifact_manifest(checkpoint_root)})


def verify_adapter(adapter_run: Path, config, run):
    summary = json.loads((adapter_run / "summary.json").read_text(encoding="utf-8"))
    if summary["status"] != "trained" or summary["seed"] not in config["training"]["seeds"]:
        raise ValueError("adapter requires a completed prescribed training seed")
    for key in ("git_sha", "config_sha256", "protocol_sha256", "source_files", "versions"):
        if summary["provenance"][key] != run.provenance[key]:
            raise ValueError(f"adapter provenance differs: {key}")
    if artifact_manifest(adapter_run / "adapter") != summary["adapter_files"]:
        raise ValueError("adapter file hash mismatch")
    return summary


def evaluate(repo, config, run, adapter_run=None, smoke_run=None):
    import torch
    from riskclf import rules
    assert_cache_roots(config)
    if smoke_run is None:
        raise ValueError("actual NF4 smoke required before evaluation")
    smoke_reference = verify_smoke(smoke_run, run)
    settings = config["evaluation"]
    seed_everything(settings["generation_seed"])
    files = check_model_files(repo, config)
    adapter_summary = verify_adapter(adapter_run, config, run) if adapter_run else None
    rows, manifest = evaluation_data(repo, config)
    model, tokenizer, model_info = load_nf4(config, adapter=adapter_run / "adapter" if adapter_run else None)
    torch.cuda.reset_peak_memory_stats(0)

    def predict(text):
        torch.cuda.synchronize()
        start = time.perf_counter()
        ids, _, truncated = prompt_ids(tokenizer, text, config)
        input_ids = torch.tensor([ids], dtype=torch.long, device="cuda")
        attention = torch.ones_like(input_ids)
        with torch.inference_mode():
            generated = model.generate(input_ids=input_ids, attention_mask=attention,
                        max_new_tokens=settings["max_new_tokens"], do_sample=False, num_beams=1,
                        use_cache=True, pad_token_id=tokenizer.pad_token_id, eos_token_id=tokenizer.eos_token_id)
        raw = tokenizer.decode(generated[0, len(ids):].tolist(), skip_special_tokens=True)
        predicted, failure = parse_binary_json(raw)
        if failure:
            predicted = int(rules.classify(text).label == "scam")
        torch.cuda.synchronize()
        elapsed = (time.perf_counter() - start) * 1000
        return {"prediction": predicted, "raw": raw, "failure": failure,
                "fallback_used": failure is not None, "latency_ms": elapsed,
                "input_tokens": len(ids), "output_tokens": generated.shape[-1] - len(ids), "truncated": truncated}

    with (run.path / "warmup.jsonl").open("xb") as stream:
        for i in range(settings["warmup_messages"]):
            stream.write(json_line({"warmup": i, **predict("Ordinary warmup message; no action required.")}))
    samples, first, failures, changes = [], [], collections.Counter(), []
    with (run.path / "latency.jsonl").open("xb") as stream, (run.path / "predictions.jsonl").open("xb") as prediction_stream:
        for repeat in range(settings["repeats"]):
            for i, row in enumerate(rows):
                record = {"id": row["id"], "y": row["y"], "repeat": repeat, **predict(row["text"])}
                stream.write(json_line(record))
                samples.append(record["latency_ms"])
                if record["failure"]:
                    failures[record["failure"]] += 1
                if repeat == 0:
                    first.append(record)
                    prediction_stream.write(json_line(record))
                elif (record["prediction"], record["failure"]) != (first[i]["prediction"], first[i]["failure"]):
                    changes.append({"id": row["id"], "repeat": repeat, "first_prediction": first[i]["prediction"],
                                    "prediction": record["prediction"], "first_failure": first[i]["failure"], "failure": record["failure"]})
                if (i + 1) % 100 == 0:
                    stream.flush()
                    print(f"repeat {repeat + 1}/{settings['repeats']}: {i + 1}/{len(rows)}", flush=True)
    by_id = {r["id"]: r for r in first}
    result_sets = {}
    for name in ("full1035", "paired400"):
        selected = [by_id[i] for i in manifest["identifiers"][f"{name}_ids"]]
        valid = [r for r in selected if r["failure"] is None]
        result_sets[name] = {**binary_metrics([r["y"] for r in selected], [r["prediction"] for r in selected]),
                             "schema_failures": sum(r["failure"] == "schema" for r in selected),
                             "json_failures": sum(r["failure"] == "invalid_json" for r in selected),
                             "valid_rate": len(valid) / len(selected),
                             "valid_only_metrics": binary_metrics([r["y"] for r in valid], [r["prediction"] for r in valid]) if valid else None}
    run.result("summary.json", {"status": "evaluated", "kind": "evaluation", "model": model_info, "model_files": files,
               "system": "qlora_nf4+rules_on_invalid_json" if adapter_run else "prompt_only_nf4+rules_on_invalid_json",
               "training_seed": adapter_summary["seed"] if adapter_summary else None,
               "adapter_run": str(adapter_run) if adapter_run else None,
               "adapter_summary_sha256": sha256_file(adapter_run / "summary.json") if adapter_run else None,
               "smoke": smoke_reference, "sets": result_sets, "first_repeat_quality_only": True,
               "benchmark": {"warmup_messages": settings["warmup_messages"], "repeats": settings["repeats"],
                 "concurrency": 1, "batch_size": 1, "n_samples": len(samples),
                 "p50_ms": percentile(samples, 0.5), "p95_ms": percentile(samples, 0.95),
                 "quantile_method": "linear interpolation", "timer": "synchronized client wall: prompt tokenize/truncate, host-to-device, local generate, decode, strict JSON/schema parse, optional rules fallback; excludes model load and warmups",
                 "ordering": "repeat-major; frozen full1035 order each repeat; no cross-request KV/prompt cache",
                 "quality_repeat": 0},
               "failure_counts_all_timed_calls": dict(failures), "repeat_disagreements": changes,
               "memory": cuda_memory(torch), "prediction_sha256": sha256_file(run.path / "predictions.jsonl"),
               "latency_sha256": sha256_file(run.path / "latency.jsonl")})


def download_model(repo, config, run):
    from huggingface_hub import snapshot_download
    assert_cache_roots(config, offline=False)
    reference = json.loads((repo / "text-risk/finetune/protocol/qwen_reference.json").read_text(encoding="utf-8"))
    location = Path(config["model"]["local_path"])
    runtime = Path(config["runtime_root"]).resolve()
    if not location.resolve().is_relative_to(runtime / "weights"):
        raise ValueError("download must stay in private runtime weights")
    snapshot_download(repo_id=config["model"]["id"], revision=config["model"]["revision"],
                      local_dir=str(location), allow_patterns=reference["download_files"], token=False)
    records = check_model_files(repo, config)
    run.result("summary.json", {"status": "downloaded_and_hash_verified", "model_files": records,
                               "revision": config["model"]["revision"], "license": config["model"]["license"]})
