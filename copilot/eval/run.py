"""Real local LLM + real HTTP MCP + controlled PostgreSQL draft evaluation."""
import argparse
import asyncio
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import time

from copilot.agent.llm import LocalChatModel
from copilot.agent.workflow import AgentConfig, AnalystCopilot
from copilot.db.persistence import PostgresDraftStore
from copilot.mcp_server.client import MCPToolClient
from copilot.scripts.provenance import RunRecord, sha256, git_output
from .metrics import evaluate_case, summarize
from .protocol import load_protocol
from .recording import TracingTools, TracingStore, TracingModel


def _verify_before_run(config):
    root = Path(config["repo_root"]).resolve()
    if config.get("require_clean_source", True):
        status = git_output(root, ["status", "--porcelain=v1", "--untracked-files=all"], timeout=600, git_binary=config.get("git_binary"))
        if status.strip(): raise ValueError("Commit the reviewed implementation before a formal real-model measurement")
    for key in ("gguf", "llama_server"):
        if sha256(config["artifacts"][key]["path"]) != config["artifacts"][key]["sha256"]:
            raise ValueError("Pinned local model/binary hash mismatch")
    if config["concurrency"] != 1 or config["repeats"] != 1:
        raise ValueError("This baseline protocol fixes concurrency=1 and one fresh case pass")


async def run_async(config_path):
    config = json.loads(Path(config_path).read_text())
    _verify_before_run(config)
    run = RunRecord(config_path)
    rows, warmups = [], []
    try:
        protocol = load_protocol(run.root/config["runtime_protocol"], config["runtime_protocol_sha256"], runtime=True)
        for name in ("langgraph", "jsonschema", "httpx", "uvicorn"):
            try: run.context["packages"][name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError: run.context["packages"][name] = None
        store = PostgresDraftStore(os.environ[config["writer_dsn_env"]])
        async with MCPToolClient(config["mcp_endpoint"], Path(config["mcp_token_file"]).read_text().strip()) as client, LocalChatModel(**config["llm"]) as model:
            # Model metadata is a server read, never a case-data bypass.
            import httpx
            async with httpx.AsyncClient(timeout=10, trust_env=False) as http:
                base = config["llm"]["endpoint"].removesuffix("/v1").rstrip("/")
                props_response = await http.get(base+"/props"); props_response.raise_for_status()
                props = props_response.json()
                run.write("llama-server-props.json", props)
                reported = props.get("model_path")
                if reported is not None and str(reported) != config["artifacts"]["gguf"]["path"]:
                    raise ValueError("Running llama.cpp reports a different model path")
            async def execute(row, *, warmup=False):
                tools, traced_store = TracingTools(client, row["frozen_tools"]), TracingStore(store)
                traced_model = TracingModel(model)
                agent = AnalystCopilot(tools, traced_model, traced_store, AgentConfig(**config["agent"]))
                started = time.perf_counter()
                state = None
                run_id = ("warmup-" if warmup else "eval-")+config["run_id"]+f"-{row['case']['case_id']}"
                try:
                    state = await agent.run(row["case"]["case_id"], run_id=run_id, task=row["task"], persist=not warmup, require_empty_memory=True)
                    if traced_store.errors or (not warmup and not state.get("receipt")):
                        raise RuntimeError("A real database read/write boundary failed or no committed draft receipt was returned")
                    if state.get("error") == "evaluation_memory_not_empty":
                        raise ValueError("Evaluation case memory is not empty")
                    if tools.violations:
                        raise ValueError("Runtime MCP facts changed after protocol freeze")
                    verified = await client.call("get_case", {"case_id": row["case"]["case_id"]})
                    human_fields = ("status", "assignee_id", "updated_at")
                    if not verified["ok"] or any(verified["data"].get(key) != row["case"].get(key) for key in human_fields):
                        raise ValueError("Controlled draft altered protected human case fields")
                    metrics = evaluate_case(row, state)
                    record = {"case_id": row["case"]["case_id"], "domain": row["domain"], "variant": row["variant"], "status": "complete",
                              "run_id": run_id, "wall_seconds": state["wall_seconds"], "metrics": metrics, "draft": state["draft"],
                              "receipt": state["receipt"], "attempts": state["attempts"], "tool_trace": tools.records,
                              "memory_loads": traced_store.loads, "memory_saves": traced_store.saves, "guard_reasons": state["guard_reasons"]}
                    record["human_fields_verification"] = {"method": "separate post-run MCP get_case; outside agent budget and timer", "unchanged": {key: verified["data"][key] for key in human_fields}}
                except Exception as exc:
                    record = {"case_id": row["case"]["case_id"], "domain": row["domain"], "variant": row["variant"], "status": "failed",
                              "run_id": run_id, "wall_seconds": state["wall_seconds"] if state is not None else time.perf_counter()-started, "failure_type": type(exc).__name__,
                              "tool_trace": tools.records, "memory_loads": traced_store.loads, "memory_saves": traced_store.saves}
                record["raw_completed_model_turns"] = traced_model.turns
                record["model_events"] = traced_model.events
                record["store_errors"] = traced_store.errors
                record["source_drift_calls"] = tools.violations
                return record
            for row in protocol["cases"][:config["warmups"]]:
                record = await execute(row, warmup=True)
                warmups.append(record)
                run.write(f"warmup-{record['case_id']}.json", record)
                if record["status"] != "complete": raise ValueError("Warmup failed; raw evidence retained")
            for row in protocol["cases"]:
                record = await execute(row)
                rows.append(record)
                run.write(f"case-{record['case_id']}.json", record)
                print(json.dumps({"case_id": record["case_id"], "status": record["status"], "wall_seconds": record["wall_seconds"]}), flush=True)
        aggregate = summarize(rows)
        run.write("metrics.json", aggregate)
        run.finish({"status": "complete" if not aggregate["failed_cases"] else "completed_with_failures", "evaluation_mode": "real-local-llm-real-http-mcp-postgresql",
            "metrics": aggregate, "artifacts": config["artifacts"], "runtime_protocol_sha256": config["runtime_protocol_sha256"],
            "benchmark_protocol": {"warmups": len(warmups), "repeats": 1, "concurrency": 1,
                "timer": "before LangGraph invocation through mandatory MCP read, PG memory load, all LLM/MCP calls, guards and committed draft persistence; includes schema retries; excludes setup/hash verification, model startup and warmups",
                "percentiles": "linear interpolation at (n-1)*q", "http_retries": 0, "schema_retries_max": config["agent"]["schema_retries"],
                "case_timeout_seconds": config["agent"]["case_timeout_seconds"], "token_source": "actual server usage prompt_tokens/completion_tokens; missing usage remains null",
                "memory": "fresh empty per-case PG memory; warmups do not persist; one measured pass"}})
    except Exception as exc:
        run.fail(type(exc).__name__); raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--config", required=True)
    asyncio.run(run_async(parser.parse_args().config))
