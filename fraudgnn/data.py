"""Elliptic Bitcoin transaction graph (Weber et al., 2019).

203,769 transactions (nodes), 234,355 payment flows (directed edges), 49 time steps (~2 weeks each).
Labels: 4,545 illicit, 42,019 licit, the rest unknown. 166 columns per node: time step,
93 local features, 72 one-hop aggregated features (anonymised by Elliptic).

The raw CSVs are downloaded through PyTorch Geometric's EllipticBitcoinDataset mirror
(https://data.pyg.org/datasets/elliptic, originally the Kaggle "Elliptic Data Set").
They are not redistributed in this repo.
"""
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1] / "data"
RAW = ROOT / "elliptic" / "raw"
CACHE = ROOT / "elliptic_cache.npz"
N_LOCAL = 93
TRAIN_END, VAL_END = 29, 34  # train t<=29, validation 30..34, test 35..49 (standard t<=34 / t>=35 split)


@dataclass
class Graph:
    x: np.ndarray          # [N, 165] float32: 93 local + 72 aggregated
    y: np.ndarray          # [N] int64: 1 illicit, 0 licit, -1 unknown
    t: np.ndarray          # [N] int64 time step 1..49
    edge_index: np.ndarray  # [2, E] int64, directed (source pays to target)

    def split_masks(self):
        lab = self.y >= 0
        return (lab & (self.t <= TRAIN_END), lab & (self.t > TRAIN_END) & (self.t <= VAL_END), lab & (self.t > VAL_END))


def download() -> None:
    if (RAW / "elliptic_txs_features.csv").exists():
        return
    from torch_geometric.datasets import EllipticBitcoinDataset
    EllipticBitcoinDataset(str(ROOT / "elliptic"))


def load() -> Graph:
    if CACHE.exists():
        z = np.load(CACHE)
        return Graph(z["x"], z["y"], z["t"], z["edge_index"])
    download()
    feats = pd.read_csv(RAW / "elliptic_txs_features.csv", header=None)
    classes = pd.read_csv(RAW / "elliptic_txs_classes.csv")
    edges = pd.read_csv(RAW / "elliptic_txs_edgelist.csv")
    ids = feats[0].to_numpy()
    idx = pd.Series(np.arange(len(ids)), index=ids)
    lab = classes.set_index("txId")["class"].reindex(ids).astype(str).map({"1": 1, "2": 0}).fillna(-1)
    g = Graph(
        x=feats.loc[:, 2:].to_numpy(np.float32),
        y=lab.to_numpy(np.int64),
        t=feats[1].to_numpy(np.int64),
        edge_index=np.vstack([idx[edges.txId1].to_numpy(), idx[edges.txId2].to_numpy()]).astype(np.int64),
    )
    np.savez_compressed(CACHE, x=g.x, y=g.y, t=g.t, edge_index=g.edge_index)
    return g


def neighbor_mean(x: np.ndarray, edge_index: np.ndarray) -> np.ndarray:
    """Mean of in+out neighbours' features (zeros for isolated nodes): a graph feature for non-graph models."""
    n = x.shape[0]
    src = np.concatenate([edge_index[0], edge_index[1]])
    dst = np.concatenate([edge_index[1], edge_index[0]])
    out = np.zeros_like(x, dtype=np.float64)
    np.add.at(out, dst, x[src])
    deg = np.bincount(dst, minlength=n).astype(np.float64)
    return (out / np.maximum(deg, 1)[:, None]).astype(np.float32)
