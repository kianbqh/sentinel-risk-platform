# fraud-gnn

I wanted to see how graph message passing compares with strong tabular baselines on the Elliptic transaction network. This experiment uses a **temporal label split on a fixed full graph (transductive)**. GNN training sees all nodes, features and edges, including the validation/test periods. Supervised loss uses training labels; scalers fit training rows and thresholds use validation labels.

## Data and split

The graph has **203,769 transactions**, **234,355 directed payment edges** and 49 indexed time steps. It contains 4,545 illicit, 42,019 licit and 157,205 unknown-label nodes. The 165 input features comprise 93 local features and 72 precomputed neighborhood aggregates; the `local` variant uses the first 93 feature columns.

| Label split | Time steps | Labeled nodes | Illicit nodes |
|---|---|---|---|
| Train | 1–29 | 26,381 | 2,871 |
| Validation | 30–34 | 3,513 | 591 |
| Test | 35–49 | 16,670 | 1,083 |

Unknown labels are excluded from supervised loss, threshold selection and evaluation. Their features still participate in the full-graph message passing and neighbor means. GNNs use 468,710 undirected message-passing edges.

The raw CSVs came from the PyG-hosted Elliptic mirror. [URLs, file sizes and SHA-256 hashes](results/data_provenance.json) · [loader and feature construction](fraudgnn/data.py).

## Models and protocol

- Logistic regression: train-fitted StandardScaler and class-balanced logistic regression, C=0.1.
- LightGBM: 600 trees, with and without an explicit mean of neighbors' features. Neighbor means use the full undirected graph.
- GraphSAGE: two layers with hidden width 128 and a skip connection.
- GAT: four 32-wide attention heads followed by one head, with a skip connection.

GNNs use full-batch, class-weighted binary cross-entropy and AdamW (learning rate 0.005, weight decay 0.0005). The best validation average precision selects the checkpoint, with at most 300 epochs and patience 40. Each tree/GNN configuration runs seeds 0, 1 and 2; logistic regression has one fit per feature set. This is **26 fits across ten model/feature groups**.

Two thresholds are selected on validation:

1. The threshold maximizing validation F1.
2. A P90 selector: the lowest threshold attaining validation precision ≥90%; when none does, it falls back to the threshold with the highest validation precision.

Each [per-seed record](results/per_seed) saves the actual thresholds, achieved validation precision, target-met flag, metrics, elapsed time and GNN checkpoint/epoch details. For example, `logreg[all]` selected threshold **0.98974**, attaining **71.00%** validation precision, so its P90 target-met flag is false.

## Local results

Measured on 31 October 2026 with an i7-12700KF and an RTX 3080 reporting 12 GB of device memory, under Ubuntu 24.04 / WSL2. The GNN build was **Torch 2.15.0+cu132 / CUDA 13.2 / PyG 2.8.1**. The original four tests passed on CUDA in 31.22 s. [Test output](results/pytest_cuda.log) · [environment](results/runtime_environment.json).

LR and LightGBM run on **CPU**; GraphSAGE and GAT run on **CUDA**. Results are test-set mean ± population standard deviation (`ddof=0`) over the listed seeds. PR-AUC means average precision. F1 uses the validation-F1 threshold; selector recall and precision are measured on the test split at the validation-selected P90/fallback threshold. “Val P90 met” counts seeds that attained the validation target.

| Model / features | Backend | Seeds | PR-AUC | F1 | Recall (selector) | Precision (selector) | Val P90 met | Fit+score s |
|---|---|---|---|---|---|---|---|---|
| `logreg[all]` | CPU | 1 | 0.216 | 0.285 | 0.522 | 0.231 | 0/1 | 0.2 |
| `lightgbm[all]` | CPU | 3 | 0.793 ± 0.001 | 0.769 ± 0.004 | 0.749 ± 0.001 | 0.568 ± 0.019 | 3/3 | 12.4 |
| `lightgbm[all+nbr_mean]` | CPU | 3 | 0.798 ± 0.001 | 0.795 ± 0.004 | 0.750 ± 0.002 | 0.556 ± 0.019 | 3/3 | 18.7 |
| `graphsage[all]` | CUDA | 3 | 0.537 ± 0.023 | 0.512 ± 0.018 | 0.567 ± 0.025 | 0.534 ± 0.006 | 3/3 | 7.2 |
| `gat[all]` | CUDA | 3 | 0.375 ± 0.031 | 0.463 ± 0.029 | 0.539 ± 0.018 | 0.444 ± 0.021 | 3/3 | 10.1 |
| `logreg[local]` | CPU | 1 | 0.145 | 0.338 | 0.746 | 0.221 | 0/1 | 0.2 |
| `lightgbm[local]` | CPU | 3 | 0.774 ± 0.001 | 0.738 ± 0.005 | 0.734 ± 0.001 | 0.599 ± 0.007 | 3/3 | 9.5 |
| `lightgbm[local+nbr_mean]` | CPU | 3 | 0.796 ± 0.002 | 0.773 ± 0.011 | 0.749 ± 0.002 | 0.589 ± 0.015 | 3/3 | 13.3 |
| `graphsage[local]` | CUDA | 3 | 0.390 ± 0.029 | 0.429 ± 0.043 | 0.703 ± 0.029 | 0.381 ± 0.050 | 3/3 | 6.5 |
| `gat[local]` | CUDA | 3 | 0.413 ± 0.013 | 0.378 ± 0.020 | 0.444 ± 0.067 | 0.537 ± 0.029 | 3/3 | 8.5 |

[Full table, including ROC-AUC and oracle recall](results/table.md) · [aggregate metrics and per-step values](results/metrics.json) · [training log](results/train.log).

Fit+score elapsed time includes the fitted model's predictions. Neighbor construction is performed once before the model loops; GNN scaling, tensor preparation and model initialization are also outside the per-fit timers. LR's scaler is part of its timed pipeline. The timer from graph loading through all fitting and scoring was **271.1 s**. CUDA allocator peaks are recorded separately in each GNN seed file.

A few observations:

- LightGBM with neighbor means has the highest PR-AUC in this configuration. With local features, adding the neighbor mean changes PR-AUC from 0.774 to 0.796.
- Feature choice affects the two GNNs differently: GraphSAGE scores 0.537 with all features and 0.390 with local features; GAT scores 0.375 and 0.413 respectively.
- Several models' recall drops late in the test period while their validation-selected thresholds remain fixed. The plot below shows seed 0 with all features; the JSON also includes each time step's illicit-node count.

![Test-period recall at the fixed validation-F1 threshold](results/recall_by_timestep.png)

Oracle recall@P90 uses test labels to find the best recall on the test precision–recall curve, so it is a test-set diagnostic. The graph context and temporal label split define the protocol throughout these results.

## Run

Create a Python 3.12 environment and install a PyTorch build for the chosen device, then install the locked analysis/PyG dependencies. `requirements-lock.txt` leaves PyTorch and its CUDA-provider libraries to that device-specific installation; the measured build is recorded above.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install torch==2.15.0
pip install -r requirements-lock.txt
export DEVICE=cuda N_JOBS=4 LGBM_JOBS=1
pytest --capture=sys -q tests
python train.py --seeds 3 --epochs 300
```

The first load downloads Elliptic through PyG and builds a local NumPy cache. `DEVICE=cpu` selects CPU execution. GitHub Actions explicitly installs the pinned CPU PyTorch wheel and runs the four tests. Raw data and caches stay under the ignored `data/` directory; metrics, per-seed JSON and the plot are written to `results/`.
