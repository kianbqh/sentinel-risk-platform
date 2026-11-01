from typing import Literal

from pydantic import BaseModel, Field

Label = Literal["scam", "legit"]
Category = Literal["phishing_or_account", "prize_or_lottery", "premium_rate_or_subscription",
                   "marketing", "personal", "other"]


class LlmVerdict(BaseModel):
    """The JSON object the LLM must return. Anything else is rejected and falls back to rules."""
    model_config = {"extra": "forbid"}
    label: Label
    category: Category
    reason: str = Field(min_length=1, max_length=160)


class ClassifyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ClassifyResponse(BaseModel):
    label: Label
    category: Category
    reason: str
    risk_score: float | None = None  # TF-IDF probability when available
    source: Literal["llm", "rules", "tfidf", "cache"]
    model: str
    latency_ms: float


JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": ["scam", "legit"]},
        "category": {"type": "string", "enum": list(Category.__args__)},
        "reason": {"type": "string", "minLength": 1, "maxLength": 160},
    },
    "required": ["label", "category", "reason"],
    "additionalProperties": False,
}
