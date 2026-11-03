"""Replay actual model responses without a model server, database or network."""
import argparse
import asyncio
import gzip
import hashlib
import json
from pathlib import Path

from copilot.agent.llm import RecordedChatModel, canonical
from copilot.agent.workflow import AnalystCopilot, AgentConfig
from copilot.scripts.provenance import RunRecord, sha256
from .metrics import evaluate_case, summarize
from .recording import RecordedTools, RecordedStore


async def replay_async(config_path):
    run = RunRecord(config_path)
    try:
        manifest_path = run.root/run.config["cassette_manifest"]
        if sha256(manifest_path) != run.config["cassette_manifest_sha256"]:
            raise ValueError("Recorded baseline manifest hash mismatch")
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("mode") != "recorded-actual-local-llm-http-mcp-pg" or len(manifest["cases"]) < 100:
            raise ValueError("CI requires an attributed actual-model cassette with at least one hundred cases")
        rows = []
        for entry in manifest["cases"]:
            path = (manifest_path.parent/entry["file"]).resolve()
            if path.parent != manifest_path.parent.resolve() or sha256(path) != entry["sha256"]:
                raise ValueError("Recorded case path/hash mismatch")
            payload = json.loads(gzip.decompress(path.read_bytes()))
            fixture, original = payload["fixture"], payload["record"]
            model = RecordedChatModel(events=original["model_events"])
            tools = RecordedTools(original["tool_trace"])
            store = RecordedStore(original["memory_loads"], original["memory_saves"])
            agent = AnalystCopilot(tools, model, store, AgentConfig(**manifest["agent_config"]))
            state = await agent.run(entry["case_id"], run_id=original["run_id"], task=fixture["task"], require_empty_memory=True)
            metrics = evaluate_case(fixture, state)
            expected = {key:value for key,value in original["metrics"].items() if key != "wall_seconds"}
            observed = {key:value for key,value in metrics.items() if key != "wall_seconds"}
            if canonical(expected) != canonical(observed) or canonical(original["draft"]) != canonical(state["draft"]):
                raise AssertionError(f"Recorded case {entry['case_id']} guard or metric regression")
            if model.violations or tools.violations or store.violations or model.position != len(model.events) or tools.position != len(tools.records) or store.load_position != len(store.loads) or store.save_position != len(store.saves):
                raise AssertionError("Recorded input was not consumed exactly")
            rows.append({"status": "complete", "metrics": metrics, "wall_seconds": state["wall_seconds"]})
        aggregate = summarize(rows)
        for metric, floor in manifest["observed_regression_floors"].items():
            if aggregate[metric] < floor: raise AssertionError("Observed real-baseline regression floor failed: "+metric)
        if aggregate["final_pii_leak_count"] > manifest["pii_ceiling"]:
            raise AssertionError("Recorded baseline PII ceiling failed")
        # Remove replay timing from published quality summary so it cannot be
        # confused with the original real-model end-to-end latency.
        aggregate.pop("latency_seconds")
        run.finish({"status": "complete", "evaluation_mode": "deterministic-recorded-replay-no-live-model",
                    "metrics": aggregate, "source_result_sha256": manifest["source_result_sha256"],
                    "source_event_counts": manifest["event_counts"],
                    "model_latency": "not measured in replay; use the original actual-model run"})
    except Exception as exc:
        run.fail(type(exc).__name__); raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--config", required=True)
    asyncio.run(replay_async(parser.parse_args().config))
