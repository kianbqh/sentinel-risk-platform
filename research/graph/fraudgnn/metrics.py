import numpy as np
from sklearn.metrics import average_precision_score, f1_score, precision_recall_curve, roc_auc_score


def threshold_max_f1(y, s) -> float:
    p, r, thr = precision_recall_curve(y, s)
    f1 = 2 * p * r / np.maximum(p + r, 1e-12)
    return float(thr[np.argmax(f1[:-1])])


def threshold_at_precision(y, s, target: float) -> float:
    """Lowest threshold whose precision on (y, s) is >= target (falls back to the max-precision threshold)."""
    p, r, thr = precision_recall_curve(y, s)
    ok = np.where(p[:-1] >= target)[0]
    return float(thr[ok[0]] if len(ok) else thr[np.argmax(p[:-1])])


def recall_at_precision(y, s, target: float) -> float:
    """Oracle: best recall achievable at precision >= target on this set."""
    p, r, _ = precision_recall_curve(y, s)
    ok = p >= target
    return float(r[ok].max()) if ok.any() else 0.0


def evaluate(y_val, s_val, y_te, s_te, target_precision: float = 0.9) -> dict:
    t_f1 = threshold_max_f1(y_val, s_val)
    t_p = threshold_at_precision(y_val, s_val, target_precision)
    hat_f1, hat_p = s_te >= t_f1, s_te >= t_p
    tp = lambda h: int((h & (y_te == 1)).sum())
    val_hat_p = s_val >= t_p
    val_precision = float(((y_val == 1) & val_hat_p).sum() / max(1, val_hat_p.sum()))
    return {
        "pr_auc": float(average_precision_score(y_te, s_te)),
        "roc_auc": float(roc_auc_score(y_te, s_te)),
        "illicit_f1_at_val_thr": float(f1_score(y_te, hat_f1)),
        "precision_at_val_p90_thr": float(tp(hat_p) / max(1, hat_p.sum())),
        "recall_at_val_p90_thr": float(tp(hat_p) / max(1, (y_te == 1).sum())),
        "oracle_recall_at_p90": recall_at_precision(y_te, s_te, target_precision),
        "val_f1_threshold": t_f1,
        "val_p90_threshold": t_p,
        "val_p90_achieved_precision": val_precision,
        "val_p90_target_met": bool(val_precision >= target_precision),
    }
