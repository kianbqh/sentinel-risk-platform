"""Keep scanner findings separate from tool/configuration failures."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
BIN = Path(os.environ.get("BIN", Path(sys.executable).parent)).resolve()
RESULTS.mkdir(exist_ok=True)
records = []
for target in ("vulnerable", "fixed"):
    commands = {
        "bandit": [str(BIN / "bandit"), "-q", "-f", "json", "-o", f"results/bandit_{target}.json", f"app/{target}.py"],
        "semgrep": [str(BIN / "semgrep"), "scan", "--quiet", "--metrics=off", "--json", "-o", f"results/semgrep_{target}.json",
                    "--config", "p/python", "--config", "p/flask", "--config", "p/secrets", "--config", "scan/semgrep_local.yml", f"app/{target}.py"],
    }
    for tool, command in commands.items():
        record = {"tool": tool, "target": target}
        records.append(record)
        started = time.monotonic()
        try:
            output = RESULTS / f"{tool}_{target}.json"
            output.unlink(missing_ok=True)
            with open(RESULTS / f"{tool}_{target}.log", "w") as log:
                result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            record.update(returncode=result.returncode, wall_seconds=round(time.monotonic() - started, 3))
            if result.returncode not in (0, 1):
                raise RuntimeError(f"{tool} failed with return code {result.returncode}")
            parsed = json.loads(output.read_text())
            if not isinstance(parsed.get("results"), list) or parsed.get("errors"):
                raise RuntimeError(f"{tool} report missing results or contains scanner errors")
            if tool == "semgrep" and not any(Path(path).name == f"{target}.py" for path in parsed.get("paths", {}).get("scanned", [])):
                raise RuntimeError("Semgrep did not scan the requested target")
            record.update(json_parsed=True, findings=len(parsed["results"]))
        except Exception as error:
            record["failure"] = str(error)
            raise
        finally:
            (RESULTS / "sast_run_manifest.json").write_text(json.dumps(records, indent=2) + "\n")
subprocess.run([sys.executable, "scan/sast_summary.py", "results"], cwd=ROOT, check=True)
