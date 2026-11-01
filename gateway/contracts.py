"""Versioned alert envelope shared by Python, Spark and the analyst console."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ModelReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)


class Alert(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal["1.0"] = "1.0"
    alert_id: str = Field(min_length=1, max_length=200)
    event_time: datetime
    source: Literal["transaction-stream", "transaction-api", "ato", "text-risk"]
    entity_type: Literal["transaction", "ip", "message"]
    entity_id: str = Field(min_length=1, max_length=200)
    risk_score: float | None = Field(ge=0, le=1)
    threshold: float | None = Field(ge=0, le=1)
    severity: Literal["low", "medium", "high"]
    summary: str = Field(min_length=1, max_length=500)
    model: ModelReference
    evidence: dict[str, Any]
    trace_id: str = Field(min_length=1, max_length=200)

    @field_validator("event_time")
    @classmethod
    def require_timezone(cls, value):
        if value.tzinfo is None:
            raise ValueError("event_time must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_semantics(self):
        expected = {"transaction-stream": "transaction", "transaction-api": "transaction",
                    "ato": "ip", "text-risk": "message"}
        if self.entity_type != expected[self.source]:
            raise ValueError("source and entity_type do not match")
        if self.source == "ato" and self.evidence.get("synthetic") is not True:
            raise ValueError("ATO examples must be marked synthetic")
        if self.source != "text-risk" and (self.risk_score is None or self.threshold is None):
            raise ValueError("numeric models require a score and threshold")
        if self.source == "text-risk":
            if self.evidence.get("score_source") not in ("tfidf_lr", "none"):
                raise ValueError("text score provenance is required")
            if (self.risk_score is None) != (self.evidence["score_source"] == "none"):
                raise ValueError("text score provenance does not match the score")
        return self
