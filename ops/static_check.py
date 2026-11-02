#!/usr/bin/env python3
"""Run the actual validation tools and preserve their full output in a new JSON."""
import argparse
import json
import os
import re
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evidence import ROOT, command, provenance, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "ops/config.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.perf_counter()
    # These values are non-deployed validation fixtures, never live credentials.
    lint_values = ["--set-string", "auth.postgresPassword=lint-only,auth.jwtSecret=lint-only-0000000000000000000000000000"]
    chart = "deploy/helm/sentinel"
    calls = [
        ["promtool", "check", "rules", "ops/prometheus/rules.yml"],
        ["promtool", "test", "rules", "ops/prometheus/rules.test.yml"],
        ["shellcheck", "ops/sentinelctl.sh", "ops/scripts/up.sh", "ops/scripts/kind.sh"],
        ["bats", "ops/tests/sentinelctl.bats"],
        [sys.executable, "-m", "pytest", "--capture=sys", "-q", "ops/tests"],
        ["helm", "lint", chart, *lint_values],
    ]
    report = {"status": "running", "checks": []}
    try:
        for call in calls:
            output = command(call, timeout=180, check=False)
            report["checks"].append({"command": call, "exit_code": output.returncode,
                                     "stdout": output.stdout, "stderr": output.stderr})
            if output.returncode:
                raise RuntimeError(f"Validation failed: {call}")
        rule_text = (ROOT / "ops/prometheus/rules.test.yml").read_text()
        report["alert_rule_test_groups_passed"] = len(re.findall(r"^  - name:", rule_text, re.M))
        report["alert_rule_assertions_passed"] = len(re.findall(r"\balertname:", rule_text))
        for extra in ([], ["--set", "kafka.enabled=false,postgres.persistence.enabled=true", "--set-string", "kafka.bootstrapServers=kafka.example:9092"]):
            rendered = command(["helm", "template", "m1b", chart, *lint_values, *extra])
            result = command(["kubeconform", "-strict", "-summary", "-kubernetes-version", "1.34.0"],
                             input_text=rendered.stdout, timeout=180, check=False)
            report["checks"].append({"command": ["helm", "template", "m1b", chart, "|", "kubeconform", "-strict", "-summary"],
                                     "variant": "bundled Kafka" if not extra else "external Kafka and persistent Postgres",
                                     "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
            if result.returncode:
                raise RuntimeError("Kubeconform rejected rendered chart")
        report["tool_versions"] = {name: command(call, check=False).stdout.strip() for name, call in {
            "helm": ["helm", "version", "--short"], "kubeconform": ["kubeconform", "-v"],
            "promtool": ["promtool", "--version"], "shellcheck": ["shellcheck", "--version"], "bats": ["bats", "--version"]}.items()}
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        report["provenance"] = provenance(args.config, started)
        write_json(args.output, report)


if __name__ == "__main__":
    main()
