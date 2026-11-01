"""Train non-graph baselines and GNNs on the Elliptic graph with a temporal split; write results/.

    python train.py                 # everything, 3 seeds (CPU: see README for runtime)
    python train.py --seeds 1 --quick
    DEVICE=cuda python train.py     # GPU if torch was installed with CUDA
"""
import argparse
import json
import os
import platform
import re
import time
from pathlib import Path

import numpy as np
import torch
from lightgbm import LGBMClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from torch_geometric.utils import to_undirected

from fraudgnn.data import N_LOCAL, load, neighbor_mean
from fraudgnn.metrics import evaluate, threshold_max_f1
from fraudgnn.models import GAT, SAGE, train_gnn

OUT = Path(__file__).resolve().parent / "results"


class Recorder:
    def __init__(self, g, va, te):
        self.g, self.va, self.te = g, va, te
        self.results, self.curves = {}, {}

    def record_run(self, name, feature_set, seed, scores, elapsed, **training):
        metrics = evaluate(self.g.y[self.va], scores[self.va], self.g.y[self.te], scores[self.te])
        record = {"model": name, "feature_set": feature_set, "seed": seed,
                  "fit_score_seconds": elapsed, "best_epoch": None, "epochs": None,
                  **training, "metrics": metrics}
        folder = OUT / "per_seed"
        folder.mkdir(parents=True, exist_ok=True)
        filename = re.sub(r"[^A-Za-z0-9_-]", "_", name) + f"_seed{seed}.json"
        (folder / filename).write_text(json.dumps(record, indent=2) + "\n")
        return (scores, elapsed, record)

    def add(self, name, runs):
        """Aggregate saved per-seed metrics using population standard deviation (ddof=0)."""
        g, va, te = self.g, self.va, self.te
        per = [run[2]["metrics"] for run in runs]
        keys = [key for key in per[0] if key != "val_p90_target_met"]
        m = {k: float(np.mean([p[k] for p in per])) for k in keys}
        if len(runs) > 1:
            m |= {f"{k}_std": float(np.std([p[k] for p in per])) for k in keys}
            m["fit_score_s_std"] = float(np.std([r[1] for r in runs]))
        m["fit_score_s"] = float(np.mean([r[1] for r in runs]))
        m["val_p90_target_met_seeds"] = sum(p["val_p90_target_met"] for p in per)
        m["seeds"] = len(runs)
        self.results[name] = m
        s0 = runs[0][0]
        thr = threshold_max_f1(g.y[va], s0[va])
        yt, st, tt = g.y[te], s0[te], g.t[te]
        self.curves[name] = {int(k): {"illicit": int((yt[tt == k] == 1).sum()),
                                      "recall": round(float(((st[tt == k] >= thr) & (yt[tt == k] == 1)).sum()
                                                            / max(1, (yt[tt == k] == 1).sum())), 4)}
                             for k in np.unique(tt)}
        print(f"{name:30s} PR-AUC {m['pr_auc']:.4f} ROC-AUC {m['roc_auc']:.4f} F1 {m['illicit_f1_at_val_thr']:.4f} "
              f"R@valP90-selector {m['recall_at_val_p90_thr']:.4f} (P {m['precision_at_val_p90_thr']:.4f}) "
              f"{m['fit_score_s']:.1f}s fit+score", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--quick", action="store_true", help="all-features only")
    a = ap.parse_args()
    if a.seeds < 1 or a.epochs < 1:
        ap.error("seeds and epochs must be positive")
    torch.set_num_threads(int(os.getenv("N_JOBS", "4")))
    dev = torch.device(os.getenv("DEVICE", "cuda" if torch.cuda.is_available() else "cpu"))
    t_start = time.perf_counter()
    g = load()
    tr, va, te = g.split_masks()
    info = {"nodes": int(len(g.y)), "edges": int(g.edge_index.shape[1]), "features": int(g.x.shape[1]),
            "protocol": "temporal label split on a fixed full graph (transductive)",
            "requested_arguments": vars(a),
            "labelled": int((g.y >= 0).sum()), "illicit": int((g.y == 1).sum()),
            "train_nodes_t1_29": int(tr.sum()), "val_nodes_t30_34": int(va.sum()), "test_nodes_t35_49": int(te.sum()),
            "train_illicit": int(g.y[tr].sum()), "val_illicit": int(g.y[va].sum()), "test_illicit": int(g.y[te].sum()),
            "device": str(dev), "torch": torch.__version__, "cpu_threads": torch.get_num_threads(),
            "platform": platform.platform()}
    print(json.dumps(info, indent=1), flush=True)
    rec = Recorder(g, va, te)
    feature_sets = {"all": slice(None)} if a.quick else {"all": slice(None), "local": slice(0, N_LOCAL)}
    nm = neighbor_mean(g.x, g.edge_index)
    ei = to_undirected(torch.tensor(g.edge_index)).to(dev)
    info["message_passing_edges"] = int(ei.shape[1])
    y_t = torch.tensor(g.y).to(dev)
    m_tr, m_va = torch.tensor(tr).to(dev), torch.tensor(va).to(dev)

    for fs, sl in feature_sets.items():
        X = g.x[:, sl]
        t0 = time.perf_counter()
        lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000, class_weight="balanced", C=0.1)).fit(X[tr], g.y[tr])
        scores = lr.predict_proba(X)[:, 1]
        rec.add(f"logreg[{fs}]", [rec.record_run(f"logreg[{fs}]", fs, 0, scores, time.perf_counter() - t0)])
        for use_nm in (False, True):
            Xg = np.hstack([X, nm[:, sl]]) if use_nm else X
            runs = []
            for seed in range(a.seeds):
                t0 = time.perf_counter()
                gb = LGBMClassifier(n_estimators=600, learning_rate=0.03, num_leaves=31, subsample=0.8, subsample_freq=1,
                                    colsample_bytree=0.5, random_state=seed, verbose=-1, n_jobs=int(os.getenv("LGBM_JOBS", "1"))).fit(Xg[tr], g.y[tr])
                scores = gb.predict_proba(Xg)[:, 1]
                name = f"lightgbm[{fs}{'+nbr_mean' if use_nm else ''}]"
                runs.append(rec.record_run(name, fs, seed, scores, time.perf_counter() - t0,
                                           trees=int(gb.n_estimators_)))
            rec.add(f"lightgbm[{fs}{'+nbr_mean' if use_nm else ''}]", runs)

        x_t = torch.tensor(StandardScaler().fit(X[tr]).transform(X), dtype=torch.float32).to(dev)
        for arch in ("graphsage", "gat"):
            runs = []
            for seed in range(a.seeds):
                torch.manual_seed(seed)
                model = (SAGE(X.shape[1]) if arch == "graphsage" else GAT(X.shape[1])).to(dev)
                if dev.type == "cuda":
                    torch.cuda.synchronize(dev)
                    torch.cuda.reset_peak_memory_stats(dev)
                t0 = time.perf_counter()
                s, best_val, epochs, details = train_gnn(
                    model, x_t, ei, y_t, m_tr, m_va, epochs=a.epochs, seed=seed,
                    log=lambda message: print(message, flush=True), return_details=True)
                if dev.type == "cuda":
                    torch.cuda.synchronize(dev)
                elapsed = time.perf_counter() - t0
                if dev.type == "cuda":
                    details.update(cuda_peak_allocated_bytes=torch.cuda.max_memory_allocated(dev),
                                   cuda_peak_reserved_bytes=torch.cuda.max_memory_reserved(dev))
                runs.append(rec.record_run(f"{arch}[{fs}]", fs, seed, np.asarray(s), elapsed, **details))
                print(f"  {arch}[{fs}] seed {seed}: best val PR-AUC {best_val:.4f}, {epochs} epochs, {runs[-1][1]:.0f}s", flush=True)
            rec.add(f"{arch}[{fs}]", runs)

    OUT.mkdir(exist_ok=True)
    info["total_runtime_s"] = round(time.perf_counter() - t_start, 1)
    (OUT / "metrics.json").write_text(json.dumps({"data": info, "results": rec.results,
                                                  "test_recall_by_timestep_seed0": rec.curves}, indent=2))
    pm = lambda m, k: f"{m[k]:.3f} ± {m[k + '_std']:.3f}" if k + "_std" in m else f"{m[k]:.3f}"
    lines = ["| model | seeds | test PR-AUC | ROC-AUC | illicit F1 (val thr) | recall @ val-P90 selector | precision there | oracle recall@P90 | val P90 met seeds | fit+score s |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for k, m in rec.results.items():
        lines.append(f"| {k} | {m['seeds']} | {pm(m,'pr_auc')} | {pm(m,'roc_auc')} | {pm(m,'illicit_f1_at_val_thr')} | "
                     f"{pm(m,'recall_at_val_p90_thr')} | {pm(m,'precision_at_val_p90_thr')} | {pm(m,'oracle_recall_at_p90')} | "
                     f"{m['val_p90_target_met_seeds']}/{m['seeds']} | {m['fit_score_s']:.1f} |")
    (OUT / "table.md").write_text("\n".join(lines) + "\n")
    plot(rec.curves)
    print("\n".join(lines))


def plot(curves):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9, 4))
    for name, c in curves.items():
        if "[all" in name:
            ks = sorted(c)
            ax.plot(ks, [c[k]["recall"] for k in ks], marker="o", ms=3, label=name)
    ax.axvline(43.0, color="grey", ls="--", lw=1)
    ax.text(43.7, 0.05, "t = 43", fontsize=8, color="grey")
    ax.set_xlabel("time step (test period)"); ax.set_ylabel("illicit recall at val max-F1 threshold")
    ax.set_title("Elliptic test period: recall per time step"); ax.legend(fontsize=7); ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(OUT / "recall_by_timestep.png", dpi=130)


if __name__ == "__main__":
    main()
