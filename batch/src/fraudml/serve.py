"""Minimal FastAPI real-time scoring service: uvicorn fraudml.serve:app --port 8000"""
import json
from functools import lru_cache

import joblib
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .config import MODELS_DIR

app = FastAPI(title="fraud-scoring")


class Txn(BaseModel):
    features: dict[str, float]


@lru_cache
def _load():
    return joblib.load(MODELS_DIR / "model.joblib"), json.loads((MODELS_DIR / "model_meta.json").read_text())


@app.get("/health")
def health():
    _, meta = _load()
    return {"status": "ok", "model": meta["model"]}


@app.post("/score")
def score(txn: Txn):
    model, meta = _load()
    missing = [f for f in meta["features"] if f not in txn.features]
    if missing:
        raise HTTPException(status_code=422, detail=f"missing features: {missing}")
    x = np.array([[txn.features[f] for f in meta["features"]]])
    p = float(model.predict_proba(x)[0, 1])
    return {"fraud_score": p, "flagged": p >= meta["threshold"], "threshold": meta["threshold"]}
