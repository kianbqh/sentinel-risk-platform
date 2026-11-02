"""Replay fixed baselines and compare immutable, matched model evaluations."""
from __future__ import annotations

import collections
import csv
import json
import time
from pathlib import Path

from .artifacts import json_line
from .metrics import binary_metrics, exact_mcnemar, paired_bootstrap, percentile, seed_summary
from .protocol import evaluation_data, sha256_file, text_id


def read_evaluation(path: Path, expected_ids, run):
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    if summary["status"] != "evaluated":
        raise ValueError("evaluation run is incomplete")
    for key in ("git_sha", "config_sha256", "protocol_sha256", "versions", "source_files"):
        if summary["provenance"][key] != run.provenance[key]:
            raise ValueError(f"evaluation provenance mismatch: {key}")
    predictions_path = path / "predictions.jsonl"
    if sha256_file(predictions_path) != summary["prediction_sha256"]:
        raise ValueError("evaluation prediction SHA mismatch")
    if sha256_file(path / "latency.jsonl") != summary["latency_sha256"]:
        raise ValueError("evaluation raw latency SHA mismatch")
    records = [json.loads(line) for line in predictions_path.read_text(encoding="utf-8").splitlines()]
    if [r["id"] for r in records] != expected_ids or any(r["repeat"] != 0 for r in records):
        raise ValueError("evaluation ID order or quality repeat mismatch")
    return records, summary


def import_historical(repo, manifest, config):
    systems, references = {}, []
    paired_ids = manifest["identifiers"]["paired400_ids"]
    paired_labels = manifest["identifiers"]["paired400_labels"]
    artifact_by_path = {r["path"]: r for r in manifest["artifacts"]}
    for size in ("0.5b", "1.5b"):
        relative = f"text-risk/results/eval_qwen{size}_predictions.csv"
        path = repo / relative
        if sha256_file(path) != artifact_by_path[relative]["sha256"]:
            raise ValueError("historical predictions changed")
        with path.open(encoding="utf-8", newline="") as stream:
            records = list(csv.DictReader(stream))
        if [text_id(r["text"]) for r in records] != paired_ids or [int(r["y"]) for r in records] != paired_labels:
            raise ValueError("historical paired identity/label mismatch")
        model_name = f"qwen2.5-{size}-q4"
        llm = [int(r[f"{model_name}_scam"]) for r in records]
        ps = [float(r["tfidf_p"]) for r in records]
        low, high = config["evaluation"]["historical_hybrid_low"], config["evaluation"]["historical_hybrid_high"]
        hybrid = [value if low <= p <= high else int(p >= 0.5) for p, value in zip(ps, llm)]
        metadata_path = repo / f"text-risk/results/eval_qwen{size}.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        for kind, values in (("llm", llm), ("hybrid", hybrid)):
            old_f1 = metadata["subsample"][f"{kind}:{model_name}"]["f1"]
            if round(binary_metrics(paired_labels, values)["f1"], 4) != old_f1:
                raise ValueError("historical hybrid reconstruction disagrees with retained metrics")
            systems[f"historical_{kind}_{model_name}"] = dict(zip(paired_ids, values))
        references.append({"predictions": relative, "predictions_sha256": sha256_file(path),
                           "metadata": metadata_path.relative_to(repo).as_posix(), "metadata_sha256": sha256_file(metadata_path),
                           "original_machine": metadata.get("machine"), "original_method": metadata.get("method"),
                           "scope": "paired400 only; raw historical predictions may already contain rules fallback; historical prompt/runtime differ",
                           "hybrid_thresholds": [low, high], "latency": "retained historical observations, never reclassified as new timing"})
    return systems, references


def tfidf_replay(repo, rows, manifest, config, run):
    import joblib
    settings = config["evaluation"]
    relative = settings["tfidf_path"]
    reference = next(a for a in manifest["artifacts"] if a["path"] == relative)
    if sha256_file(repo / relative) != reference["sha256"]:
        raise ValueError("served TF-IDF artifact changed")
    model = joblib.load(repo / relative)
    probabilities, latencies = [], []
    with (run.path / "tfidf-warmup.jsonl").open("xb") as warmups:
        for i in range(settings["warmup_messages"]):
            start = time.perf_counter()
            p = float(model.predict_proba(["Ordinary warmup message; no action required."])[0, 1])
            warmups.write(json_line({"warmup": i, "probability": p, "latency_ms": (time.perf_counter() - start) * 1000}))
    with (run.path / "tfidf-latency.jsonl").open("xb") as stream:
        for repeat in range(settings["repeats"]):
            for row in rows:
                start = time.perf_counter()
                p = float(model.predict_proba([row["text"]])[0, 1])
                elapsed = (time.perf_counter() - start) * 1000
                stream.write(json_line({"id": row["id"], "repeat": repeat, "latency_ms": elapsed, "probability": p}))
                latencies.append(elapsed)
                if repeat == 0:
                    probabilities.append(p)
    values = {r["id"]: int(p >= settings["tfidf_threshold"]) for r, p in zip(rows, probabilities)}
    probability_map = {r["id"]: p for r, p in zip(rows, probabilities)}
    # Independent exact replay against both immutable CSV probability columns.
    for size in ("0.5b", "1.5b"):
        with (repo / f"text-risk/results/eval_qwen{size}_predictions.csv").open(encoding="utf-8", newline="") as stream:
            maximum = max(abs(probability_map[text_id(r["text"])] - float(r["tfidf_p"])) for r in csv.DictReader(stream))
        if maximum > 1e-12:
            raise ValueError("TF-IDF probability replay differs from frozen paired baseline")
    return values, {"model_sha256": reference["sha256"], "warmup_messages": settings["warmup_messages"],
                    "repeats": settings["repeats"], "concurrency": 1, "batch_size": 1,
                    "n_samples": len(latencies), "p50_ms": percentile(latencies, 0.5), "p95_ms": percentile(latencies, 0.95),
                    "quantile_method": "linear interpolation", "raw_sha256": sha256_file(run.path / "tfidf-latency.jsonl"),
                    "timer": "sequential client wall including one-message vectorization and predict_proba; excludes joblib model load/warmups"}


def compare(repo, config, run, prompt_run: Path, seed_runs: list[Path]):
    rows, manifest = evaluation_data(repo, config)
    full_ids = manifest["identifiers"]["full1035_ids"]
    by_id = {r["id"]: r for r in rows}
    tfidf, tfidf_timing = tfidf_replay(repo, rows, manifest, config, run)
    historical, historical_references = import_historical(repo, manifest, config)
    systems = {"tfidf_lr": tfidf, **historical}
    p_records, p_summary = read_evaluation(prompt_run, full_ids, run)
    if p_summary["training_seed"] is not None or p_summary["system"] != "prompt_only_nf4+rules_on_invalid_json":
        raise ValueError("matched prompt-only run must have no adapter")
    systems["prompt_only_nf4"] = {r["id"]: r["prediction"] for r in p_records}
    inputs = [{"path": str(prompt_run), "summary_sha256": sha256_file(prompt_run / "summary.json"),
               "benchmark": p_summary["benchmark"], "sets": p_summary["sets"]}]
    seen_seeds = set()
    for path in seed_runs:
        records, summary = read_evaluation(path, full_ids, run)
        seed = summary["training_seed"]
        if seed not in (42, 43, 44) or seed in seen_seeds or summary["system"] != "qlora_nf4+rules_on_invalid_json":
            raise ValueError("require exactly one adapter evaluation for each fixed seed")
        seen_seeds.add(seed)
        systems[f"qlora_seed_{seed}"] = {r["id"]: r["prediction"] for r in records}
        inputs.append({"path": str(path), "training_seed": seed, "summary_sha256": sha256_file(path / "summary.json"),
                       "benchmark": summary["benchmark"], "sets": summary["sets"]})
    if seen_seeds != {42, 43, 44}:
        raise ValueError("all three prescribed training seeds must finish")
    settings = config["evaluation"]
    report = {"served_default": "tfidf_lr", "sets": {}, "tfidf_benchmark": tfidf_timing,
              "model_evaluations": inputs, "historical_sources": historical_references,
              "policy": "both matched NF4 systems use strict single-label JSON; existing rules replace invalid/schema outputs; counts and valid-only metrics remain in each input summary",
              "uncertainty": "95% paired row-bootstrap sampling intervals for each model; population std over exactly 3 training seeds reported separately; unadjusted per-comparison exact two-sided McNemar",
              "not_available": {"historical_full1035_llm_or_hybrid_predictions": "not run; retained CSVs contain only paired400"}}
    with (run.path / "comparison-predictions.jsonl").open("xb") as stream:
        for row in rows:
            stream.write(json_line({"id": row["id"], "y": row["y"],
                                   "systems": {name: values[row["id"]] for name, values in systems.items() if row["id"] in values}}))
    for name in ("paired400", "full1035"):
        ids = manifest["identifiers"][f"{name}_ids"]
        y = [by_id[i]["y"] for i in ids]
        reference = [tfidf[i] for i in ids]
        scores = {}
        for system, values in systems.items():
            if not all(i in values for i in ids):
                continue
            predictions = [values[i] for i in ids]
            scores[system] = {**binary_metrics(y, predictions),
                    "sampling_uncertainty": paired_bootstrap(y, reference, predictions,
                        resamples=settings["bootstrap_resamples"], seed=settings["bootstrap_seed"], confidence=settings["confidence_level"]),
                    "mcnemar_vs_tfidf": exact_mcnemar(y, reference, predictions)}
            if system.startswith("qlora_seed_"):
                prompt_values = [systems["prompt_only_nf4"][i] for i in ids]
                scores[system]["vs_matched_prompt_only"] = {
                    "sampling_uncertainty": paired_bootstrap(y, prompt_values, predictions,
                       resamples=settings["bootstrap_resamples"], seed=settings["bootstrap_seed"], confidence=settings["confidence_level"]),
                    "mcnemar": exact_mcnemar(y, prompt_values, predictions)}
        seeds = [{"seed": seed, **scores[f"qlora_seed_{seed}"]} for seed in (42, 43, 44)]
        report["sets"][name] = {"scores": scores, "qlora_training_seed_variation": seed_summary(seeds)}
    report["predictions_sha256"] = sha256_file(run.path / "comparison-predictions.jsonl")
    run.result("summary.json", {"status": "compared", "kind": "comparison", **report})
    lines = ["# SMS QLoRA comparison", "", "These values were generated by the local comparison run. TF-IDF remains the served default.", "",
             "| set | system | F1 | 95% sampling CI | paired F1 difference vs TF-IDF | difference CI | exact McNemar p |",
             "|---|---|---|---|---|---|---|"]
    for set_name, result in report["sets"].items():
        for system, values in result["scores"].items():
            uncertainty = values["sampling_uncertainty"]
            lines.append(f"| {set_name} | {system} | {values['f1']} | {uncertainty['candidate_f1_ci']} | {uncertainty['f1_difference_candidate_minus_reference']} | {uncertainty['paired_f1_difference_ci']} | {values['mcnemar_vs_tfidf']['p_value_two_sided_exact']} |")
        variation = result["qlora_training_seed_variation"]["f1"]
        lines.extend(["", f"{set_name} QLoRA F1 across training seeds 42/43/44: {variation['mean']} ± {variation['std']} (population std, ddof=0).", ""])
    lines.extend(["", "Sampling intervals condition on the fitted model. Seed std measures training variation. Neither is substituted for the other.",
                  "NF4 rows include the documented rules fallback on JSON/schema failure; per-run summaries retain both failure counts and valid-only scores.",
                  "Historical GGUF rows are limited to the paired400 messages and use their original prompt/runtime; full-test predictions are unavailable.",
                  "All p-values are unadjusted per-comparison tests. Inspect the effect intervals rather than selecting a model by p-value.",
                  "", "## Limitations", "", "One small public SMS dataset and one frozen split; local single-GPU training; no deployment-quality or broader-domain claim follows.", ""])
    with (run.path / "table.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))
