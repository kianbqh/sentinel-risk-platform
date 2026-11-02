#!/usr/bin/env python3
"""Helm lifecycle acceptance on an isolated single-node kind cluster."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evidence import ROOT, RunJournal, command, provenance, write_json
from verify import synthetic_features, wait_for


def percentile(values, fraction):
    values = sorted(values)
    index = (len(values) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (index - lower)


def validate_serving_state(deployment, all_pods, endpoint_slices, expected_tag):
    """Accept controller-observed desired replicas and their actual ready endpoints."""
    desired = deployment["spec"]["replicas"]
    status = deployment.get("status", {})
    if status.get("observedGeneration", 0) < deployment["metadata"]["generation"]:
        raise AssertionError("Deployment controller has not observed this generation")
    for field in ("updatedReplicas", "readyReplicas", "availableReplicas"):
        if status.get(field, 0) != desired:
            raise AssertionError(f"Deployment {field} is not the desired replica count")
    ready = [pod for pod in all_pods if not pod["metadata"].get("deletionTimestamp")
             and pod["status"].get("phase") == "Running"
             and any(condition["type"] == "Ready" and condition["status"] == "True"
                     for condition in pod["status"].get("conditions", []))]
    if len(ready) != desired:
        raise AssertionError("Non-deleting Ready pod count does not match desired replicas")
    endpoint_uids = set()
    for endpoint_slice in endpoint_slices:
        for endpoint in endpoint_slice.get("endpoints", []):
            conditions = endpoint.get("conditions", {})
            if conditions.get("ready") is True:
                if conditions.get("terminating") is True:
                    raise AssertionError("A ready Service endpoint is still terminating")
                target = endpoint.get("targetRef", {})
                if target.get("kind") != "Pod" or not target.get("uid"):
                    raise AssertionError("Ready Service endpoint has no Pod UID")
                endpoint_uids.add(target["uid"])
    if endpoint_uids != {pod["metadata"]["uid"] for pod in ready}:
        raise AssertionError("Ready Service endpoints do not exactly match non-deleting Ready pods")
    for pod in ready:
        if any(not container["image"].endswith(":" + expected_tag) for container in pod["spec"]["containers"]):
            raise AssertionError("An actual serving Pod has the wrong image tag")
    return ready


INITIAL_SMOKE_SOURCE = r'''
import json,sys,time,urllib.request
cfg=json.loads(sys.argv[1]); timeout=float(sys.argv[2]); statuses={}
for name,url in cfg.items():
 started=time.time_ns(); status=None; error=None
 try:
  with urllib.request.urlopen(url,timeout=timeout) as response:
   status=response.status
 except Exception as exc:
  status=getattr(exc,"code",None); error=str(exc)
 print(json.dumps({"event":"service_probe","service":name,"url":url,"start_unix_ns":started,"end_unix_ns":time.time_ns(),"status":status,"error":error}),flush=True)
 if status!=200: sys.exit(1)
 statuses[name]=status
print(json.dumps({"service_status":statuses}),flush=True)
'''


def wait_for_initial_smoke(probe, journal, expected_services, timeout, interval=.5,
                          *, monotonic=time.monotonic, sleep=time.sleep):
    """Bound initial Service warmup only; journal every failed or successful attempt."""
    if not expected_services or not 0 < timeout < float("inf") or not 0 < interval < float("inf"):
        raise ValueError("Initial smoke requires services and finite positive timeout/poll seconds")
    started = monotonic()
    deadline = started + timeout
    attempt = 0
    expected = {name: 200 for name in expected_services}
    while monotonic() < deadline:
        attempt += 1
        before = monotonic()
        remaining = deadline - before
        if remaining <= 0:
            break
        journal.record("initial_service_smoke_attempt_started", attempt=attempt, remaining_budget_seconds=remaining)
        try:
            process = probe(remaining)
        except subprocess.TimeoutExpired as error:
            def decoded(value):
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
            journal.record("initial_service_smoke_attempt", attempt=attempt, timed_out=True,
                           duration_seconds=monotonic() - before, remaining_budget_seconds=remaining,
                           command=error.cmd, stdout=decoded(error.stdout), stderr=decoded(error.stderr))
            raise RuntimeError("Initial Service HTTP smoke exhausted its startup deadline") from error
        journal.record("initial_service_smoke_attempt", attempt=attempt, timed_out=False,
                       duration_seconds=monotonic() - before, remaining_budget_seconds=remaining,
                       command=process.args, exit_code=process.returncode, stdout=process.stdout,
                       stderr=process.stderr)
        if process.returncode == 0:
            try:
                result = json.loads(process.stdout.splitlines()[-1])
            except (ValueError, IndexError) as error:
                raise RuntimeError("Initial Service HTTP smoke emitted no valid summary") from error
            if result.get("service_status") != expected:
                raise RuntimeError("Initial Service HTTP smoke did not verify every expected Service")
            # Reject a probe which only completed after the total startup budget.
            if monotonic() > deadline:
                break
            return {**result, "attempts": attempt, "duration_seconds": monotonic() - started,
                    "scope": "initial startup gate before load Pod creation; not measured load"}
        sleep(min(interval, max(0, deadline - monotonic())))
    raise RuntimeError(f"Initial Service HTTP smoke did not converge within {timeout} seconds ({attempt} attempts)")


LOAD_SOURCE = r'''
import concurrent.futures, json, pathlib, sys, threading, time, urllib.request
cfg=json.loads(sys.argv[1]); body=json.loads(sys.argv[2]); url=sys.argv[3]
stop=pathlib.Path('/tmp/sentinel-stop'); lock=threading.Lock(); started=time.perf_counter()
def request(phase):
    before=time.perf_counter(); start_ns=time.time_ns(); status=None; error=None; valid=False
    try:
        req=urllib.request.Request(url,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=cfg['request_timeout_seconds']) as response:
            status=response.status; data=json.loads(response.read())
            valid=status==200 and data['transaction_id']==body['transaction_id'] and data['alert_published'] is False and isinstance(data['fraud_score'],(int,float))
    except Exception as exc: error=str(exc)
    row={'phase':phase,'start_seconds':before-started,'seconds':time.perf_counter()-before,'start_unix_ns':start_ns,'end_unix_ns':time.time_ns(),'status':status,'valid':valid,'error':error}
    with lock: print(json.dumps(row),flush=True)
for _ in range(cfg['warmup_requests']): request('warmup')
print(json.dumps({'event':'load_ready'}),flush=True)
def worker():
    while not stop.exists() and time.perf_counter()-started<cfg['rollout_timeout_seconds']+60:
        request('measured'); time.sleep(cfg['load_interval_seconds'])
with concurrent.futures.ThreadPoolExecutor(max_workers=cfg['load_concurrency']) as pool:
    list(pool.map(lambda _:worker(), range(cfg['load_concurrency'])))
print(json.dumps({'event':'load_complete','wall_seconds':time.perf_counter()-started}),flush=True)
'''


def run(config, values_path, journal):
    cfg = config["kind"]
    cluster, namespace, release = (cfg[name] for name in ("cluster", "namespace", "release"))
    for value in (cluster, namespace):
        if not value.startswith("sentinel-m1b"):
            raise ValueError("kind cluster and namespace must start with sentinel-m1b")
    context = "kind-" + cluster
    base = release + "-sentinel"

    def kubectl(*args, timeout=30, check=True):
        return command(["kubectl", "--context", context, "--namespace", namespace, *args], timeout=timeout, check=check).stdout

    def helm(*args, timeout=360):
        return command(["helm", *args, "--kube-context", context, "--namespace", namespace], timeout=timeout).stdout

    def pods(component):
        return json.loads(kubectl("get", "pods", "-l", f"app.kubernetes.io/instance={release},app.kubernetes.io/component={component}", "-o", "json"))["items"]

    def versions(expected_tag):
        snapshot = {"serving_pods": {}, "all_pods": {}, "deployment_state": {}, "endpoint_slices": {}}
        for name in ("gateway", "backend", "frontend"):
            all_pods = pods(name)
            deployment = json.loads(kubectl("get", "deployment", base + "-" + name, "-o", "json"))
            slices = json.loads(kubectl("get", "endpointslices", "-l", "kubernetes.io/service-name=" + base + "-" + name, "-o", "json"))["items"]
            # Persist the exact pre-assert observation, including deleting old pods.
            journal.record("serving_snapshot_observed", component=name, expected_tag=expected_tag,
                           all_pods=all_pods, deployment=deployment, endpoint_slices=slices)
            try:
                ready = validate_serving_state(deployment, all_pods, slices, expected_tag)
            except AssertionError as error:
                journal.record("serving_snapshot_not_converged", component=name, reason=str(error))
                raise
            snapshot["serving_pods"][name] = ready
            snapshot["all_pods"][name] = all_pods
            snapshot["deployment_state"][name] = deployment
            snapshot["endpoint_slices"][name] = slices
        return snapshot

    def stable_versions(expected_tag):
        return wait_for(lambda: versions(expected_tag), cfg["rollout_timeout_seconds"],
                        interval=.5, description="observed desired Deployment and ready Service endpoints on " + expected_tag)

    def smoke():
        # Reach Kubernetes Services from a running gateway pod; no port-forward artifact.
        source = '''import json,sys,urllib.request
cfg=json.loads(sys.argv[1])
for name,url in cfg.items():
 with urllib.request.urlopen(url,timeout=8) as response:
  assert response.status==200,(name,response.status)
print(json.dumps({"service_status":{name:200 for name in cfg}}))
'''
        urls = {"gateway": f"http://{base}-gateway:18040/readyz", "backend": f"http://{base}-backend:18041/readyz",
                "frontend": f"http://{base}-frontend:13040/"}
        return json.loads(kubectl("exec", "deploy/" + base + "-gateway", "--", "python", "-c", source, json.dumps(urls)))

    tags_a = ",".join(f"images.{component}.tag={cfg['initial_tag']}" for component in ("gateway", "backend", "frontend"))
    install_start = time.perf_counter()
    journal.record("helm_install_started", cluster=cluster, namespace=namespace)
    install_output = helm("install", release, "deploy/helm/sentinel", "--create-namespace", "--values", str(values_path),
                          "--set-string", tags_a, "--wait", "--timeout", "5m")
    install_seconds = time.perf_counter() - install_start
    installed_pods = stable_versions(cfg["initial_tag"])
    urls = {"gateway": f"http://{base}-gateway:18040/readyz", "backend": f"http://{base}-backend:18041/readyz",
            "frontend": f"http://{base}-frontend:13040/"}

    def initial_probe(remaining):
        return command(["kubectl", "--context", context, "--namespace", namespace,
                        "exec", "deploy/" + base + "-gateway", "--", "python", "-c", INITIAL_SMOKE_SOURCE,
                        json.dumps(urls), str(min(8, remaining / len(urls)))],
                       timeout=remaining, check=False)

    journal.record("initial_service_smoke_started", urls=urls,
                   timeout_seconds=cfg["initial_smoke_timeout_seconds"], poll_seconds=cfg["initial_smoke_poll_seconds"])
    initial_smoke = wait_for_initial_smoke(initial_probe, journal, urls,
                                          cfg["initial_smoke_timeout_seconds"], cfg["initial_smoke_poll_seconds"])
    installed = {"duration_seconds": install_seconds, "helm_output": install_output,
                 "smoke": initial_smoke, "pods": installed_pods}
    journal.record("helm_install_completed", **installed)
    load_name = "sentinel-m1b-load"
    body = {"features": synthetic_features(), "transaction_id": "ops-kind-load", "publish": False}
    kubectl("run", load_name, "--image=sentinel-risk-gateway:" + cfg["initial_tag"], "--image-pull-policy=Never",
            "--restart=Never", "--command", "--", "python", "-u", "-c", LOAD_SOURCE,
            json.dumps(cfg), json.dumps(body), f"http://{base}-gateway:18040/v1/transactions/score")
    kubectl("wait", "pod/" + load_name, "--for=condition=Ready", "--timeout=120s", timeout=130)
    wait_for(lambda: '"event": "load_ready"' in kubectl("logs", load_name), 60, description="warmed in-cluster load loop")
    rolled = {}
    raw = []

    def capture_load():
        latest = [json.loads(line) for line in kubectl("logs", load_name, check=False).splitlines() if line.startswith("{")]
        if len(latest) > len(raw):
            fresh = latest[len(raw):]
            raw.extend(fresh)
            journal.record("load_samples", rows=fresh)

    capture_load()
    try:
        upgrade_start = time.perf_counter()
        upgrade_start_ns = time.time_ns()
        tags_b = ",".join(f"images.{component}.tag={cfg['upgrade_tag']}" for component in ("gateway", "backend", "frontend"))
        upgrade_args = ["helm", "upgrade", release, "deploy/helm/sentinel", "--reuse-values", "--set-string", tags_b,
                        "--wait", "--timeout", "5m", "--kube-context", context, "--namespace", namespace]
        journal.record("helm_upgrade_started", start_unix_ns=upgrade_start_ns, command=upgrade_args)
        process = subprocess.Popen(upgrade_args, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            while process.poll() is None:
                capture_load()
                if time.perf_counter() - upgrade_start > cfg["rollout_timeout_seconds"] + 30:
                    process.kill()
                    raise TimeoutError("Helm upgrade exceeded the configured observation timeout")
                time.sleep(.5)
            upgrade_output, upgrade_errors = process.communicate(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        upgrade_end_ns = time.time_ns()
        journal.record("helm_upgrade_exited", end_unix_ns=upgrade_end_ns, exit_code=process.returncode,
                       stdout=upgrade_output, stderr=upgrade_errors)
        if process.returncode:
            raise RuntimeError(f"Helm upgrade failed with {process.returncode}: {upgrade_errors}")
        rollout_seconds = time.perf_counter() - upgrade_start
        rolled = {"duration_seconds": rollout_seconds, "start_unix_ns": upgrade_start_ns, "end_unix_ns": upgrade_end_ns,
                  "helm_output": upgrade_output, "pods": stable_versions(cfg["upgrade_tag"]), "smoke": smoke()}
    finally:
        kubectl("exec", load_name, "--", "python", "-c", "from pathlib import Path;Path('/tmp/sentinel-stop').touch()", check=False)
        capture_load()
    wait_for(lambda: json.loads(kubectl("get", "pod", load_name, "-o", "json"))["status"].get("phase") == "Succeeded",
             30, description="load pod completion")
    capture_load()
    measured = [row for row in raw if row.get("phase") == "measured"]
    overlapping = [row for row in measured if row["start_unix_ns"] < upgrade_end_ns and row["end_unix_ns"] > upgrade_start_ns]
    if not overlapping:
        raise AssertionError("No measured requests overlapped the rolling upgrade")
    latencies = [row["seconds"] for row in measured]
    rolled["load"] = {"total_requests": len(measured), "failed_requests": sum(not row["valid"] for row in measured),
                      "valid_requests": sum(row["valid"] for row in measured),
                      "requests_overlapping_upgrade": len(overlapping),
                      "failed_requests_overlapping_upgrade": sum(not row["valid"] for row in overlapping),
                      "overlap_protocol": "request wall-clock interval intersects the host-observed Helm upgrade interval; Unix nanoseconds retained",
                      "p50_seconds": percentile(latencies, .5), "p95_seconds": percentile(latencies, .95),
                      "p99_seconds": percentile(latencies, .99), "raw_samples": raw,
                      "warmup_requests_excluded": cfg["warmup_requests"], "concurrency": cfg["load_concurrency"],
                      "timer_includes": "in-cluster HTTP request, response read and JSON/body validation; per-worker sleep excluded",
                      "percentiles": "linear interpolation on sorted raw request durations"}
    journal.record("helm_rollout_completed", result={key: value for key, value in rolled.items() if key != "load"},
                   load_summary={key: value for key, value in rolled["load"].items() if key != "raw_samples"})
    rollback_start = time.perf_counter()
    journal.record("helm_rollback_started", revision=1)
    rollback_output = helm("rollback", release, "1", "--wait", "--timeout", "5m")
    previous = stable_versions(cfg["initial_tag"])
    rollback = {"success": True, "duration_seconds": time.perf_counter() - rollback_start,
                "helm_output": rollback_output, "smoke": smoke(), "pods": previous,
                "history": json.loads(helm("history", release, "--output", "json"))}
    journal.record("helm_rollback_completed", **rollback)
    before = pods("gateway")
    victim = previous["serving_pods"]["gateway"][0]
    known = {pod["metadata"]["uid"] for pod in before}
    recovery_start = time.perf_counter()
    journal.record("gateway_pod_delete_started", pod=victim["metadata"]["name"], uid=victim["metadata"]["uid"])
    kubectl("delete", "pod", victim["metadata"]["name"], "--wait=false")

    def recovered():
        now = pods("gateway")
        fresh = [pod for pod in now if pod["metadata"]["uid"] not in known]
        for pod in fresh:
            if any(condition["type"] == "Ready" and condition["status"] == "True" for condition in pod["status"].get("conditions", [])):
                return {"name": pod["metadata"]["name"], "uid": pod["metadata"]["uid"]}
        return None

    new_pod = wait_for(recovered, 120, interval=.25, description="new replacement gateway Pod Ready")
    recovery = {"duration_seconds": time.perf_counter() - recovery_start, "deleted_pod": victim["metadata"]["name"],
                "deleted_uid": victim["metadata"]["uid"], "replacement": new_pod, "smoke": smoke(),
                "serving_state": stable_versions(cfg["initial_tag"]),
                "timer_includes": "delete API call through observing replacement Pod Ready; 250 ms polling"}
    journal.record("replacement_pod_ready", **recovery)
    return {"scope": "single-node kind, local/CI; the two image tags refer to the same measured source build",
            "cluster": cluster, "namespace": namespace, "install": installed, "rollout": rolled,
            "rollback": rollback, "pod_recovery": recovery, "all_assertions_passed": True,
            "cluster_version": json.loads(kubectl("version", "-o", "json")),
            "nodes": json.loads(kubectl("get", "nodes", "-o", "json"))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "ops/config.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    journal = RunJournal(args.output)
    values_path = ROOT / ".runtime/ops/kind-secret-values.json"
    values_path.parent.mkdir(parents=True, exist_ok=True)
    values_path.write_text(json.dumps({"auth": {"postgresPassword": secrets.token_hex(24), "jwtSecret": secrets.token_hex(32)}}))
    values_path.chmod(0o600)
    started = time.perf_counter()
    report = {}
    try:
        journal.record("run_started", provenance=provenance(args.config, started))
        report = {"status": "passed", "result": run(config, values_path, journal)}
    except Exception as error:
        report = {"status": "failed", "error": str(error)}
        journal.record("kind_run_failed", error=str(error))
        cfg = config["kind"]
        for action in (["get", "pods", "-o", "json"], ["get", "events", "-o", "json"], ["logs", "sentinel-m1b-load"]):
            try:
                diagnostic = command(["kubectl", "--context", "kind-" + cfg["cluster"], "--namespace", cfg["namespace"], *action],
                                     timeout=15, check=False)
                journal.record("failure_diagnostic", command=action, exit_code=diagnostic.returncode,
                               stdout=diagnostic.stdout, stderr=diagnostic.stderr)
            except Exception as diagnostic_error:
                journal.record("failure_diagnostic_unavailable", command=action, error=str(diagnostic_error))
        raise
    finally:
        report["provenance"] = provenance(args.config, started)
        report["raw_event_log"] = str(journal.path.relative_to(ROOT))
        report["progress_events"] = journal.events
        journal.close()
        write_json(args.output, report)


if __name__ == "__main__":
    main()
