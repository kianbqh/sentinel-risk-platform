import torch
import torch.nn.functional as F
from torch import nn
from torch_geometric.nn import GATConv, SAGEConv


class SAGE(nn.Module):
    def __init__(self, d_in: int, hidden: int = 128, layers: int = 2, dropout: float = 0.3):
        super().__init__()
        dims = [d_in] + [hidden] * layers
        self.convs = nn.ModuleList(SAGEConv(a, b) for a, b in zip(dims[:-1], dims[1:]))
        self.norms = nn.ModuleList(nn.BatchNorm1d(hidden) for _ in range(layers))
        self.skip = nn.Linear(d_in, hidden)  # keeps the node's own features strong (they matter most here)
        self.head = nn.Linear(hidden, 1)
        self.dropout = dropout

    def forward(self, x, edge_index):
        h = x
        for conv, bn in zip(self.convs, self.norms):
            h = F.dropout(F.relu(bn(conv(h, edge_index))), self.dropout, self.training)
        return self.head(h + self.skip(x)).squeeze(-1)


class GAT(nn.Module):
    def __init__(self, d_in: int, hidden: int = 32, heads: int = 4, dropout: float = 0.3):
        super().__init__()
        self.c1 = GATConv(d_in, hidden, heads=heads, dropout=0.1)
        self.c2 = GATConv(hidden * heads, hidden, heads=1, dropout=0.1)
        self.bn = nn.BatchNorm1d(hidden * heads)
        self.skip = nn.Linear(d_in, hidden)
        self.head = nn.Linear(hidden, 1)
        self.dropout = dropout

    def forward(self, x, edge_index):
        h = F.dropout(F.elu(self.bn(self.c1(x, edge_index))), self.dropout, self.training)
        h = F.elu(self.c2(h, edge_index))
        return self.head(h + self.skip(x)).squeeze(-1)


def train_gnn(model, x, edge_index, y, train_mask, val_mask, epochs=300, lr=5e-3, patience=40, seed=0, log=None,
              return_details=False):
    """Full-batch training with class-weighted BCE; keeps the epoch with the best validation PR-AUC."""
    from sklearn.metrics import average_precision_score
    torch.manual_seed(seed)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=5e-4)
    yt = y.float()
    pos_w = (train_mask & (y == 0)).sum() / (train_mask & (y == 1)).sum().clamp(min=1)
    best, best_state, bad, best_epoch = -1.0, None, 0, 0
    for ep in range(epochs):
        model.train(); opt.zero_grad()
        out = model(x, edge_index)
        loss = F.binary_cross_entropy_with_logits(out[train_mask], yt[train_mask], pos_weight=pos_w)
        loss.backward(); opt.step()
        model.eval()
        with torch.no_grad():
            s = torch.sigmoid(model(x, edge_index))
        ap = average_precision_score(y[val_mask].detach().cpu().numpy(), s[val_mask].detach().cpu().numpy())
        if ap > best:
            best, bad = ap, 0
            best_epoch = ep + 1
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
        if log is not None and ep % 20 == 0:
            log(f"  epoch {ep:3d} loss {loss.item():.4f} val_pr_auc {ap:.4f}")
        if bad >= patience:
            break
    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        scores = torch.sigmoid(model(x, edge_index)).detach().cpu().numpy()
    result = (scores, best, ep + 1)
    if return_details:
        return (*result, {"best_epoch": best_epoch, "epochs": ep + 1, "best_val_pr_auc": float(best)})
    return result
