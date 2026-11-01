"""Offline evaluation harness.

Compares, on the same held-out messages:
  * rules      - regex fallback (full test set)
  * tfidf_lr   - TF-IDF + logistic regression (full test set)
  * llm:<name> - each LLM endpoint given with --llm name=base_url (stratified subsample, --n-llm)
  * hybrid     - TF-IDF decides confident cases, LLM only for HYBRID_LOW <= p <= HYBRID_HIGH

Every number written to results/ comes from this run. Latency is measured per message,
sequentially, on whatever machine runs the script.
"""
import argparse
import hashlib
import json
import platform
import statistics
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score, accuracy_score

from riskclf import rules
from riskclf import PROMPT_VERSION
from riskclf.baseline import load as load_tfidf
from riskclf.data import SEED, split
from riskclf.llm import LlmClient, LlmError

OUT = Path(__file__).resolve().parents[1] / "results"


def metrics(y, yhat) -> dict:
    return {"n": int(len(y)), "accuracy": round(accuracy_score(y, yhat), 4),
            "precision": round(precision_score(y, yhat, zero_division=0), 4),
            "recall": round(recall_score(y, yhat, zero_division=0), 4),
            "f1": round(f1_score(y, yhat, zero_division=0), 4)}


def lat(ms: list[float]) -> dict:
    a = np.array(ms)
    return {"latency_ms_p50": round(float(np.percentile(a, 50)), 4), "latency_ms_p95": round(float(np.percentile(a, 95)), 4),
            "latency_ms_mean": round(float(a.mean()), 4),
            # sequential wall-clock seconds to classify 1,000 messages on this machine
            "elapsed_seconds_per_1k": round(float(a.mean()), 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="append", default=[], help="name=base_url[,model] (OpenAI-compatible)")
    ap.add_argument("--n-llm", type=int, default=400)
    ap.add_argument("--timeout-s", type=float, default=30.0)
    ap.add_argument("--low", type=float, default=0.05)
    ap.add_argument("--high", type=float, default=0.95)
    ap.add_argument("--tag", default="eval")
    a = ap.parse_args()

    tr, te = split()
    tf = load_tfidf()
    p_full = tf.predict_proba(te.text)[:, 1]
    res = {"dataset": {"name": "UCI SMS Spam Collection (CC BY 4.0), exact-duplicate texts removed",
                       "train": len(tr), "test": len(te), "test_scam": int(te.y.sum())},
           "machine": {"platform": platform.platform(), "processor": platform.processor() or platform.machine()},
           "full_test": {}, "subsample": {}}
    res["full_test"]["tfidf_lr"] = metrics(te.y, (p_full >= 0.5).astype(int)) | {"pr_auc": round(average_precision_score(te.y, p_full), 4)}
    t0 = time.perf_counter(); rp = [rules.classify(t).label == "scam" for t in te.text]
    res["full_test"]["rules"] = metrics(te.y, np.array(rp, dtype=int)) | {"latency_ms_mean": round((time.perf_counter() - t0) * 1000 / len(te), 4)}
    unc_full = (p_full >= a.low) & (p_full <= a.high)
    res["full_test"]["hybrid_llm_call_fraction"] = round(float(unc_full.mean()), 4)

    # stratified subsample for the (slow) LLMs; same rows for every system
    sub = (te.groupby("y").sample(frac=min(1.0, a.n_llm / len(te)), random_state=SEED)
           .sample(frac=1.0, random_state=SEED).reset_index(drop=True))
    y = sub.y.to_numpy()
    ps, tl = [], []
    for t in sub.text:  # per-message latency as the service would see it
        s = time.perf_counter(); ps.append(tf.predict_proba([t])[0, 1]); tl.append((time.perf_counter() - s) * 1000)
    ps = np.array(ps)
    res["subsample"]["n"] = len(sub); res["subsample"]["scam"] = int(y.sum())
    res["subsample"]["text_order_sha256"] = hashlib.sha256(json.dumps(sub.text.tolist(), ensure_ascii=False).encode()).hexdigest()
    res["method"] = {"split_seed": SEED, "prompt_version": PROMPT_VERSION, "temperature": 0,
                     "max_tokens": 80, "quantiles": "numpy percentile, linear interpolation",
                     "elapsed_time_scope": "sequential client wall time; includes local HTTP, decoding and validation",
                     "hybrid_scope": "replayed decisions; latency derived from paired TF-IDF and LLM measurements"}
    res["subsample"]["tfidf_lr"] = metrics(y, (ps >= 0.5).astype(int)) | lat(tl)
    rule_labels, rule_ms = [], []
    for text in sub.text:
        started = time.perf_counter()
        rule_labels.append(rules.classify(text).label == "scam")
        rule_ms.append((time.perf_counter() - started) * 1000)
    res["subsample"]["rules"] = metrics(y, np.array(rule_labels, dtype=int)) | lat(rule_ms)
    preds = sub[["text", "y"]].copy(); preds["tfidf_p"] = ps
    unc = (ps >= a.low) & (ps <= a.high)

    for spec in a.llm:
        name, rest = spec.split("=", 1)
        url, _, model = rest.partition(",")
        cli = LlmClient(base_url=url, model=model or name, timeout_s=a.timeout_s)
        lab, ms, cats, errs, ptok, ctok = [], [], [], {}, [], []
        cli.classify("warm-up message, please ignore")  # load weights / build prompt cache before timing
        for item_number, t in enumerate(sub.text, 1):
            s = time.perf_counter()
            try:
                r = cli.classify(t)
                lab.append(r.verdict.label == "scam"); cats.append(r.verdict.category)
                ptok.append(r.prompt_tokens); ctok.append(r.completion_tokens)
            except LlmError as e:  # production path: fall back to rules
                errs[e.kind] = errs.get(e.kind, 0) + 1
                lab.append(rules.classify(t).label == "scam"); cats.append("fallback")
            ms.append((time.perf_counter() - s) * 1000)
            if item_number % 50 == 0:
                print(f"{name}: {item_number}/{len(sub)} messages", flush=True)
        lab = np.array(lab, dtype=int)
        preds[f"{name}_scam"] = lab; preds[f"{name}_category"] = cats; preds[f"{name}_ms"] = ms
        hyb = np.where(unc, lab, (ps >= 0.5).astype(int))
        hyb_ms = np.where(unc, np.array(ms) + np.array(tl), np.array(tl))
        res["subsample"][f"llm:{name}"] = metrics(y, lab) | lat(ms) | {
            "model": cli.model, "errors_fell_back_to_rules": errs,
            "successful_verdict_rate": round(1 - sum(errs.values()) / len(sub), 4),
            "sampling_seed": cli.seed,
            "prompt_tokens_per_1k": int(sum(ptok) / max(1, len(ptok)) * 1000),
            "completion_tokens_per_1k": int(sum(ctok) / max(1, len(ctok)) * 1000)}
        res["subsample"][f"hybrid:{name}"] = metrics(y, hyb) | lat(list(hyb_ms)) | {
            "llm_call_fraction": round(float(unc.mean()), 4),
            "latency_measurement": "derived from paired LLM + TF-IDF latencies; not a separate routed run"}
        print(name, json.dumps(res["subsample"][f"llm:{name}"]), flush=True)

    OUT.mkdir(exist_ok=True)
    (OUT / f"{a.tag}.json").write_text(json.dumps(res, indent=2))
    preds.to_csv(OUT / f"{a.tag}_predictions.csv", index=False)
    rows = [("full test", k, v) for k, v in res["full_test"].items() if isinstance(v, dict)] + \
           [("subsample", k, v) for k, v in res["subsample"].items() if isinstance(v, dict)]
    md = ["| set | system | n | accuracy | precision | recall | F1 | p50 ms | p95 ms | elapsed s per 1k |", "|---|---|---|---|---|---|---|---|---|---|"]
    for s, k, v in rows:
        md.append(f"| {s} | {k} | {v.get('n','')} | {v['accuracy']} | {v['precision']} | {v['recall']} | {v['f1']} | "
                  f"{v.get('latency_ms_p50','')} | {v.get('latency_ms_p95','')} | {v.get('elapsed_seconds_per_1k','')} |")
    md.extend(["", "Hybrid rows replay paired predictions; their latencies are derived, not a separate routed measurement."])
    (OUT / f"{a.tag}_table.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
