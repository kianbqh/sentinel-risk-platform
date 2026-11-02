"""Shared, credential-free provenance for the local/CI operations runners."""
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def command(args, *, timeout=30, check=True, input_text=None):
    process = subprocess.run(args, cwd=ROOT, text=True, capture_output=True,
                             input=input_text, timeout=timeout, check=False)
    if check and process.returncode:
        # Commands intentionally exclude secret values. Do not dump env or config.
        raise RuntimeError(f"Command {args!r} exited {process.returncode}: {process.stderr[-2000:]}")
    return process


def optional(args):
    try:
        result = command(args, timeout=20, check=False)
        return result.stdout.strip() if result.returncode == 0 else "not available"
    except (OSError, subprocess.TimeoutExpired):
        return "not available"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def provenance(config_path, started, exact_command=None):
    cpu = platform.processor()
    if Path("/proc/cpuinfo").exists():
        cpu = next((line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                    if line.startswith("model name")), cpu)
    sources = sorted((ROOT / "ops").rglob("*.py")) + sorted((ROOT / "ops").rglob("*.sh"))
    git_sha = os.environ.get("SENTINEL_GIT_SHA") or optional(["git", "rev-parse", "HEAD"])
    git_status = optional(["git", "status", "--porcelain"])
    dirty_env = os.environ.get("SENTINEL_GIT_DIRTY")
    dirty = (dirty_env.lower() == "true") if dirty_env is not None else (None if git_status == "not available" else bool(git_status))
    gpu_tool = "/usr/lib/wsl/lib/nvidia-smi" if Path("/usr/lib/wsl/lib/nvidia-smi").exists() else "nvidia-smi"
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha,
        "git_sha_source": "caller supplied from native Git" if os.environ.get("SENTINEL_GIT_SHA") else "git rev-parse HEAD",
        "git_worktree_dirty": dirty,
        "config_path": str(Path(config_path).resolve().relative_to(ROOT)),
        "config_sha256": sha256(config_path),
        "source_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in sources},
        "environment": json.loads(Path(config_path).read_text())["environment"],
        "kernel": platform.platform(), "python": platform.python_version(),
        "cpu_model": cpu, "logical_cpus": os.cpu_count(),
        "gpu_driver_cuda": optional([gpu_tool]),
        "gpu_used": False,
        "docker": optional(["docker", "version", "--format", "{{json .}}"]),
        "package_versions": {"python": platform.python_version(),
                             **{name: importlib.metadata.version(name)
                                for name in ("pytest",) if importlib.util.find_spec(name)}},
        "exact_command": exact_command or " ".join([sys.executable, *sys.argv]),
        "wall_timer_scope": "runner entry through assertions and provenance collection; preceding image build and tool download excluded",
        "wall_seconds": __import__("time").perf_counter() - started,
    }


def write_json(path, report):
    path = checked_output(path)
    # New runs choose a new directory. Refusing overwrite protects recorded runs.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")


def checked_output(path):
    path = Path(path).resolve()
    allowed = (ROOT / "results" / "ops").resolve()
    runtime = (ROOT / ".runtime" / "ops").resolve()
    if not (path.is_relative_to(allowed) or path.is_relative_to(runtime)):
        raise ValueError("Ops outputs must be under results/ops/ or .runtime/ops/")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class RunJournal:
    """Reserve a unique attempt before side effects; flush raw events as they arrive."""
    def __init__(self, output):
        self.output = checked_output(output)
        if self.output.exists():
            raise FileExistsError(f"Recorded output already exists: {self.output}")
        self.path = self.output.with_suffix(".events.jsonl")
        self.stream = self.path.open("x", encoding="utf-8")
        self.events = []
        self.started = __import__("time").perf_counter()

    def record(self, event, **values):
        row = {"timestamp": datetime.now(timezone.utc).isoformat(),
               "elapsed_seconds": __import__("time").perf_counter() - self.started,
               "event": event, **values}
        self.events.append(row)
        self.stream.write(json.dumps(row, allow_nan=False) + "\n")
        self.stream.flush()

    def close(self):
        self.stream.close()
