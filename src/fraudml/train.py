"""Train supervised + unsupervised fraud models and track everything in MLflow."""
import json
import time

import joblib
import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .config import (EXPERIMENT, FEATURE_COLS, FEATURES_DIR, LABEL, MLFLOW_URI, MODELS_DIR,
                     REPORTS_DIR, SEED, TARGET_PRECISION)
from .metrics import evaluate, recall_at_precision, threshold_at_precision


def load_splits():
    train = pd.read_parquet(FEATURES_DIR / "train").sort_values("Time").reset_index(drop=True)
    test = pd.read_parquet(FEATURES_DIR / "test").sort_values("Time").reset_index(drop=True)
    # Keep every transaction at the cutoff timestamp in the training set.
    # Validation uses strictly later times for model selection and threshold tuning.
    cutoff = train["Time"].quantile(0.8, interpolation="lower")
    fitting = train.loc[train["Time"] <= cutoff]
    validation = train.loc[train["Time"] > cutoff]
    if fitting.empty or validation.empty:
        raise ValueError("Training data needs distinct timestamps on both sides of the validation cutoff")
    return fitting, validation, test


def supervised_models():
    return {
        "logreg_balanced": make_pipeline(
            StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=2000, random_state=SEED)
        ),
        "hist_gbdt": HistGradientBoostingClassifier(
            learning_rate=0.05, max_iter=400, max_leaf_nodes=31, l2_regularization=1.0,
            early_stopping=True, validation_fraction=0.1, random_state=SEED,
        ),
    }


def run() -> dict:
    t0 = time.perf_counter()
    tr, va, te = load_splits()
    Xtr, ytr = tr[FEATURE_COLS].to_numpy(), tr[LABEL].to_numpy()
    Xva, yva = va[FEATURE_COLS].to_numpy(), va[LABEL].to_numpy()
    Xte, yte = te[FEATURE_COLS].to_numpy(), te[LABEL].to_numpy()

    mlflow.set_tracking_uri(MLFLOW_URI)
    mlflow.set_experiment(EXPERIMENT)
    results = {"data": {"train": len(tr), "val": len(va), "test": len(te),
                        "train_fraud": int(ytr.sum()), "val_fraud": int(yva.sum()), "test_fraud": int(yte.sum())}}

    # ---------- supervised ----------
    best_name, best_val, fitted = None, -1.0, {}
    for name, model in supervised_models().items():
        with mlflow.start_run(run_name=name):
            mlflow.log_params({"model": name, "n_features": len(FEATURE_COLS), "target_precision": TARGET_PRECISION})
            model.fit(Xtr, ytr)
            s_va = model.predict_proba(Xva)[:, 1]
            s_te = model.predict_proba(Xte)[:, 1]
            thr = threshold_at_precision(yva, s_va, TARGET_PRECISION)
            val = evaluate(yva, s_va)
            test = evaluate(yte, s_te, thr)
            test["oracle_recall_at_p90"] = recall_at_precision(yte, s_te, TARGET_PRECISION)
            mlflow.log_metrics({f"val_{k}": v for k, v in val.items()})
            mlflow.log_metrics({f"test_{k}": v for k, v in test.items() if np.isfinite(v)})
            results[name] = {"val": val, "test": test}
            fitted[name] = (model, thr)
            if val["pr_auc"] > best_val:
                best_name, best_val = name, val["pr_auc"]

    # ---------- unsupervised ----------
    with mlflow.start_run(run_name="isolation_forest"):
        iso = make_pipeline(StandardScaler(), IsolationForest(n_estimators=300, contamination="auto",
                                                              random_state=SEED, n_jobs=-1))
        iso.fit(Xtr)  # labels never used
        s_te = -iso.decision_function(Xte)
        iso_res = evaluate(yte, s_te)
        mlflow.log_params({"model": "isolation_forest", "n_estimators": 300})
        mlflow.log_metrics({f"test_{k}": v for k, v in iso_res.items()})
        results["isolation_forest"] = {"test": iso_res}

    with mlflow.start_run(run_name="kmeans_segments"):
        k = 8
        km = make_pipeline(StandardScaler(), KMeans(n_clusters=k, n_init=10, random_state=SEED))
        km.fit(Xtr)
        seg = pd.DataFrame({"cluster": km.predict(Xte), "y": yte})
        prof = seg.groupby("cluster")["y"].agg(["size", "sum", "mean"]).rename(
            columns={"size": "txns", "sum": "fraud", "mean": "fraud_rate"})
        base = yte.mean()
        top = prof["fraud_rate"].idxmax()
        dist = km.transform(Xte).min(axis=1)  # distance to nearest centroid as anomaly score
        km_res = {
            "k": k,
            "base_fraud_rate": float(base),
            "top_cluster_fraud_rate": float(prof.loc[top, "fraud_rate"]),
            "top_cluster_lift": float(prof.loc[top, "fraud_rate"] / base),
            "top_cluster_share_of_txns": float(prof.loc[top, "txns"] / len(seg)),
            "top_cluster_share_of_fraud": float(prof.loc[top, "fraud"] / max(yte.sum(), 1)),
            "centroid_distance_roc_auc": evaluate(yte, dist)["roc_auc"],
        }
        mlflow.log_metrics({k_: v for k_, v in km_res.items()})
        results["kmeans"] = km_res
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        prof.to_csv(REPORTS_DIR / "kmeans_cluster_profile.csv")

    # ---------- package best model ----------
    model, thr = fitted[best_name]
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODELS_DIR / "model.joblib")
    meta = {"model": best_name, "threshold": thr, "features": FEATURE_COLS,
            "target_precision": TARGET_PRECISION, "test_metrics": results[best_name]["test"]}
    (MODELS_DIR / "model_meta.json").write_text(json.dumps(meta, indent=2, allow_nan=False))
    with mlflow.start_run(run_name=f"best_{best_name}"):
        mlflow.log_params({"model": best_name, "threshold": thr})
        mlflow.log_metrics({f"test_{k}": v for k, v in results[best_name]["test"].items()})
        mlflow.sklearn.log_model(model, name="model", input_example=Xte[:5])
        mlflow.log_artifact(str(MODELS_DIR / "model_meta.json"))

    results["best_model"] = best_name
    results["runtime_seconds"] = round(time.perf_counter() - t0, 1)
    (REPORTS_DIR / "metrics.json").write_text(json.dumps(results, indent=2, allow_nan=False))
    print("[train]", json.dumps(results, indent=2))
    return results


if __name__ == "__main__":
    run()
