"""Dependency-free strict input contracts shared by client and handlers."""
import math

TOOL_SCOPES = {
    "get_case": "cases:read", "list_case_events": "events:read",
    "score_transaction": "transactions:score", "explain_score": "scores:explain",
    "search_similar_cases": "retrieval:read",
}
FEATURE_NAMES = tuple([f"V{i}" for i in range(1, 29)] + [
    "Amount", "log_amount", "hour_of_day", "txn_count_1h", "amount_ratio_1h", "amount_z_1h"])


class ToolRejected(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def failure(code, message):
    return {"ok": False, "error": {"code": code, "message": message}}


def validate_envelope(value):
    """Require the exact structured success/failure contract on both boundaries."""
    if not isinstance(value, dict) or type(value.get("ok")) is not bool:
        raise ValueError("Tool response must contain a Boolean ok field")
    if value["ok"]:
        if set(value) != {"ok", "data"} or not isinstance(value["data"], dict):
            raise ValueError("Successful tool response must contain exactly ok and object data")
    else:
        error = value.get("error")
        if set(value) != {"ok", "error"} or not isinstance(error, dict) or set(error) != {"code", "message"}:
            raise ValueError("Failed tool response must contain exactly ok and a typed error")
        if any(not isinstance(error[key], str) or not error[key] for key in ("code", "message")):
            raise ValueError("Tool error code/message must be nonempty strings")
    return value


def positive_id(value):
    if type(value) is not int or not 1 <= value <= 9223372036854775807:
        raise ToolRejected("invalid_arguments", "case_id must be a positive integer")
    return value


def finite_features(values):
    if not isinstance(values, dict) or set(values) != set(FEATURE_NAMES):
        raise ToolRejected("invalid_features", "Exactly the 34 frozen model features are required")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values.values()):
        raise ToolRejected("invalid_features", "Feature values must be finite JSON numbers")
    return {key: float(values[key]) for key in FEATURE_NAMES}


def validate_call(name, args):
    if not isinstance(name, str) or name not in TOOL_SCOPES:
        raise ToolRejected("tool_not_allowed", "Tool name is not in the read-only allow-list")
    expected = {"features"} if name == "score_transaction" else {"case_id", "k"} if name == "search_similar_cases" else {"case_id"}
    if not isinstance(args, dict) or set(args) != expected:
        raise ToolRejected("invalid_arguments", "Missing or unknown tool arguments")
    if name == "score_transaction":
        return {"features": finite_features(args["features"])}
    clean = {"case_id": positive_id(args["case_id"])}
    if name == "search_similar_cases":
        if type(args["k"]) is not int or not 1 <= args["k"] <= 20:
            raise ToolRejected("invalid_arguments", "k must be an integer from 1 through 20")
        clean["k"] = args["k"]
    return clean


def tool_schemas():
    case = {"type": "integer", "minimum": 1, "maximum": 9223372036854775807}
    result = {}
    for name in TOOL_SCOPES:
        props = {"case_id": case}
        if name == "score_transaction":
            props = {"features": {"type": "object", "properties": {key: {"type": "number"} for key in FEATURE_NAMES},
                                    "required": list(FEATURE_NAMES), "additionalProperties": False}}
        if name == "search_similar_cases":
            props["k"] = {"type": "integer", "minimum": 1, "maximum": 20}
        result[name] = {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}
    return result
