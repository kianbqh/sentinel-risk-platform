"""Owned, exclusive run journals with source and machine provenance."""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.metadata
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .protocol import sha256_file

PACKAGES = ("torch", "transformers", "peft", "bitsandbytes", "accelerate", "numpy",
            "scikit-learn", "joblib", "pydantic", "safetensors", "huggingface-hub")
SELECTED_SOURCE_PATHS = ("text-risk/finetune", "text-risk/riskclf",
                         "text-risk/data/SMSSpamCollection", "text-risk/models/tfidf_lr.joblib")


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def json_line(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")


def exclusive_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never truncate an old file, even if the process is interrupted.
    with path.open("xb") as stream:
        stream.write(json_bytes(value))


def run_command(args, cwd=None, env=None, timeout_s=120):
    try:
        process = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout_s, check=False)
        return {"argv": list(map(str, args)), "command": shlex.join(map(str, args)), "returncode": process.returncode,
                "stdout": process.stdout.strip(), "stderr": process.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"argv": list(map(str, args)), "command": shlex.join(map(str, args)), "error": str(error)}


def git_snapshot(repo: Path):
    """Read-only snapshot via explicit task Git or available native Git.

    A Windows .exe invoked from WSL gets a Windows repository argument through
    wslpath. No shell interpolation, installation, configuration write or index
    refresh is performed. A task-private Linux Git needs no Windows interop.
    """
    configured = os.environ.get("FINETUNE_GIT")
    executable = configured or shutil.which("git")
    if not executable:
        raise RuntimeError("Git unavailable: set FINETUNE_GIT to the task-private Git executable; see README")
    if os.name != "nt" and len(executable) > 2 and executable[1] == ":":
        translated = run_command(["wslpath", "-u", executable])
        if translated.get("returncode") != 0:
            raise RuntimeError("cannot translate configured Windows Git path")
        executable = translated["stdout"]
    found = shutil.which(executable)
    path = Path(found or executable)
    if not path.is_file():
        raise RuntimeError(f"configured Git executable unavailable: {executable}")
    executable = str(path.resolve())
    windows_backend = executable.lower().endswith(".exe")
    repository_argument = str(repo.resolve())
    if windows_backend and os.name != "nt":
        translated = run_command(["wslpath", "-w", repository_argument])
        if translated.get("returncode") != 0 or not translated["stdout"]:
            raise RuntimeError("cannot translate repository path for native Windows Git")
        repository_argument = translated["stdout"]
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    prefix = [executable, "--no-optional-locks", "-C", repository_argument]
    version = run_command([executable, "--version"], env=env)
    before = run_command([*prefix, "rev-parse", "HEAD"], env=env)
    # Cold DrvFs metadata scans can be slow; this occurs before a measured run
    # and never contributes to the training/inference timer.
    selected = run_command([*prefix, "status", "--porcelain=v1", "--untracked-files=normal", "--", *SELECTED_SOURCE_PATHS], env=env, timeout_s=600)
    whole = run_command([*prefix, "status", "--porcelain=v1", "--untracked-files=normal"], env=env, timeout_s=600)
    after = run_command([*prefix, "rev-parse", "HEAD"], env=env)
    if any(r.get("returncode") != 0 for r in (version, before, selected, whole, after)):
        raise RuntimeError(f"read-only Git snapshot failed: {json.dumps([version, before, selected, whole, after])}")
    if before["stdout"] != after["stdout"] or len(before["stdout"]) != 40:
        raise RuntimeError("repository HEAD changed while collecting provenance")
    return {"backend": "native_windows_via_wslpath" if windows_backend and os.name != "nt" else
                       "native_windows" if windows_backend else "linux_explicit_task_git" if configured else "linux_git_on_path",
            "configured_executable": configured, "executable": executable,
            "executable_sha256": sha256_file(path), "version": version, "repository_argument": repository_argument,
            "read_only_flags": {"argv": "--no-optional-locks", "GIT_OPTIONAL_LOCKS": "0"},
            "head_before": before, "head_after": after, "git_sha": before["stdout"],
            "selected_source_scope": list(SELECTED_SOURCE_PATHS), "selected_source_status": selected,
            "selected_source_dirty": bool(selected["stdout"]), "whole_worktree_status": whole,
            "whole_worktree_dirty": bool(whole["stdout"])}


def load_config(path: Path):
    config = json.loads(path.read_text(encoding="utf-8"))
    if config["protocol_version"] != "sms-qlora-v1" or config["data"]["split_seed"] != 42:
        raise ValueError("unsupported data protocol")
    if config["training"]["seeds"] != [42, 43, 44] or config["model"]["quant_type"] != "nf4":
        raise ValueError("three fixed seeds and actual NF4 are required")
    if config["model"]["revision"] != "989aa7980e4cf806f80c7fef2b1adb7bc71aa306":
        raise ValueError("unexpected base model revision")
    ev = config["evaluation"]
    if ev["served_default"] != "tfidf_lr" or ev["do_sample"] or ev["concurrency"] != 1:
        raise ValueError("served baseline / greedy sequential evaluation contract changed")
    if ev["repeats"] < 2 or ev["warmup_messages"] < 1 or ev["invalid_output_policy"] != "existing_rules_fallback":
        raise ValueError("invalid evaluation protocol")
    if config["data"]["validation_rows"] != 414 or config["data"]["validation_seed"] != 1729:
        raise ValueError("the preregistered train-only validation partition is fixed")
    if config["model"]["id"] != "Qwen/Qwen2.5-1.5B-Instruct" or not config["model"]["double_quant"]:
        raise ValueError("unexpected model/quantization protocol")
    if config["training"]["selection"] != "minimum_train_only_validation_loss":
        raise ValueError("unsupported checkpoint selection")
    if ev["seed_std_ddof"] != 0:
        raise ValueError("population seed std is preregistered")
    return config


def source_fingerprint(repo: Path):
    selected = [repo / "text-risk" / "finetune"]
    files = []
    for root in selected:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if any(part in {"runs", "weights", "checkpoints", ".venv", ".tmp"} for part in path.relative_to(root).parts):
                continue
            files.append({"path": path.relative_to(repo).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    # Actual current-repository fallback/schema/package/data/baseline code is
    # part of experiment identity, even though it lives outside finetune/.
    for path in sorted((repo / "text-risk/riskclf").rglob("*.py")):
        if "__pycache__" not in path.parts:
            files.append({"path": path.relative_to(repo).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return sorted(files, key=lambda value: value["path"])


def provenance(repo: Path, config_path: Path, config: dict, torch=None):
    git = git_snapshot(repo)
    if git["selected_source_dirty"]:
        raise RuntimeError("commit reviewed selected training/baseline paths first; see selected_source_scope; unrelated worktree changes are recorded separately")
    versions = {}
    for name in PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    cpu = platform.processor() or platform.machine()
    info = Path("/proc/cpuinfo")
    if info.exists():
        for line in info.read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.partition(":")[2].strip()
                break
    release = platform.release()
    environment = "WSL2" if "microsoft" in release.lower() else ("native Windows" if os.name == "nt" else "native Linux")
    if Path("/.dockerenv").exists():
        environment = "Docker"
    value = {"git_sha": git["git_sha"], "git": git, "selected_source_dirty": git["selected_source_dirty"],
             "selected_source_scope": git["selected_source_scope"], "selected_source_status": git["selected_source_status"]["stdout"],
             "whole_worktree_dirty": git["whole_worktree_dirty"], "whole_worktree_status": git["whole_worktree_status"]["stdout"],
             "config_sha256": sha256_file(config_path),
             "protocol_sha256": sha256_file(repo / config["data"]["manifest"]),
             "source_files": source_fingerprint(repo), "versions": versions,
             "environment": environment, "platform": platform.platform(), "python": sys.version,
             "cpu": cpu, "exact_command": shlex.join([sys.executable, *sys.argv]), "working_directory": str(Path.cwd()),
             "nvidia_smi": run_command(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,compute_cap", "--format=csv,noheader"]),
             "configured_model": config["model"], "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
             "process_timezone": str(dt.datetime.now().astimezone().tzinfo), "pid": os.getpid(),
             "task_environment": {key: os.environ.get(key) for key in ("HF_HOME", "PIP_CACHE_DIR", "TMPDIR", "XDG_CACHE_HOME", "TORCH_HOME", "HF_HUB_OFFLINE", "PYTHONHASHSEED", "CUBLAS_WORKSPACE_CONFIG", "FINETUNE_GIT")}}
    try:
        value["timestamp_America_Los_Angeles"] = dt.datetime.now(ZoneInfo("America/Los_Angeles")).isoformat()
    except ZoneInfoNotFoundError:
        value["timestamp_America_Los_Angeles"] = None
    if torch is not None:
        value["torch"] = {"version": torch.__version__, "cuda_build": torch.version.cuda,
                           "cuda_available": torch.cuda.is_available()}
        if torch.cuda.is_available():
            prop = torch.cuda.get_device_properties(0)
            value["torch"].update(gpu=prop.name, total_memory_bytes=prop.total_memory,
                                  capability=list(torch.cuda.get_device_capability(0)),
                                  bf16_supported=torch.cuda.is_bf16_supported())
    return value


class OwnedRun:
    def __init__(self, path: Path, config: dict, provenance_value: dict, kind: str, seed=None, resume=False):
        self.path = path.resolve()
        runtime = Path(config["runtime_root"]).resolve()
        try:
            self.path.relative_to(runtime / "runs")
        except ValueError as error:
            raise ValueError("run directories must be beneath the private runtime's runs directory") from error
        if self.path == runtime / "runs":
            raise ValueError("choose a unique run name")
        self.provenance = provenance_value
        self.t0 = time.perf_counter()
        self.owner = {"protocol": config["protocol_version"], "config_sha256": provenance_value["config_sha256"],
                      "protocol_sha256": provenance_value["protocol_sha256"], "git_sha": provenance_value["git_sha"],
                      "source_files": provenance_value["source_files"], "versions": provenance_value.get("versions"),
                      "torch": provenance_value.get("torch"), "kind": kind, "seed": seed}
        if resume:
            if kind != "train" or not self.path.is_dir():
                raise ValueError("only an existing owned training run can resume")
            if json.loads((self.path / "owner.json").read_text()) != self.owner:
                raise ValueError("resume owner/config/source mismatch")
            if (self.path / "summary.json").exists() or (self.path / "adapter").exists():
                raise FileExistsError("completed run or saved adapter cannot be overwritten")
        else:
            self.path.mkdir(parents=True, exist_ok=False)
            exclusive_json(self.path / "owner.json", self.owner)
            exclusive_json(self.path / "config.json", config)
        self.lock_path = self.path / "active.lock"
        with self.lock_path.open("x", encoding="utf-8") as lock:
            lock.write(f"pid={os.getpid()} started={dt.datetime.now(dt.timezone.utc).isoformat()}\n")
        self.attempt = len(list(self.path.glob("attempt-*-start.json"))) + 1
        exclusive_json(self.path / f"attempt-{self.attempt:04d}-start.json",
                       {"provenance": self.provenance, "resume": resume, "kind": kind, "seed": seed})

    def result(self, name: str, data: dict):
        exclusive_json(self.path / name, {"provenance": self.provenance,
                                         "wall_time_seconds": time.perf_counter() - self.t0,
                                         "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(), **data})

    def close(self, error=None):
        self.result(f"attempt-{self.attempt:04d}-end.json", {"status": "failed" if error else "success", "error": str(error) if error else None})
        self.lock_path.unlink()


def artifact_manifest(directory: Path):
    return [{"path": p.relative_to(directory).as_posix(), "bytes": p.stat().st_size, "sha256": sha256_file(p),
             "keep_out_of_git": p.stat().st_size > 20 * 1024 * 1024 or p.suffix in {".safetensors", ".bin", ".pt"}}
            for p in sorted(directory.rglob("*")) if p.is_file()]
