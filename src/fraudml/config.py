from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RAW_PATH = DATA_DIR / "creditcard.parquet"
FEATURES_DIR = DATA_DIR / "features"
MODELS_DIR = ROOT / "models"
REPORTS_DIR = ROOT / "reports"
MLFLOW_URI = f"sqlite:///{ROOT / 'mlflow.db'}"
EXPERIMENT = "stripe-fraud-ml"

# OpenML dataset 1597 ("creditcard", ULB/Worldline, public, no login required)
DATASET_URL = "https://data.openml.org/datasets/0000/1597/dataset_1597.pq"

LABEL = "label"
PCA_COLS = [f"V{i}" for i in range(1, 29)]
ENGINEERED_COLS = ["log_amount", "hour_of_day", "txn_count_1h", "amount_ratio_1h", "amount_z_1h"]
FEATURE_COLS = PCA_COLS + ["Amount"] + ENGINEERED_COLS
TARGET_PRECISION = 0.90
SEED = 42
