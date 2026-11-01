"""Run ZAP only against this project's two local apps and validate every generated report."""
import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def validate_report(path, html_path, target, returncode):
    if returncode not in (0, 1, 2):
        raise RuntimeError(f"Scanner infrastructure failure, return code {returncode}")
    report = json.loads(path.read_text())
    if not html_path.is_file() or "<html" not in html_path.read_text(errors="replace").lower():
        raise RuntimeError(f"Missing or invalid HTML report: {html_path}")
    expected = urlsplit(target)
    sites = report.get("site")
    if not isinstance(sites, list) or not sites:
        raise RuntimeError("Report has no scanned site")
    alerts = []
    for site in sites:
        origin = urlsplit(site["@name"])
        if (origin.scheme, origin.hostname, origin.port) != (expected.scheme, expected.hostname, expected.port):
            raise RuntimeError(f"Unexpected report origin: {site['@name']}")
        if not isinstance(site.get("alerts"), list):
            raise RuntimeError("Missing alert list")
        for alert in site["alerts"]:
            if str(alert["riskcode"]) not in ("0", "1", "2", "3"):
                raise RuntimeError("Unrecognized risk code")
            for instance in alert.get("instances", []):
                uri = urlsplit(instance["uri"])
                if (uri.scheme, uri.hostname, uri.port) != (expected.scheme, expected.hostname, expected.port):
                    raise RuntimeError(f"Out-of-scope alert: {instance['uri']}")
            alerts.append(alert)
    return {"sites": len(sites), "alert_types": len(alerts), "json_parsed": True, "html_present": True}


def free_port(port):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


def owns_listener(pid, port):
    sockets = set()
    for descriptor in Path(f"/proc/{pid}/fd").iterdir():
        try:
            target = descriptor.readlink().as_posix()
        except OSError:
            continue
        if target.startswith("socket:["):
            sockets.add(target[8:-1])
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        for line in Path(table).read_text().splitlines()[1:]:
            columns = line.split()
            if int(columns[1].split(":")[-1], 16) == port and columns[3] == "0A" and columns[9] in sockets:
                return True
    return False


def stop_owned_app(process, database):
    if process.poll() is not None:
        return
    command = Path(f"/proc/{process.pid}/cmdline").read_bytes()
    if str(database).encode() not in command or os.getpgid(process.pid) != process.pid:
        raise RuntimeError("App ownership changed; refusing to signal an unrelated process")
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", nargs="?", default="both", choices=("baseline", "full", "both"))
    args = parser.parse_args()
    minutes = int(os.environ.get("FULL_MINUTES", "5"))
    if minutes < 1:
        parser.error("FULL_MINUTES must be positive")
    docker = os.environ.get("DOCKER_BIN", "docker")
    image = os.environ.get("ZAP_IMAGE", "ghcr.io/zaproxy/zaproxy:stable")
    run_id = uuid.uuid4().hex[:12]
    reports = ROOT / "results" / "zap" / run_id
    reports.mkdir(parents=True)
    reports.chmod(0o777)
    base_tmp = Path(os.environ.get("TMPDIR", ROOT / ".tmp")).resolve()
    base_tmp.mkdir(parents=True, exist_ok=True)
    manifest = {"run_id": run_id, "image": image, "active_scan_max_minutes": minutes, "scans": []}
    manifest_path = ROOT / "results" / "dast_run_manifest.json"
    app_code = "import importlib,sys; importlib.import_module('app.'+sys.argv[1]).serve(sys.argv[2],int(sys.argv[3]))"
    modes = ("baseline", "full") if args.mode == "both" else (args.mode,)
    for port in (5001, 5002):
        free_port(port)
    with tempfile.TemporaryDirectory(prefix="appsec-dast-", dir=base_tmp) as temporary:
        for name, port in (("vulnerable", 5001), ("fixed", 5002)):
            free_port(port)
            database = Path(temporary) / f"{name}.db"
            target = f"http://127.0.0.1:{port}"
            app_log = open(reports / f"app_{name}.log", "w")
            process = subprocess.Popen(
                [sys.executable, "-c", app_code, name, str(database), str(port)],
                cwd=ROOT, env={**os.environ, "COOKIE_SECURE": "0"},
                stdout=app_log, stderr=subprocess.STDOUT, start_new_session=True,
            )
            try:
                for attempt in range(60):
                    if process.poll() is not None:
                        raise RuntimeError(f"Owned {name} app exited before readiness")
                    try:
                        with urlopen(target + "/", timeout=2) as response:
                            if response.status == 200:
                                if not owns_listener(process.pid, port):
                                    raise RuntimeError("The HTTP listener is not owned by the spawned app")
                                break
                    except OSError:
                        pass
                    time.sleep(0.5)
                else:
                    raise RuntimeError(f"Owned {name} app did not become ready")
                for scan in modes:
                    if process.poll() is not None or not owns_listener(process.pid, port):
                        raise RuntimeError("Owned app listener disappeared before scanning")
                    stem = f"{scan}_{name}"
                    container = f"appsec13-zap-{stem.replace('_', '-')}-{run_id}"
                    record = {"scan": scan, "target_name": name, "target": target, "app_pid": process.pid,
                              "database": str(database), "container": container, "listener_pid_verified": True}
                    manifest["scans"].append(record)
                    command = [
                        docker, "run", "--rm", "--name", container, "--label", f"appsec.run={run_id}",
                        "--network", "host", "--cpus", "2", "-v", f"{reports}:/zap/wrk:rw",
                        "-v", f"{ROOT / 'scan/zap_owned_scope.py'}:/zap/owned_scope.py:ro",
                        "-e", f"APPSEC_TARGET={target}", "-e", f"APPSEC_ACTIVE_MINUTES={minutes}",
                        "-e", f"APPSEC_SCOPE_PROOF=/zap/wrk/scope_{stem}.json", image,
                        "zap-baseline.py" if scan == "baseline" else "zap-full-scan.py",
                        "-t", target, "-m", "1", "-T", "5", "-I", "--hook", "/zap/owned_scope.py",
                        "-J", f"{stem}.json", "-r", f"{stem}.html",
                    ]
                    if scan == "baseline":
                        command.append("--autooff")
                    started = time.monotonic()
                    try:
                        with open(reports / f"{stem}.log", "w") as log:
                            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                                    timeout=(minutes + 12) * 60)
                        record["returncode"] = result.returncode
                        record["wall_seconds"] = round(time.monotonic() - started, 3)
                        record["report"] = str((reports / f"{stem}.json").relative_to(ROOT))
                        record.update(validate_report(reports / f"{stem}.json", reports / f"{stem}.html",
                                                      target, result.returncode))
                        proof = json.loads((reports / f"scope_{stem}.json").read_text())
                        if proof["target"] != target or proof["mode"] != "protect" or proof["active_scan_max_minutes"] != minutes:
                            raise RuntimeError("Missing or invalid owned-context configuration evidence")
                        record["scope_verified"] = True
                    except Exception as error:
                        record["failure"] = str(error)
                        raise
                    finally:
                        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
                        ownership = subprocess.run([docker, "inspect", "--format", '{{index .Config.Labels "appsec.run"}}', container],
                                                   capture_output=True, text=True)
                        if ownership.returncode == 0 and ownership.stdout.strip() == run_id:
                            subprocess.run([docker, "rm", "-f", container], check=True, stdout=subprocess.DEVNULL)
            finally:
                stop_owned_app(process, database)
                app_log.close()
    subprocess.run([sys.executable, "scan/zap_summary.py", str(reports), "--output", "results/dast_summary.md"],
                   cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
