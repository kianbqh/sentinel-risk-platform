"""Case-level metrics retain model failures separately from guarded delivery."""
import math
from statistics import mean

from copilot.agent.llm import canonical
from copilot.agent.redaction import findings
from copilot.agent.schemas import strict_loads, validate_draft, validate, decision_schema
from .faithfulness import check_draft
from .protocol import call_key


def percentile(samples, q):
    if not samples: return None
    values = sorted(samples)
    position = (len(values)-1)*q
    low, high = math.floor(position), math.ceil(position)
    return values[low]+(values[high]-values[low])*(position-low)


def tool_accuracy(expected, observed):
    wanted = [call_key(row["name"], row["arguments"]) for row in expected]
    actual = [call_key(row["name"], row["arguments"]) for row in observed]
    return {"tool_exact_match": wanted == actual, "tool_set_match": set(wanted) == set(actual),
            "expected_model_calls": expected, "observed_model_calls": observed}


def token_usage(attempts):
    result = {}
    for source, target in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
        values = [row.get("usage", {}).get(source) for row in attempts]
        result[target] = sum(values) if values and all(type(value) is int and value >= 0 for value in values) else None
    return result


def evaluate_case(row, result):
    attempts = result.get("attempts", [])
    raw_draft = None
    for attempt in reversed(attempts):
        try:
            decision = strict_loads(attempt["content"])
            if isinstance(decision, dict) and decision.get("action") == "draft":
                raw_draft = decision.get("draft")
                break
        except (KeyError, ValueError): pass
    raw_schema = False
    if raw_draft is not None:
        try:
            validate_draft(raw_draft, row["case"]["case_id"])
            raw_schema = True
        except Exception: pass
    draft = result["draft"]
    validate_draft(draft, row["case"]["case_id"])
    selected = [{"name": call["name"], "arguments": call["arguments"]} for call in result.get("calls", []) if not call["mandatory"]]
    return {**tool_accuracy(row["expected_model_calls"], selected), **token_usage(attempts),
        "pre_guard_schema_valid": raw_schema, "final_schema_valid": True,
        "first_decision_schema_valid": bool(attempts and attempts[0].get("schema_valid")),
        "decision_attempts": len(attempts), "valid_decision_attempts": sum(bool(item.get("schema_valid")) for item in attempts),
        "schema_retry_count": result.get("schema_retry_count", 0), "schema_failure_count": result.get("schema_failures", 0),
        "pre_guard_pii_leaks": sum(len(item.get("pii_findings", [])) for item in attempts),
        "final_pii_leaks": len(findings(canonical(draft))),
        "pre_guard_faithfulness": check_draft(raw_draft, row["ground_truth"]["allowed_facts"]) if raw_schema else None,
        "final_faithfulness": check_draft(draft, row["ground_truth"]["allowed_facts"]),
        "abstained": draft["abstain"], "expected_abstain": row["ground_truth"]["expected_abstain"],
        "abstain_matches_expectation": draft["abstain"] == row["ground_truth"]["expected_abstain"],
        "guard_reasons": result.get("guard_reasons", []), "handoff_reason": draft["handoff_reason"],
        "mandatory_tool_calls": sum(call["mandatory"] for call in result.get("calls", [])),
        "executed_tool_calls": sum(call["executed"] for call in result.get("calls", [])),
        "requested_tool_calls": len(result.get("calls", [])), "wall_seconds": result["wall_seconds"]}


def summarize(rows):
    complete = [row["metrics"] for row in rows if row["status"] == "complete"]
    def rate(key):
        # Case failure is a failure for all positive-quality rates, not removed.
        return sum(bool(item.get(key)) for item in complete)/len(rows) if rows else None
    def faith_rate(key):
        return sum(bool(item.get(key) and item[key]["faithful"]) for item in complete)/len(rows) if rows else None
    wall = [row["wall_seconds"] for row in rows]
    # A persistence/verification failure must not erase completed raw model
    # outputs from PII, attempt or token accounting.
    failed_attempts = [attempt for row in rows if row["status"] != "complete" for attempt in row.get("raw_completed_model_turns", [])]
    failed_valid = 0
    for attempt in failed_attempts:
        try:
            validate(strict_loads(attempt["content"]), decision_schema()); failed_valid += 1
        except Exception: pass
    failed_pii = sum(len(findings(canonical(attempt.get("raw_response", {}).get("choices", [{}])[0].get("message", {"content": attempt["content"]})))) for attempt in failed_attempts)
    all_usage = complete+[token_usage(row.get("raw_completed_model_turns", [])) for row in rows if row["status"] != "complete"]
    return {"cases": len(rows), "completed_cases": len(complete), "failed_cases": len(rows)-len(complete),
        "tool_exact_accuracy": rate("tool_exact_match"), "tool_set_accuracy": rate("tool_set_match"),
        "pre_guard_schema_valid_rate": rate("pre_guard_schema_valid"), "final_schema_valid_rate": rate("final_schema_valid"),
        "first_decision_schema_valid_rate": rate("first_decision_schema_valid"),
        "pre_guard_lexical_faithful_rate": faith_rate("pre_guard_faithfulness"),
        "final_lexical_faithful_rate": faith_rate("final_faithfulness"),
        "abstain_rate": rate("abstained"), "abstain_expectation_accuracy": rate("abstain_matches_expectation"),
        "pre_guard_pii_leak_count": sum(item["pre_guard_pii_leaks"] for item in complete)+failed_pii,
        "final_pii_leak_count": sum(item["final_pii_leaks"] for item in complete),
        "schema_retry_count": sum(item["schema_retry_count"] for item in complete),
        "decision_schema_valid_rate": (sum(item["valid_decision_attempts"] for item in complete)+failed_valid)/(sum(item["decision_attempts"] for item in complete)+len(failed_attempts)) if sum(item["decision_attempts"] for item in complete)+len(failed_attempts) else None,
        "latency_seconds": {"p50": percentile(wall, .5), "p95": percentile(wall, .95), "raw_samples": wall},
        "tokens_per_case": {key: {"mean": mean([item[key] for item in all_usage if item[key] is not None]) if any(item[key] is not None for item in all_usage) else None,
                                  "measured_cases": sum(item[key] is not None for item in all_usage)} for key in ("input_tokens", "output_tokens")}}
