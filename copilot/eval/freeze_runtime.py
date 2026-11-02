"""Freeze actual MCP tool facts before any model evaluation. No direct DB read."""
import argparse
import asyncio
import copy
from pathlib import Path

from copilot.mcp_server.client import MCPToolClient
from copilot.scripts.provenance import RunRecord, sha256
from .protocol import load_protocol, call_key, validate_protocol


def _same_source(expected, actual):
    # Timestamp/DB serialization is fixed in the runtime freeze. All meaningful
    # source fields and protected human fields must already match the import.
    fields = {"case_id", "alert_id", "status", "assignee_id", "source", "entity_type", "entity_id", "risk_score", "threshold", "severity", "summary", "model_name", "model_version", "evidence", "trace_id"}
    if any(expected.get(key) != actual.get(key) for key in fields):
        raise ValueError("MCP case differs from immutable imported source fixture")


async def run_async(config_path):
    run = RunRecord(config_path)
    try:
        config = run.config
        protocol = copy.deepcopy(load_protocol(run.root/config["source_protocol"], config["source_protocol_sha256"]))
        async with MCPToolClient(config["mcp_endpoint"], Path(config["mcp_token_file"]).read_text().strip()) as tools:
            for row in protocol["cases"]:
                calls = [{"name": "get_case", "arguments": {"case_id": row["case"]["case_id"]}}, *row["expected_model_calls"]]
                frozen = {}
                for call in calls:
                    key = call_key(call["name"], call["arguments"])
                    if key in frozen: continue
                    response = await tools.call(call["name"], call["arguments"])
                    frozen[key] = response
                    if call["name"] == "get_case":
                        if not response["ok"]: raise ValueError("Fixed case unavailable through MCP")
                        _same_source(row["case"], response["data"])
                        row["case"] = response["data"]
                row["frozen_tools"] = frozen
                row["ground_truth"]["allowed_facts"] = [response["data"] for response in frozen.values() if response["ok"]]
        protocol.update(stage="runtime_frozen", source_protocol_sha256=config["source_protocol_sha256"])
        counts = validate_protocol(protocol, runtime=True)
        run.write("runtime_protocol.json", protocol)
        run.finish({"status": "complete", "counts": counts, "protocol_sha256": sha256(run.output/"runtime_protocol.json"), "scope": "actual MCP facts frozen before model evaluation; no LLM and no agent quality measurement"})
    except Exception as exc:
        run.fail(type(exc).__name__); raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--config", required=True)
    asyncio.run(run_async(parser.parse_args().config))
