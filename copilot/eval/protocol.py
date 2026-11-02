"""Validate immutable source/runtime protocols without exposing gold to prompts."""
import hashlib
import json
from pathlib import Path

from copilot.agent.llm import canonical
from copilot.mcp_server.contracts import positive_id, validate_call


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def call_key(name, arguments):
    return canonical({"name": name, "arguments": arguments})


def load_protocol(path, expected_sha256=None, *, runtime=False):
    if expected_sha256 is not None and sha256(path) != expected_sha256:
        raise ValueError("Frozen protocol hash mismatch")
    value = json.loads(Path(path).read_text())
    validate_protocol(value, runtime=runtime)
    return value


def validate_protocol(value, *, runtime=False):
    if value.get("schema_version") != "1.0" or value.get("stage") != ("runtime_frozen" if runtime else "source_frozen"):
        raise ValueError("Protocol version or freeze stage mismatch")
    rows = value.get("cases", [])
    if len(rows) < 100:
        raise ValueError("At least one hundred fixed cases are required")
    identities, sources, counts = set(), set(), {}
    for row in rows:
        case = row["case"]
        identity = positive_id(case["case_id"])
        if identity in identities or row["source_record_id"] in sources:
            raise ValueError("Duplicate fixed case/source identity")
        identities.add(identity); sources.add(row["source_record_id"])
        domain, gold = row["domain"], row["ground_truth"]["label"]
        if domain not in {"card", "ato"} or type(gold) is not int or gold not in (0, 1):
            raise ValueError("Invalid domain or ground-truth label")
        if domain == "ato" and row.get("synthetic") is not True:
            raise ValueError("ATO must remain explicitly synthetic")
        counts[f"{domain}/{gold}"] = counts.get(f"{domain}/{gold}", 0)+1
        for call in row["expected_model_calls"]:
            args = validate_call(call["name"], call["arguments"])
            if "case_id" in args and args["case_id"] != identity:
                raise ValueError("Expected call crosses cases")
        # Neither ground-truth nor expected calls may leak into evidence.
        blocked = {"label", "gold_label", "kind", "expected_model_calls", "ground_truth"}
        def no_gold(item):
            if isinstance(item, dict):
                if blocked.intersection(item): raise ValueError("Gold leaked into case evidence")
                for child in item.values(): no_gold(child)
            elif isinstance(item, list):
                for child in item: no_gold(child)
        no_gold(case)
        if runtime and not isinstance(row.get("frozen_tools"), dict):
            raise ValueError("Runtime protocol lacks actual MCP source facts")
    if set(counts) != {"card/0", "card/1", "ato/0", "ato/1"} or len(set(counts.values())) != 1:
        raise ValueError("Fixed evaluation must be balanced across domain and gold label")
    if not value.get("source_hashes"):
        raise ValueError("Protocol source hashes are required")
    return counts
