"""Publish completed-hour synthetic login-abuse signals to the shared alert topic."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import time

from sklearn.ensemble import HistGradientBoostingClassifier

from gateway.contracts import Alert, ModelReference
from gateway.main import Publisher
from .datagen import generate
from .features import FEATURES, window_features
from .evaluate import evaluate


def run(limit=100, output="results/ato/publish.json", prefix="synthetic-ato"):
    started = time.perf_counter()
    train_events = generate(seed=1, include_low_slow=False)
    test_events = generate(seed=2, include_low_slow=True)
    train, test = window_features(train_events), window_features(test_events)
    classifier = HistGradientBoostingClassifier(max_iter=200, learning_rate=.1, random_state=0,
                                                class_weight="balanced")
    classifier.fit(train[FEATURES].to_numpy(), train["label"].to_numpy())
    scores = classifier.predict_proba(test[FEATURES].to_numpy())[:, 1]
    flags = scores >= .5
    metrics = evaluate(test, flags, "GradientBoosting (synthetic completed-hour classification)")
    version = hashlib.sha256(json.dumps({"features": FEATURES, "seed": 1, "iterations": 200,
                                         "sklearn": __import__("sklearn").__version__}, sort_keys=True).encode()).hexdigest()
    publisher = Publisher(os.environ["KAFKA_BOOTSTRAP_SERVERS"])
    events = []
    try:
        for index in test.index[flags][:limit]:
            row = test.loc[index]
            identity = f"{prefix}:{row['ip']}:{int(row['win'])}"
            event = Alert(alert_id=identity, event_time=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=int(row["win"]) + 1),
                          source="ato", entity_type="ip", entity_id=str(row["ip"]),
                          risk_score=float(scores[test.index.get_loc(index)]), threshold=.5, severity="high",
                          summary="Synthetic login-abuse window flagged",
                          model=ModelReference(name="synthetic-login-hgb", version=version),
                          evidence={"synthetic": True, "window": "completed IP-hour", "seed": 2,
                                    "features": {name: float(row[name]) for name in FEATURES}}, trace_id=identity)
            publisher.alert(event)
            events.append(event.model_dump(mode="json"))
    finally:
        publisher.close()
    report = {"synthetic": True, "train_events": len(train_events), "test_events": len(test_events),
              "train_windows": len(train), "test_windows": len(test), "flagged_windows": int(flags.sum()),
              "published": len(events), "seconds": time.perf_counter() - started, "metrics": metrics,
              "topic": os.getenv("ALERTS_TOPIC", "sentinel.alerts.v1"), "events": events}
    path = Path(output); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "events"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output", default="results/ato/publish.json")
    parser.add_argument("--prefix", default="synthetic-ato")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("limit must be positive")
    run(args.limit, args.output, args.prefix)
