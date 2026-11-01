import os
import numpy as np
import torch

from fraudgnn.data import TRAIN_END, VAL_END, Graph, neighbor_mean
from fraudgnn.metrics import evaluate, recall_at_precision, threshold_at_precision, threshold_max_f1
from fraudgnn.models import GAT, SAGE, train_gnn


def toy_graph(n=400, seed=0):
    r = np.random.default_rng(seed)
    y = (r.random(n) < 0.2).astype(np.int64)
    x = (r.standard_normal((n, 8)) + y[:, None] * 0.8).astype(np.float32)
    src = r.integers(0, n, 3 * n); dst = r.integers(0, n, 3 * n)
    t = r.integers(1, 50, n)
    return Graph(x, np.where(r.random(n) < 0.1, -1, y), t, np.vstack([src, dst]).astype(np.int64))


def test_temporal_split_masks_are_disjoint_and_ordered():
    g = toy_graph()
    tr, va, te = g.split_masks()
    assert not (tr & va).any() and not (va & te).any() and not (tr & te).any()
    assert g.t[tr].max() <= TRAIN_END < g.t[va].min() and g.t[va].max() <= VAL_END < g.t[te].min()
    assert (g.y[tr | va | te] >= 0).all()  # unknown labels never used for training or scoring


def test_neighbor_mean_matches_loop():
    x = np.arange(12, dtype=np.float32).reshape(4, 3)
    ei = np.array([[0, 1, 2], [1, 2, 0]])  # 0->1, 1->2, 2->0, node 3 isolated
    nm = neighbor_mean(x, ei)
    assert np.allclose(nm[0], (x[1] + x[2]) / 2) and np.allclose(nm[1], (x[0] + x[2]) / 2)
    assert np.allclose(nm[3], 0)


def test_metric_helpers():
    y = np.array([0, 0, 0, 1, 1, 0, 1, 1])
    s = np.array([.1, .2, .3, .35, .8, .4, .9, .95])
    assert recall_at_precision(y, s, 1.0) == 0.75
    assert threshold_at_precision(y, s, 1.0) == 0.8
    assert 0.3 < threshold_max_f1(y, s) <= 0.8
    m = evaluate(y, s, y, s)
    assert m["roc_auc"] > 0.9 and 0 <= m["recall_at_val_p90_thr"] <= 1
    fallback = evaluate(np.array([1, 0]), np.array([.1, .9]), y, s)
    assert not fallback["val_p90_target_met"] and fallback["val_p90_achieved_precision"] == 0.5


def test_gnns_forward_and_train_on_toy_graph():
    g = toy_graph()
    device = torch.device(os.environ.get("DEVICE", "cpu"))
    x = torch.tensor(g.x).to(device); ei = torch.tensor(g.edge_index).to(device); y = torch.tensor(g.y).to(device)
    tr, va, _ = (torch.tensor(m).to(device) for m in g.split_masks())
    for model in (SAGE(8, hidden=16), GAT(8, hidden=8, heads=2)):
        model = model.to(device)
        assert model(x, ei).shape == (len(g.y),)
        s, best_val, epochs, details = train_gnn(model, x, ei, y, tr, va, epochs=30, patience=10, return_details=True)
        assert s.shape == (len(g.y),) and 0 <= s.min() and s.max() <= 1 and best_val > 0.3
        assert 1 <= details["best_epoch"] <= details["epochs"] == epochs
