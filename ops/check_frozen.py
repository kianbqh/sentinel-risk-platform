"""Audit the exported pre-module result manifest and original workflow jobs.

Run from a complete checkout with Python's standard library and Git:
  python ops/check_frozen.py --output results/ops/local/frozen-<unique>.json

Missing originals, including ignored originals, fail the audit. The manifest is
a configuration input: this command never updates it or any existing result.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "ops" / "frozen-baseline.json"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative_file(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
        raise ValueError(f"Expected a repository-relative POSIX path: {value!r}")
    return ROOT.joinpath(*path.parts)


def workflow_blocks(raw):
    """Keep job bytes verbatim; exclude only trailing blank separator lines."""
    header = re.search(rb"(?m)^jobs:[ \t]*\r?\n", raw)
    if header is None:
        raise ValueError("Workflow has no top-level jobs mapping")
    body = raw[header.end():]
    matches = list(re.finditer(rb"(?m)^  ([A-Za-z_][A-Za-z0-9_-]*):[ \t]*\r?\n", body))
    if not matches:
        raise ValueError("Workflow has no conventional two-space job keys")
    jobs = {}
    for index, match in enumerate(matches):
        name = match.group(1).decode("ascii")
        if name in jobs:
            raise ValueError(f"Duplicate workflow job key: {name}")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        # Separator lines may be inserted when appending a job. Indented body
        # lines, comments, spaces within lines and line endings are retained.
        jobs[name] = re.sub(rb"(?:\r?\n[ \t]*)+$", b"", body[match.start():end])
    return raw[:header.end()], jobs


def inspect_file(row):
    path = relative_file(row["path"])
    observed = {"path": row["path"], "expected_bytes": row["bytes"],
                "expected_sha256": row["sha256"], "expected_symlink_target": row["symlink_target"]}
    errors = []
    if not path.is_file():
        observed.update(availability="missing", verified=False)
        errors.append("missing original file (ignored files are also required)")
    else:
        observed.update(availability="available", bytes=path.stat().st_size, sha256=sha256(path),
                        symlink_target=os.readlink(path) if path.is_symlink() else None)
        for key in ("bytes", "sha256", "symlink_target"):
            if observed[key] != row[key]:
                errors.append(f"{key} differs from frozen baseline")
        observed["verified"] = not errors
    observed["errors"] = errors
    return observed


class Git:
    def __init__(self, executable):
        self.executable = executable
        self.commands = []

    def run(self, *args, input_bytes=None, accepted=(0,)):
        argv = [self.executable, "-C", str(ROOT), *args]
        output = subprocess.run(argv, input=input_bytes, capture_output=True, check=False, timeout=60)
        row = {"argv": argv, "exit_code": output.returncode,
               "stdout": output.stdout.decode("utf-8", errors="replace"),
               "stderr": output.stderr.decode("utf-8", errors="replace")}
        self.commands.append(row)
        if output.returncode not in accepted:
            raise RuntimeError(f"Git command exited {output.returncode}: {argv!r}; {row['stderr']}")
        return output.stdout


def validate_baseline(config):
    if config.get("schema_version") != 1 or config.get("repo") != "sentinel-risk-platform":
        raise ValueError("Unsupported frozen baseline schema or repository")
    if not re.fullmatch(r"[0-9a-f]{40}", config["start_sha"]):
        raise ValueError("Invalid module-start Git SHA")
    files = config["result_files"]
    if not files or len({row["path"] for row in files}) != len(files):
        raise ValueError("Frozen results must be nonempty and have unique paths")
    for row in files:
        relative_file(row["path"])
        if "results" not in PurePosixPath(row["path"]).parts:
            raise ValueError("Frozen result path is outside a results directory")
        if not isinstance(row["bytes"], int) or row["bytes"] < 0 or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]):
            raise ValueError("Invalid frozen file length or SHA-256")
        if row["symlink_target"] is not None and not isinstance(row["symlink_target"], str):
            raise ValueError("Invalid frozen symlink target")
    if not config["workflows"]:
        raise ValueError("Frozen workflow inventory is empty")
    for row in config["workflows"]:
        relative_file(row["path"])
        raw = row["original_text"].encode("utf-8")
        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError(f"Frozen original workflow text does not match its source hash: {row['path']}")
        workflow_blocks(raw)


def audit(config, git):
    errors = []
    start = config["start_sha"]
    head = git.run("rev-parse", "HEAD").decode().strip()
    git_status = git.run("status", "--porcelain").decode("utf-8")
    start_paths = set(git.run("ls-tree", "-r", "--name-only", "-z", start).decode("utf-8").split("\0"))
    current_paths = set(git.run("ls-files", "--cached", "-z").decode("utf-8").split("\0"))
    all_paths = [row["path"] for row in config["result_files"]]
    ignored_raw = git.run("check-ignore", "-z", "--stdin", input_bytes=("\0".join(all_paths) + "\0").encode("utf-8"), accepted=(0, 1))
    ignored = set(ignored_raw.decode("utf-8").split("\0"))
    files = []
    for expected in config["result_files"]:
        row = inspect_file(expected)
        row.update(tracked_at_start=expected["path"] in start_paths,
                   tracked_in_current_index=expected["path"] in current_paths,
                   ignored_in_current_checkout=expected["path"] in ignored)
        files.append(row)
        errors.extend({"path": row["path"], "error": error} for error in row["errors"])
    tracked = [path for path in all_paths if path in start_paths]
    # Only exact original tracked paths are supplied. New results/ops files do
    # not make the proof nonempty, even when they share a result root.
    diff_args = ["diff", "--stat", start, "--", *tracked]
    if not tracked:
        raise ValueError("No original tracked result paths; refusing a broad Git diff")
    diff = git.run(*diff_args).decode("utf-8")
    if diff.strip():
        errors.append({"error": "Tracked original result diff is not empty", "stdout": diff})
    workflows = []
    for expected in config["workflows"]:
        original = expected["original_text"].encode("utf-8")
        git_original = git.run("show", start + ":" + expected["path"])
        if hashlib.sha256(git_original).hexdigest() != expected["sha256"] or git_original != original:
            errors.append({"path": expected["path"], "error": "Start-commit workflow differs from frozen original"})
        original_preamble, old_jobs = workflow_blocks(original)
        row = {"path": expected["path"], "original_sha256": expected["sha256"],
               "original_jobs": list(old_jobs), "original_job_sha256": {
                   name: hashlib.sha256(raw).hexdigest() for name, raw in old_jobs.items()}}
        try:
            current = relative_file(expected["path"]).read_bytes()
            current_preamble, new_jobs = workflow_blocks(current)
            changed = [name for name, raw in old_jobs.items() if new_jobs.get(name) != raw]
            row.update(current_sha256=hashlib.sha256(current).hexdigest(),
                       new_jobs=[name for name in new_jobs if name not in old_jobs],
                       changed_original_jobs=changed, preamble_unchanged=current_preamble == original_preamble)
            if changed:
                errors.append({"path": expected["path"], "error": "Original CI job block changed or missing", "jobs": changed})
            if current_preamble != original_preamble:
                errors.append({"path": expected["path"], "error": "Original workflow preamble changed"})
        except (OSError, ValueError) as error:
            row["error"] = str(error)
            errors.append({"path": expected["path"], "error": str(error)})
        workflows.append(row)
    return {"status": "passed" if not errors else "failed", "repo": config["repo"],
            "git_sha": head, "git_status_porcelain": git_status, "git_worktree_dirty": bool(git_status),
            "module_start_sha": start, "frozen_files_expected": len(files),
            "frozen_files_verified": sum(row["verified"] for row in files),
            "original_tracked_files": len(tracked),
            "original_untracked_or_ignored_files": [row["path"] for row in files if not row["tracked_at_start"]],
            "unavailable_originals": [row["path"] for row in files if row["availability"] == "missing"],
            "files": files, "git_diff_stat_argv": [git.executable, "-C", str(ROOT), *diff_args],
            "git_diff_stat_stdout": diff, "workflows": workflows, "errors": errors}


def checked_output(value):
    path = Path(value).resolve()
    if path.suffix != ".json" or not any(path.is_relative_to((ROOT / prefix).resolve())
                                          for prefix in ("results/ops", ".runtime/ops")):
        raise ValueError("Choose a new .json output under results/ops/ or .runtime/ops/")
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite recorded evidence: {path}")
    return path


def environment():
    if os.name == "nt":
        return "native Windows"
    return "WSL2" if "microsoft" in platform.release().lower() else platform.system()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--git", default="git", help="Git executable; native Windows Git works without WSL Git")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = checked_output(args.output)
    started = time.perf_counter()
    timestamp = datetime.now(timezone.utc).isoformat()
    script = Path(__file__).resolve()
    baseline = args.baseline.resolve()
    before = {"ops/check_frozen.py": sha256(script), "baseline": sha256(baseline)}
    executable = shutil.which(args.git)
    if executable is None:
        parser.error(f"Git executable not available: {args.git}")
    git = Git(executable)
    config = json.loads(baseline.read_text(encoding="utf-8"))
    report = {"status": "failed"}
    try:
        validate_baseline(config)
        report = audit(config, git)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        report.update(status="failed", errors=[{"error": str(error)}])
    after = {"ops/check_frozen.py": sha256(script), "baseline": sha256(baseline)}
    if before != after:
        report["status"] = "failed"
        report.setdefault("errors", []).append({"error": "Audit source or baseline changed during this invocation"})
    argv = [sys.executable, *sys.argv]
    report.update(timestamp=timestamp, finished_timestamp=datetime.now(timezone.utc).isoformat(),
                  environment=environment(), platform=platform.platform(), cpu_model=platform.processor(),
                  logical_cpus=os.cpu_count(), gpu_used=False,
                  gpu_driver_cuda="not queried; file/Git integrity audit uses no GPU",
                  package_versions={"python": platform.python_version()},
                  exact_command=subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv),
                  argv=argv, baseline_path=str(baseline), baseline_sha256=before["baseline"],
                  source_manifest_sha256=config.get("origin", {}).get("source_manifest_sha256"),
                  source_sha256_before=before, source_sha256_after=after,
                  git_executable=executable, git_commands=git.commands,
                  wall_timer_scope="manifest loading, all original-byte/workflow/Git checks, provenance collection; output serialization excluded",
                  wall_seconds=time.perf_counter() - started)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "output": str(output),
                      "frozen_files_verified": report.get("frozen_files_verified"),
                      "errors": report.get("errors", [])}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
