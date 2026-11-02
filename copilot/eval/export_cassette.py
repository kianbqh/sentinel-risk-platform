"""Export per-case compressed cassettes only from a completed real-model run."""
import argparse
import gzip
import json
from pathlib import Path

from copilot.agent.llm import canonical
from copilot.scripts.provenance import RunRecord, sha256
from .protocol import load_protocol
from .model_events import validate_event_record,validate_tool_events


def export(config_path):
    run = RunRecord(config_path)
    try:
        config = run.config
        source = run.root/config["source_run"]
        result = json.loads((source/"result.json").read_text())
        actual_config = json.loads((source/"config.json").read_text())
        if result.get("evaluation_mode") != "real-local-llm-real-http-mcp-postgresql" or result.get("status") != "complete":
            raise ValueError("Only a completed actual model/MCP/PG run can become a cassette")
        protocol_path = run.root/actual_config["runtime_protocol"]
        protocol = load_protocol(protocol_path, actual_config["runtime_protocol_sha256"], runtime=True)
        cases = []
        event_counts = {"actual_model_responses": 0, "recorded_model_errors": 0, "actual_mcp_responses":0, "recorded_mcp_transport_errors":0,
                        "model_invoked_cases":0,"mcp_only_handoff_cases":0}
        for row in protocol["cases"]:
            path = source/f"case-{row['case']['case_id']}.json"
            record = json.loads(path.read_text())
            counts={**validate_event_record(record),**validate_tool_events(record)}
            for key,count in counts.items():event_counts[key]+=count
            event_counts["model_invoked_cases" if record["model_events"] else "mcp_only_handoff_cases"]+=1
            name = f"case-{row['case']['case_id']}.json.gz"
            payload = {"fixture": row, "record": record}
            (run.output/name).write_bytes(gzip.compress(canonical(payload).encode(), mtime=0))
            cases.append({"case_id": row["case"]["case_id"], "file": name, "sha256": sha256(run.output/name), "source_case_sha256": sha256(path)})
        manifest = {"schema_version": "1.0", "mode": "recorded-actual-local-llm-http-mcp-pg",
            "source_result_sha256": sha256(source/"result.json"), "source_git_sha": result["git_sha"],
            "source_config_sha256": result["config_sha256"], "runtime_protocol_sha256": actual_config["runtime_protocol_sha256"],
            "artifacts": result["artifacts"], "agent_config": actual_config["agent"], "cases": cases,
            "event_counts": event_counts,
            "observed_regression_floors": {key: result["metrics"][key] for key in ("tool_exact_accuracy", "tool_set_accuracy", "pre_guard_schema_valid_rate", "final_schema_valid_rate", "first_decision_schema_valid_rate", "pre_guard_lexical_faithful_rate", "final_lexical_faithful_rate")},
            "pii_ceiling": result["metrics"]["final_pii_leak_count"],
            "scope": "Actual model/MCP responses and recorded adapter errors replayed in order in CI. Errors are not responses. MCP-only handoffs did not invoke the LLM. CI does not run the real model; replay wall times are not model latencies."}
        run.write("manifest.json", manifest)
        run.finish({"status": "complete", "cases": len(cases), "event_counts": event_counts, "manifest_sha256": sha256(run.output/"manifest.json"), "source_result_sha256": manifest["source_result_sha256"]})
    except Exception as exc:
        run.fail(type(exc).__name__); raise


if __name__ == "__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--config", required=True)
    export(parser.parse_args().config)
