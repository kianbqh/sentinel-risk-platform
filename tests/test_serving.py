import json

import joblib
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sklearn.linear_model import LogisticRegression

from fraudml import batch_score, serve
from fraudml.config import FEATURE_COLS


@pytest.fixture()
def tiny_model(tmp_path, monkeypatch):
    rng = np.random.default_rng(0)
    X = rng.normal(size=(200, len(FEATURE_COLS)))
    y = (X[:, 0] > 1).astype(int)
    model = LogisticRegression().fit(X, y)
    joblib.dump(model, tmp_path / "model.joblib")
    (tmp_path / "model_meta.json").write_text(json.dumps({"model": "tiny", "threshold": 0.5, "features": FEATURE_COLS}))
    monkeypatch.setattr(serve, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(batch_score, "MODELS_DIR", tmp_path)
    serve._load.cache_clear()
    return X


def test_health_and_score(tiny_model):
    client = TestClient(serve.app)
    assert client.get("/health").json()["status"] == "ok"
    high = dict(zip(FEATURE_COLS, [5.0] + [0.0] * (len(FEATURE_COLS) - 1)))
    r = client.post("/score", json={"features": high}).json()
    assert r["flagged"] is True and 0.0 <= r["fraud_score"] <= 1.0


def test_missing_feature_rejected(tiny_model):
    client = TestClient(serve.app)
    r = client.post("/score", json={"features": {"V1": 1.0}})
    assert r.status_code == 422


def test_batch_score(tiny_model):
    df = pd.DataFrame(tiny_model, columns=FEATURE_COLS)
    out = batch_score.score(df)
    assert len(out) == 200 and out["fraud_score"].between(0, 1).all()


def test_api_and_batch_return_matching_scores_and_flags(tiny_model):
    indices = [tiny_model[:, 0].argmin(), tiny_model[:, 0].argmax()]
    transactions = pd.DataFrame(tiny_model[indices], columns=FEATURE_COLS)
    # Input order should not change the model's feature order in either path.
    transactions = transactions.loc[:, list(reversed(FEATURE_COLS))]
    batch = batch_score.score(transactions)
    client = TestClient(serve.app)

    for index, transaction in transactions.iterrows():
        response = client.post("/score", json={"features": transaction.to_dict()})
        assert response.status_code == 200
        result = response.json()
        assert result["fraud_score"] == pytest.approx(batch.loc[index, "fraud_score"], abs=1e-12)
        assert result["flagged"] is bool(batch.loc[index, "flagged"])
    assert set(batch["flagged"]) == {False, True}
