"""Paired nonparametric sampling uncertainty, separate from training-seed std."""
from __future__ import annotations

import math
import random
import statistics as stats
from fractions import Fraction


def percentile(samples, q):
    if not samples or not 0 <= q <= 1:
        raise ValueError("empty samples or invalid quantile")
    values = sorted(samples)
    position = (len(values) - 1) * q
    lo = math.floor(position)
    hi = math.ceil(position)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def binary_metrics(y, predictions):
    if len(y) != len(predictions) or not y:
        raise ValueError("unaligned or empty predictions")
    if any(type(v) is not int or v not in (0, 1) for v in [*y, *predictions]):
        raise ValueError("binary integer labels required")
    tp = sum(a == b == 1 for a, b in zip(y, predictions))
    fp = sum(a == 0 and b == 1 for a, b in zip(y, predictions))
    fn = sum(a == 1 and b == 0 for a, b in zip(y, predictions))
    tn = len(y) - tp - fp - fn
    return {"n": len(y), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "accuracy": (tp + tn) / len(y), "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0}


def exact_mcnemar(y, reference, candidate):
    binary_metrics(y, reference)
    binary_metrics(y, candidate)
    b = sum(a == r and a != c for a, r, c in zip(y, reference, candidate))
    c = sum(a != r and a == p for a, r, p in zip(y, reference, candidate))
    n = b + c
    p = min(1.0, float(Fraction(2 * sum(math.comb(n, k) for k in range(min(b, c) + 1)), 1 << n))) if n else 1.0
    return {"reference_correct_candidate_wrong": b, "reference_wrong_candidate_correct": c,
            "discordant": n, "p_value_two_sided_exact": p,
            "method": "2 * Binomial(n, 0.5) lower tail at min(b,c), capped at 1; no continuity correction"}


def paired_bootstrap(y, reference, candidate, *, resamples, seed, confidence=0.95):
    """Both F1s use the same sampled row indices; percentile interval, no rounding."""
    binary_metrics(y, reference)
    binary_metrics(y, candidate)
    if resamples < 2 or not 0 < confidence < 1:
        raise ValueError("invalid bootstrap settings")
    # A row's (TP, FP, FN) contribution permits exact F1 without copying texts.
    contribution = lambda a, b: (int(a == b == 1), int(a == 0 and b == 1), int(a == 1 and b == 0))
    pairs = [(contribution(a, r), contribution(a, c)) for a, r, c in zip(y, reference, candidate)]
    rng = random.Random(seed)
    candidate_scores, reference_scores, differences = [], [], []
    for _ in range(resamples):
        rt = rf = rn = ct = cf = cn = 0
        for _ in range(len(y)):
            r, c = pairs[rng.randrange(len(y))]
            rt += r[0]; rf += r[1]; rn += r[2]
            ct += c[0]; cf += c[1]; cn += c[2]
        r_score = 2 * rt / (2 * rt + rf + rn) if 2 * rt + rf + rn else 0.0
        c_score = 2 * ct / (2 * ct + cf + cn) if 2 * ct + cf + cn else 0.0
        reference_scores.append(r_score)
        candidate_scores.append(c_score)
        differences.append(c_score - r_score)
    tail = (1 - confidence) / 2
    interval = lambda values: [percentile(values, tail), percentile(values, 1 - tail)]
    return {"candidate_f1_ci": interval(candidate_scores), "reference_f1_ci": interval(reference_scores),
            "f1_difference_candidate_minus_reference": binary_metrics(y, candidate)["f1"] - binary_metrics(y, reference)["f1"],
            "paired_f1_difference_ci": interval(differences), "resamples": resamples, "seed": seed,
            "confidence_level": confidence, "method": "paired row bootstrap, percentile, linear quantiles",
            "scope": "held-out sampling uncertainty conditional on this trained model, not seed uncertainty"}


def seed_summary(records):
    if len(records) != 3 or {r["seed"] for r in records} != {42, 43, 44}:
        raise ValueError("exactly the three prespecified training seeds are required")
    result = {"n_seeds": 3, "seeds": [42, 43, 44], "std_ddof": 0,
              "scope": "between-training-seed variation, not a confidence interval"}
    for key in ("f1", "accuracy", "precision", "recall"):
        values = [r[key] for r in records]
        result[key] = {"mean": stats.mean(values), "std": stats.pstdev(values), "raw": values}
    return result
