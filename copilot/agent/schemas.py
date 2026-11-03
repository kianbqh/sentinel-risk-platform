"""Closed JSON Schemas and strict parsing for model decisions and drafts."""
import json
import math

from copilot.mcp_server.contracts import TOOL_SCOPES, tool_schemas, positive_id

REASONS = ["none", "low_confidence", "insufficient_evidence", "invalid_model_output", "budget_exhausted", "pii_output", "unfaithful_output", "tool_error", "memory_error", "runtime_error"]
DRAFT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object", "additionalProperties": False,
    "properties": {
        "schema_version": {"const": "1.0"}, "case_id": {"type": "integer", "minimum": 1, "maximum": 9223372036854775807},
        "risk_level": {"enum": ["low", "medium", "high", "unknown"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "summary": {"type": "string", "minLength": 1, "maxLength": 1800},
        "key_evidence": {"type": "array", "maxItems": 8, "items": {"type": "object", "additionalProperties": False,
            "properties": {"source": {"enum": list(TOOL_SCOPES)}, "fact": {"type": "string", "minLength": 1, "maxLength": 600}}, "required": ["source", "fact"]}},
        "recommended_next_step": {"enum": ["review_evidence", "request_more_evidence", "human_review"]},
        "abstain": {"type": "boolean"}, "handoff_reason": {"enum": REASONS}},
    "required": ["schema_version", "case_id", "risk_level", "confidence", "summary", "key_evidence", "recommended_next_step", "abstain", "handoff_reason"]}


def decision_schema():
    branches = [{"type": "object", "additionalProperties": False,
                 "properties": {"action": {"const": "draft"}, "draft": DRAFT_SCHEMA}, "required": ["action", "draft"]}]
    for name, schema in tool_schemas().items():
        branches.append({"type": "object", "additionalProperties": False,
            "properties": {"action": {"const": "tool"}, "tool_name": {"const": name}, "arguments": schema},
            "required": ["action", "tool_name", "arguments"]})
    return {"type": "object", "oneOf": branches}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def strict_loads(text):
    if not isinstance(text, str) or len(text.encode("utf-8")) > 65536:
        raise ValueError("Model output is missing or too large")
    def reject_constant(_):
        raise ValueError("Non-finite JSON constant")
    result = json.loads(text, object_pairs_hook=_pairs, parse_constant=reject_constant)
    def check(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Non-finite JSON number")
        if isinstance(value, dict):
            for item in value.values(): check(item)
        if isinstance(value, list):
            for item in value: check(item)
    check(result)
    return result


def validate(instance, schema):
    from jsonschema import Draft202012Validator
    Draft202012Validator(schema).validate(instance)
    return instance


def validate_draft(draft, case_id):
    validate(draft, DRAFT_SCHEMA)
    positive_id(draft["case_id"])
    if draft["case_id"] != case_id:
        raise ValueError("Draft case binding mismatch")
    return draft


def fallback(case_id, reason):
    value = {"schema_version": "1.0", "case_id": case_id, "risk_level": "unknown", "confidence": 0.0,
        "summary": "The available evidence could not support a reliable automated draft. A human analyst should review the case.",
        "key_evidence": [], "recommended_next_step": "human_review", "abstain": True, "handoff_reason": reason}
    return validate_draft(value, case_id)
