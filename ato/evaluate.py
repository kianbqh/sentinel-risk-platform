"""Train on one synthetic week (seed A, no low-and-slow campaign), test on an independent week (seed B, includes
the unseen low-and-slow campaign). Compare threshold rules, IsolationForest (unsupervised) and gradient boosting.

Metrics at two levels:
  window : (IP, hour) entities; positive if it contains any attack event
  event  : an attack attempt counts as caught if its window is flagged; benign FPR = benign attempts in flagged windows
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest
from sklearn.metrics import average_precision_score

from .datagen import generate
from .features import FEATURES, window_features
from .rules import RULES, apply_rules


def prf(y, p):
    tp = int(((p == 1) & (y == 1)).sum()); fp = int(((p == 1) & (y == 0)).sum()); fn = int(((p == 0) & (y == 1)).sum())
    pr = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": pr, "recall": rc, "f1": 2 * pr * rc / (pr + rc) if pr + rc else 0.0, "tp": tp, "fp": fp, "fn": fn}


def evaluate(f: pd.DataFrame, flag: np.ndarray, name: str) -> dict:
    y = f["label"].to_numpy()
    out = {"method": name, "window": prf(y, flag)}
    w = f["events"].to_numpy()
    att, ben = f["label"] == 1, f["label"] == 0
    out["event_recall"] = float(w[att & flag].sum() / w[att].sum())
    out["benign_event_fpr"] = float(w[ben & flag].sum() / w[ben].sum())
    out["per_kind_event_recall"] = {k: float(w[(f["kind"] == k) & flag].sum() / w[f["kind"] == k].sum())
                                    for k in sorted(f.loc[att, "kind"].unique())}
    nat = ben & f["ip"].str.startswith("172.")
    out["nat_window_fp"] = int((nat & flag).sum()); out["nat_windows"] = int(nat.sum())
    return out


def run(seed_train=1, seed_test=2, out_dir="results"):
    t0 = time.time()
    tr_ev = generate(seed=seed_train, include_low_slow=False)
    te_ev = generate(seed=seed_test, include_low_slow=True)
    tr, te = window_features(tr_ev), window_features(te_ev)
    Xtr, ytr, Xte, yte = tr[FEATURES].to_numpy(), tr["label"].to_numpy(), te[FEATURES].to_numpy(), te["label"].to_numpy()
    res = []
    rule_flag = apply_rules(te).to_numpy()
    res.append(evaluate(te, rule_flag, "rules R1-R4"))
    for rname, r in RULES.items():
        res.append(evaluate(te, r(te).to_numpy(), f"rule {rname}"))
    iso = IsolationForest(n_estimators=200, contamination=float(ytr.mean()), random_state=0).fit(Xtr)
    iso_flag = iso.predict(Xte) == -1
    res.append(evaluate(te, iso_flag, "IsolationForest (unsupervised)"))
    gb = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.1, random_state=0,
                                        class_weight="balanced").fit(Xtr, ytr)
    prob = gb.predict_proba(Xte)[:, 1]
    gb_flag = prob >= 0.5
    r = evaluate(te, gb_flag, "GradientBoosting (supervised)"); r["pr_auc"] = float(average_precision_score(yte, prob))
    res.append(r)
    res.append(evaluate(te, gb_flag | rule_flag, "rules OR GradientBoosting"))
    stats = {
        "train": {"events": len(tr_ev), "attack_events": int(tr_ev["label"].sum()), "windows": len(tr),
                  "attack_windows": int(ytr.sum())},
        "test": {"events": len(te_ev), "attack_events": int(te_ev["label"].sum()), "windows": len(te),
                 "attack_windows": int(yte.sum()), "by_kind": te_ev["kind"].value_counts().to_dict()},
        "seconds": round(time.time() - t0, 1),
    }
    Path(out_dir).mkdir(exist_ok=True)
    Path(out_dir, "detector_metrics.json").write_text(json.dumps({"data": stats, "results": res}, indent=1))
    kinds = sorted(res[0]["per_kind_event_recall"])
    md = ["| method | window precision | window recall | window F1 | attack-event recall | " +
          " | ".join(f"recall {k}" for k in kinds) + " | benign-event FPR | NAT windows flagged |",
          "|---" * (7 + len(kinds)) + "|"]
    for r in res:
        md.append(f"| {r['method']} | {r['window']['precision']:.3f} | {r['window']['recall']:.3f} | "
                  f"{r['window']['f1']:.3f} | {r['event_recall']:.3f} | " +
                  " | ".join(f"{r['per_kind_event_recall'][k]:.3f}" for k in kinds) +
                  f" | {r['benign_event_fpr']:.4f} | {r['nat_window_fp']}/{r['nat_windows']} |")
    md.append(f"\nSynthetic data. Train: {stats['train']}. Test: {stats['test']}. "
              f"GradientBoosting PR-AUC {res[-2]['pr_auc']:.3f}. low_slow campaign absent from training.")
    Path(out_dir, "detector_metrics.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    return res, stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-train", type=int, default=1)
    ap.add_argument("--seed-test", type=int, default=2)
    a = ap.parse_args()
    run(a.seed_train, a.seed_test)
