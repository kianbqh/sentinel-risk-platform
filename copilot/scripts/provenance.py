"""Unique result directories and run context; never reuse an existing output."""
import hashlib
import importlib.metadata
import json
import os
import platform
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked_output(command, cwd=None):
    try:
        return subprocess.check_output(command, cwd=cwd, text=True, stderr=subprocess.DEVNULL, timeout=10).strip()
    except (OSError, subprocess.SubprocessError):
        return None


DEFAULT_SOURCE_PATHS = (
    "copilot/agent", "copilot/eval", "copilot/mcp_server", "copilot/retrieval", "copilot/db",
    "copilot/scripts", "copilot/configs", "copilot/requirements-tools.txt", "copilot/README-tools.md",
    "copilot/compose.copilot.yml", "serving/model/model.joblib", "serving/model/model_meta.json",
    "streaming/model/lr_model.json", "gateway/main.py", "gateway/fixtures/manifest.json",
    "gateway/fixtures/creditcard-prefix-10000.parquet", "ato/datagen.py", "ato/features.py",
)
TASK_ENV_KEYS = ("COPILOT_GIT", "FINETUNE_GIT", "HF_HOME", "PIP_CACHE_DIR", "TMPDIR", "TEMP", "TMP",
    "XDG_CACHE_HOME", "TORCH_HOME", "HF_HUB_OFFLINE", "HF_HUB_DISABLE_IMPLICIT_TOKEN", "PYTHONPATH",
    "PYTHONHASHSEED", "CUBLAS_WORKSPACE_CONFIG", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS")


def _git_backend(repo, git_binary=None):
    private = Path("/opt/codex-resume/all-modules-v2/m1a/tools/git-native/usr/bin/git")
    selected = git_binary or os.environ.get("COPILOT_GIT") or os.environ.get("FINETUNE_GIT")
    selected = selected or (str(private) if private.is_file() else shutil.which("git"))
    if not selected:
        raise RuntimeError("Git unavailable; set COPILOT_GIT to the private Git executable")
    found = shutil.which(selected)
    executable = Path(found or selected).resolve()
    if not executable.is_file():
        raise RuntimeError("Configured read-only Git executable is unavailable")
    root = Path(repo).resolve()
    argument = str(root)
    if os.name != "nt" and str(executable).lower().endswith(".exe"):
        translated = subprocess.run(["wslpath", "-w", str(root)], text=True, capture_output=True, timeout=30, check=True)
        argument = translated.stdout.strip()
        if not argument:
            raise RuntimeError("Could not translate repository path for Windows Git")
    return executable, argument


def _git_record(repo, args, *, timeout=600, git_binary=None):
    executable, argument = _git_backend(repo, git_binary)
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    command = [str(executable), "--no-optional-locks", "-c", "safe.directory="+argument, "-C", argument, *args]
    try:
        result = subprocess.run(command, cwd=Path(repo).resolve(), env=env, text=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Read-only Git provenance command could not complete") from exc
    receipt = {"argv": command, "command": shlex.join(command), "returncode": result.returncode,
        "stdout": result.stdout.rstrip("\r\n"), "stderr": result.stderr.rstrip("\r\n"), "timeout_seconds": timeout,
        "environment_overrides": {key: env[key] for key in ("GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT", "LC_ALL")}}
    if result.returncode:
        raise RuntimeError("Read-only Git provenance command failed: "+json.dumps(receipt))
    return receipt


def git_output(repo, args, *, timeout=600, git_binary=None):
    """Strict read-only Git helper; failure is never represented as null/empty."""
    return _git_record(repo, list(args), timeout=timeout, git_binary=git_binary)["stdout"]


def source_hashes(repo, selected_paths):
    root = Path(repo).resolve()
    hashes, missing = {}, []
    for name in selected_paths:
        path = root/name
        if not path.exists():
            missing.append(name)
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("Selected source must stay within the repository")
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        for candidate in candidates:
            if not candidate.is_file() or candidate.is_symlink() or "__pycache__" in candidate.parts or candidate.suffix == ".pyc":
                continue
            relative = candidate.relative_to(root).as_posix()
            hashes[relative] = sha256(candidate)
    return {"sha256": hashes, "missing_paths": missing}


def git_snapshot(repo, selected_paths=DEFAULT_SOURCE_PATHS, *, timeout=600, git_binary=None):
    root = Path(repo).resolve()
    before = _git_record(root, ["rev-parse", "HEAD"], timeout=timeout, git_binary=git_binary)
    if not re.fullmatch(r"[0-9a-f]{40}", before["stdout"]):
        raise RuntimeError("Git did not return an exact full commit SHA")
    selected = _git_record(root, ["status", "--porcelain=v1", "--untracked-files=normal", "--", *selected_paths], timeout=timeout, git_binary=git_binary)
    whole = _git_record(root, ["status", "--porcelain=v1", "--untracked-files=normal"], timeout=timeout, git_binary=git_binary)
    files = source_hashes(root, selected_paths)
    after = _git_record(root, ["rev-parse", "HEAD"], timeout=timeout, git_binary=git_binary)
    if before["stdout"] != after["stdout"]:
        raise RuntimeError("Repository HEAD changed during provenance collection")
    executable, argument = _git_backend(root, git_binary)
    return {"git_sha": before["stdout"], "executable": str(executable), "executable_sha256": sha256(executable),
        "repository_argument": argument, "selected_source_scope": list(selected_paths), "source_hashes": files,
        "selected_source_dirty": bool(selected["stdout"]), "selected_source_status": selected,
        "whole_worktree_dirty": bool(whole["stdout"]), "whole_worktree_status": whole,
        "head_before": before, "head_after": after,
        "task_environment": {key: os.environ.get(key) for key in TASK_ENV_KEYS}}


def verify_source_snapshot(repo, snapshot, *, timeout=600, git_binary=None):
    """Check HEAD and selected sources only; unrelated outputs may change."""
    before = _git_record(repo, ["rev-parse", "HEAD"], timeout=timeout, git_binary=git_binary)
    selected = _git_record(repo, ["status", "--porcelain=v1", "--untracked-files=normal", "--",
        *snapshot["selected_source_scope"]], timeout=timeout, git_binary=git_binary)
    files = source_hashes(repo, snapshot["selected_source_scope"])
    after = _git_record(repo, ["rev-parse", "HEAD"], timeout=timeout, git_binary=git_binary)
    if before["stdout"] != snapshot["git_sha"] or after["stdout"] != snapshot["git_sha"]:
        raise RuntimeError("Repository HEAD changed during the run")
    if files != snapshot["source_hashes"] or selected["stdout"] != snapshot["selected_source_status"]["stdout"]:
        raise RuntimeError("Selected source content or index state changed during the run")
    return {"status": "unchanged", "head_before": before, "head_after": after,
        "selected_source_status": selected, "source_hashes": files,
        "scope": "HEAD and selected source bytes/index state; unrelated worktree outputs are permitted"}


class RunRecord:
    def __init__(self, config_path, command=None):
        self.started = time.perf_counter()
        self.config_path = Path(config_path).resolve()
        self.config = json.loads(self.config_path.read_text())
        self.root = Path(self.config.get("repo_root", ".")).resolve()
        selected = tuple(self.config.get("source_paths", DEFAULT_SOURCE_PATHS))
        if not selected or any(not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts for name in selected):
            raise ValueError("A nonempty repository-relative source scope is required")
        snapshot = git_snapshot(self.root, selected, timeout=self.config.get("git_timeout_seconds", 600),
            git_binary=self.config.get("git_binary"))
        runner = Path(__file__).resolve()
        if self.config.get("require_clean_source", False):
            if snapshot["selected_source_dirty"] or snapshot["source_hashes"]["missing_paths"] or not runner.is_relative_to(self.root):
                raise ValueError("Formal measurement requires integrated, committed selected source")
        output = Path(self.config["output_dir"])
        self.output = output if output.is_absolute() else self.root / output
        self.output.mkdir(parents=True, exist_ok=False)
        (self.output / "config.json").write_bytes(self.config_path.read_bytes())
        packages = {}
        for name in ("mcp", "mcp-types", "psycopg", "pgvector", "sentence-transformers", "transformers", "torch", "numpy",
                     "scikit-learn", "pandas", "joblib", "pyarrow", "langgraph", "jsonschema", "httpx", "httpx2", "uvicorn"):
            try:
                packages[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                packages[name] = None
        self.context = {"git_sha": snapshot["git_sha"], "git": snapshot,
            "git_status": snapshot["whole_worktree_status"]["stdout"],
            "selected_source_dirty": snapshot["selected_source_dirty"],
            "whole_worktree_dirty": snapshot["whole_worktree_dirty"],
            "source_hashes": snapshot["source_hashes"], "runner_source": {"path": str(runner), "sha256": sha256(runner)},
            "config_sha256": sha256(self.config_path), "packages": packages,
            "environment": self.config["environment"], "platform": platform.platform(),
            "cpu": platform.processor() or checked_output(["lscpu"]),
            "gpu_driver": checked_output(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"]),
            "cuda": None, "python": sys.version, "python_executable": sys.executable,
            "python_import_paths": list(sys.path), "command": command or sys.argv,
            "timestamp": datetime.now(timezone.utc).isoformat()}
        self.write("started.json", self.context)

    def _run_context(self):
        return {**self.context, "wall_seconds": time.perf_counter()-self.started,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "device": {"runner": "cpu orchestration", "configured_model_device": self.config.get("device", "not specified by this runner"),
                "observed_cpu": self.context.get("cpu") or "not available in this runner",
                "observed_gpu_driver": self.context.get("gpu_driver") or "not available in this runner",
                "cuda": self.context.get("cuda") or "not queried by this runner"},
            "interpretation": "provenance of this artifact; raw observations are evidence, not an acceptance claim"}

    def write(self, name, value, *, raw=False):
        context = self._run_context()
        if isinstance(value, dict) and not raw:
            payload = dict(value)
            if "run_context" in payload:
                context["upstream_run_context"] = payload.pop("run_context")
            payload["run_context"] = context
        else:
            # Preserve external JSON payloads and list/scalar observations exactly.
            payload = {"value": value, "run_context": context}
        with (self.output / name).open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, allow_nan=False)
            handle.write("\n")

    def finish(self, result):
        if sha256(self.config_path) != self.context["config_sha256"]:
            raise RuntimeError("Run config changed during execution")
        runner = self.context["runner_source"]
        if sha256(runner["path"]) != runner["sha256"]:
            raise RuntimeError("Actual runner source changed during execution")
        self.context["end_source_verification"] = verify_source_snapshot(self.root, self.context["git"],
            timeout=self.config.get("git_timeout_seconds", 600), git_binary=self.config.get("git_binary"))
        self.write("result.json", {**self.context, "wall_seconds": time.perf_counter()-self.started, **result})

    def fail(self, error_type):
        self.write("failed.json", {**self.context, "wall_seconds": time.perf_counter()-self.started,
            "status": "failed", "error_type": error_type})
