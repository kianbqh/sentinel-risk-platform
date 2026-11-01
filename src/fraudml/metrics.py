import numpy as np
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score


def threshold_at_precision(y_true, scores, target_precision: float) -> float:
    """Maximise recall at target precision for probabilities in [0, 1].

    An unreachable target returns the next float above 1, flagging no
    probabilities while remaining serialisable in model metadata and API JSON.
    """
    scores = np.asarray(scores)
    if not np.all(np.isfinite(scores) & (scores >= 0) & (scores <= 1)):
        raise ValueError("scores must be finite probabilities in [0, 1]")
    precision, _, thresholds = precision_recall_curve(y_true, scores)
    ok = np.where(precision[:-1] >= target_precision)[0]
    if len(ok) == 0:
        return float(np.nextafter(1.0, np.inf))
    return float(thresholds[ok[0]])


def recall_at_precision(y_true, scores, target_precision: float) -> float:
    precision, recall, _ = precision_recall_curve(y_true, scores)
    ok = precision >= target_precision
    return float(recall[ok].max()) if ok.any() else 0.0


def evaluate(y_true, scores, threshold: float | None = None) -> dict:
    y_true = np.asarray(y_true)
    scores = np.asarray(scores)
    out = {
        "pr_auc": float(average_precision_score(y_true, scores)),
        "roc_auc": float(roc_auc_score(y_true, scores)),
    }
    if threshold is not None:
        pred = scores >= threshold
        tp = int((pred & (y_true == 1)).sum())
        out.update(
            threshold=float(threshold),
            precision_at_thr=tp / max(int(pred.sum()), 1),
            recall_at_thr=tp / max(int((y_true == 1).sum()), 1),
            flagged=int(pred.sum()),
        )
    return out
