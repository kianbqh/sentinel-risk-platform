"""Exact frozen StandardScaler + LogisticRegression log-odds decomposition."""
import hashlib
import json
import math
from pathlib import Path

from .contracts import FEATURE_NAMES, ToolRejected, finite_features

MODEL_SHA256 = "18e11ae45ff0ec4883851d10c3e0655ebed4c5c15c7b2ecf6b209de6a7f03570"


class ExactExplainer:
    def __init__(self, artifact_path, model_path, *, expected_export_sha256, tolerance=1e-10):
        raw = Path(artifact_path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_export_sha256:
            raise ValueError("Coefficient export hash mismatch")
        if hashlib.sha256(Path(model_path).read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError("Frozen joblib model hash mismatch")
        self.artifact = json.loads(raw)
        self.export_sha256 = expected_export_sha256
        self.tolerance = tolerance
        a = self.artifact
        if tuple(a["features"]) != FEATURE_NAMES or a["model"] != "logreg_balanced":
            raise ValueError("Unsupported coefficient artifact")
        if any(len(a[key]) != 34 for key in ("mean", "scale", "coef")):
            raise ValueError("Incomplete coefficient artifact")
        if any(not math.isfinite(v) for key in ("mean", "scale", "coef") for v in a[key]):
            raise ValueError("Non-finite coefficient artifact")
        if not math.isfinite(a["intercept"]) or any(v <= 0 for v in a["scale"]):
            raise ValueError("Invalid scaling artifact")

    def explain(self, case):
        if case["source"] not in {"transaction-api", "transaction-stream"}:
            raise ToolRejected("unsupported_source", "Exact transaction explanation is unavailable for this source")
        expected_name, expected_version = (("logreg_balanced", MODEL_SHA256) if case["source"] == "transaction-api"
            else ("windowed-logistic-regression", self.export_sha256))
        if case["model_name"] != expected_name or case["model_version"] != expected_version:
            raise ToolRejected("model_version_mismatch", "Case model does not match the frozen coefficient artifact")
        evidence = case.get("evidence")
        if not isinstance(evidence, dict) or not isinstance(evidence.get("features"), dict):
            raise ToolRejected("features_unavailable", "Source evidence does not contain the complete transaction feature vector")
        values = finite_features(evidence["features"])
        a = self.artifact
        contributions = []
        for j, feature in enumerate(FEATURE_NAMES):
            z = (values[feature] - a["mean"][j]) / a["scale"][j]
            contribution = a["coef"][j] * z
            if not math.isfinite(contribution):
                raise ToolRejected("invalid_features", "Feature arithmetic overflow")
            contributions.append({"feature": feature, "value": values[feature], "mean": a["mean"][j],
                "scale": a["scale"][j], "coefficient": a["coef"][j], "standardized_value": z,
                "log_odds_contribution": contribution})
        log_odds = a["intercept"] + math.fsum(row["log_odds_contribution"] for row in contributions)
        p = 1 / (1 + math.exp(-log_odds)) if log_odds >= 0 else math.exp(log_odds) / (1 + math.exp(log_odds))
        stored = case.get("risk_score")
        if type(stored) not in (float, int) or not math.isfinite(stored) or not 0 <= stored <= 1:
            raise ToolRejected("score_unavailable", "Case has no finite stored probability")
        difference = abs(p - stored)
        if difference > self.tolerance:
            raise ToolRejected("evidence_score_mismatch", "Stored score does not match source feature evidence and frozen model")
        return {"case_id": case["case_id"], "method": "exact_standardized_logistic_regression",
            "units": "log_odds", "model_name": case["model_name"], "model_version": case["model_version"],
            "coefficient_artifact_sha256": self.export_sha256,
            "intercept": a["intercept"], "log_odds": log_odds, "probability": p,
            "stored_probability": stored, "max_probability_difference": difference, "contributions": contributions}
