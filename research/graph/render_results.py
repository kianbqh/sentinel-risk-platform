"""Render the stored test curves without loading models or training again."""
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

out = Path(__file__).resolve().parent / "results"
curves = json.loads((out / "metrics.json").read_text())["test_recall_by_timestep_seed0"]
fig, ax = plt.subplots(figsize=(9, 4))
for name, curve in curves.items():
    if "[all" in name:
        times = sorted(curve, key=int)
        ax.plot([int(t) for t in times], [curve[t]["recall"] for t in times], marker="o", ms=3, label=name)
ax.axvline(43, color="grey", ls="--", lw=1)
ax.text(43.7, 0.05, "t = 43", fontsize=8, color="grey")
ax.set(xlabel="time step (test period)", ylabel="illicit recall at val max-F1 threshold",
       title="Elliptic test period: recall per time step")
ax.legend(fontsize=7)
ax.grid(alpha=.3)
fig.tight_layout()
fig.savefig(out / "recall_by_timestep.png", dpi=130)
print("Stored curves rendered; no model fit performed.")
