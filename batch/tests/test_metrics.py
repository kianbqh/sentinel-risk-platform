import json

import numpy as np
import pytest

from fraudml.metrics import evaluate, recall_at_precision, threshold_at_precision


def test_perfect_scores():
    y = np.array([0, 0, 0, 1, 1])
    s = np.array([0.1, 0.2, 0.3, 0.8, 0.9])
    assert recall_at_precision(y, s, 0.9) == 1.0
    thr = threshold_at_precision(y, s, 0.9)
    m = evaluate(y, s, thr)
    assert m["pr_auc"] == 1.0 and m["recall_at_thr"] == 1.0 and m["precision_at_thr"] == 1.0


def test_unreachable_precision_flags_nothing_and_serialises_metadata():
    y = np.array([1, 0, 1, 0])
    s = np.array([0.1, 0.9, 0.2, 0.8])
    threshold = threshold_at_precision(y, s, 0.99)
    assert np.isfinite(threshold) and threshold > 1.0
    # A future score of exactly 1 must also remain unflagged.
    metrics = evaluate(y, np.array([0.0, 1.0, 0.2, 0.8]), threshold)
    assert metrics["flagged"] == 0
    metadata = {"threshold": threshold, "test_metrics": metrics}
    assert json.loads(json.dumps(metadata, allow_nan=False)) == metadata


@pytest.mark.parametrize("scores", [[-0.1, 0.9], [0.1, 1.1], [0.1, np.nan], [0.1, np.inf]])
def test_threshold_requires_finite_probabilities(scores):
    with pytest.raises(ValueError, match="probabilities"):
        threshold_at_precision([0, 1], scores, 0.9)
