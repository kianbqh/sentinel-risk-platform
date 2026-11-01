"""Fraud-scoring API: FastAPI service wrapping the scikit-learn model from stripe-fraud-ml.

Endpoints
  GET  /health/live   liveness probe (process is up)
  GET  /health/ready  readiness probe (model loaded and answering)
  POST /score         score one transaction
  POST /score/batch   score up to MAX_BATCH transactions
  GET  /metrics       Prometheus metrics
Config comes from environment variables (12-factor) so the same image runs in Docker, Kubernetes and ECS.
"""
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import joblib
import numpy as np
from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, ConfigDict, Field

MODEL_DIR = Path(os.getenv("MODEL_DIR", Path(__file__).resolve().parents[1] / "model"))
MAX_BATCH = int(os.getenv("MAX_BATCH", "512"))
THRESHOLD_OVERRIDE = os.getenv("THRESHOLD_OVERRIDE")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fraud-scoring")

REQS = Counter("fraud_requests_total", "Scoring requests", ["endpoint", "status"])
LAT = Histogram("fraud_request_seconds", "Request latency", ["endpoint"],
                buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1))
FLAGGED = Counter("fraud_flagged_total", "Transactions flagged as fraud")


class State:
    model = None
    meta: dict = {}
    threshold: float = 0.5


def load_model(model_dir: Path = None) -> None:
    model_dir = Path(model_dir or MODEL_DIR)
    State.model = joblib.load(model_dir / "model.joblib")
    State.meta = json.loads((model_dir / "model_meta.json").read_text())
    State.threshold = float(THRESHOLD_OVERRIDE) if THRESHOLD_OVERRIDE else float(State.meta["threshold"])
    log.info("loaded model=%s features=%d threshold=%.6f", State.meta["model"], len(State.meta["features"]), State.threshold)


@asynccontextmanager
async def lifespan(_app):
    if State.model is None:
        load_model()
    yield


app = FastAPI(title="fraud-scoring", version="1.0.0", lifespan=lifespan)


class Txn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    features: dict[str, float]


class Batch(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    transactions: list[dict[str, float]] = Field(min_length=1)


def _matrix(rows: list[dict]) -> np.ndarray:
    feats = State.meta["features"]
    for i, r in enumerate(rows):
        missing = [f for f in feats if f not in r]
        if missing:
            raise HTTPException(status_code=422, detail=f"row {i}: missing features {missing[:5]}")
    return np.array([[r[f] for f in feats] for r in rows], dtype=float)


@app.middleware("http")
async def timing(request: Request, call_next):
    t0 = time.perf_counter()
    resp = await call_next(request)
    path = request.url.path
    if path.startswith("/score"):
        LAT.labels(path).observe(time.perf_counter() - t0)
        REQS.labels(path, str(resp.status_code)).inc()
    return resp


@app.get("/health/live")
def live():
    return {"status": "alive"}


@app.get("/health/ready")
def ready():
    if State.model is None:
        raise HTTPException(503, "model not loaded")
    State.model.predict_proba(np.zeros((1, len(State.meta["features"]))))  # smoke inference
    return {"status": "ready", "model": State.meta["model"], "threshold": State.threshold}


@app.post("/score")
def score(txn: Txn):
    p = float(State.model.predict_proba(_matrix([txn.features]))[0, 1])
    flagged = p >= State.threshold
    if flagged:
        FLAGGED.inc()
    return {"fraud_score": p, "flagged": flagged, "threshold": State.threshold}


@app.post("/score/batch")
def score_batch(batch: Batch):
    if len(batch.transactions) > MAX_BATCH:
        raise HTTPException(413, f"batch larger than MAX_BATCH={MAX_BATCH}")
    p = State.model.predict_proba(_matrix(batch.transactions))[:, 1]
    flags = p >= State.threshold
    FLAGGED.inc(int(flags.sum()))
    return {"n": len(p), "n_flagged": int(flags.sum()),
            "results": [{"fraud_score": float(s), "flagged": bool(f)} for s, f in zip(p, flags)]}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
