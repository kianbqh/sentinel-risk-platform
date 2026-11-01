"""TF-IDF + logistic regression baseline. `python -m riskclf.baseline` trains and saves models/tfidf_lr.joblib."""
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, make_pipeline

MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "tfidf_lr.joblib"


def build():
    feats = FeatureUnion([
        ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2, sublinear_tf=True)),
    ])
    return make_pipeline(feats, LogisticRegression(C=10, class_weight="balanced", max_iter=2000))


def train_and_save():
    from .data import split
    tr, _ = split()
    m = build().fit(tr.text, tr.y)
    MODEL_PATH.parent.mkdir(exist_ok=True)
    joblib.dump(m, MODEL_PATH)
    print(f"saved {MODEL_PATH} (train rows={len(tr)})")
    return m


def load():
    return joblib.load(MODEL_PATH)


if __name__ == "__main__":
    train_and_save()
