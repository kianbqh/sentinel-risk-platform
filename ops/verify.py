#!/usr/bin/env python3
"""Real HTTP/Jaeger/Kafka/Postgres observations; no simulated drill results."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evidence import ROOT, RunJournal, command, provenance, write_json


def http(url, body=None, *, headers=None, timeout=8):
    headers = {"Content-Type": "application/json", **(headers or {})}
    request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode()
            return response.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            pass
        return error.code, raw


def wait_for(predicate, timeout, interval=1, description="condition"):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (OSError, ValueError, RuntimeError, AssertionError, KeyError):
            pass
        time.sleep(interval)
    raise RuntimeError(f"Timed out waiting for {description}; last={last!r}")


def synthetic_features():
    """A model-directed synthetic ops input, never an accuracy benchmark."""
    model = json.loads((ROOT / "streaming/model/lr_model.json").read_text())
    return {name: mean + (12 if coefficient >= 0 else -12) * scale
            for name, mean, scale, coefficient in zip(model["features"], model["mean"], model["scale"], model["coef"])}


def predict(config, identity, trace_id=None):
    headers = None
    if trace_id:
        headers = {"traceparent": f"00-{trace_id}-0123456789abcdef-01"}
    code, body = http(config["gateway"] + "/v1/transactions/score", {
        "features": synthetic_features(), "transaction_id": identity, "publish": True}, headers=headers)
    if code != 200 or not body.get("flagged") or not body.get("alert_published"):
        raise RuntimeError(f"Prediction did not publish: {code}, {body}")
    return body


def validate_trace(trace, expected_names, expected_services):
    spans = trace["spans"]
    by_id = {span["spanID"]: span for span in spans}
    names = {span["operationName"] for span in spans}
    services = {process["serviceName"] for process in trace["processes"].values()}
    if not set(expected_names) <= names or not set(expected_services) <= services:
        raise AssertionError(f"Trace missing spans/services: {names}, {services}")
    # Validate parent ancestry, not just four unrelated spans sharing an ID.
    selected = [next(span for span in spans if span["operationName"] == name) for name in expected_names]
    for parent, child in zip(selected, selected[1:]):
        pending = [child]
        visited = set()
        while pending and parent["spanID"] not in visited:
            node = pending.pop()
            for reference in node.get("references", []):
                if reference["refType"] == "CHILD_OF" and reference["spanID"] not in visited:
                    visited.add(reference["spanID"])
                    if reference["spanID"] in by_id:
                        pending.append(by_id[reference["spanID"]])
        if parent["spanID"] not in visited:
            raise AssertionError(f"No parent path {parent['operationName']} -> {child['operationName']}")
    if len({span["traceID"] for span in spans}) != 1:
        raise AssertionError("Jaeger response spans do not belong to one trace")
    return {"span_count": len(spans), "span_names": sorted(names), "services": sorted(services),
            "parent_chain_verified": expected_names}


def smoke(config, journal=None):
    observed = {}
    for name in ("gateway", "backend"):
        for endpoint in ("/healthz", "/readyz"):
            code, body = http(config[name] + endpoint)
            if journal:
                journal.record("http_health", service=name, endpoint=endpoint, status=code, body=body)
            if code != 200:
                raise AssertionError(f"{name}{endpoint} returned {code}: {body}")
            observed[name + endpoint] = {"status": code, "body": body}
    code, targets = http(config["prometheus"] + "/api/v1/targets")
    if code != 200 or targets["status"] != "success":
        raise AssertionError("Prometheus targets API is unavailable")
    active = targets["data"]["activeTargets"]
    if journal:
        journal.record("prometheus_targets", targets=active)
    expected = set(config["expected_scrape_jobs"])
    good = {row["labels"]["job"] for row in active if row["health"] == "up"}
    if not expected <= good:
        raise AssertionError(f"Missing up scrape jobs: {expected - good}")
    code, grafana_health = http(config["grafana"] + "/api/health")
    if journal:
        journal.record("grafana_health", status=code, response=grafana_health)
    if code != 200 or grafana_health.get("database") != "ok":
        raise AssertionError("Grafana health API is not ready")
    # Read the generated local credential without serializing it or the auth header.
    env_file = Path(os.environ.get("OPS_ENV_FILE", ROOT / ".runtime/ops/.env"))
    values = dict(line.split("=", 1) for line in env_file.read_text().splitlines() if line and not line.startswith("#") and "=" in line)
    password = values.get("GRAFANA_PASSWORD")
    if not password:
        raise RuntimeError("Generated ops credential file has no Grafana password")
    auth = {"Authorization": "Basic " + base64.b64encode(("sentinel:" + password).encode()).decode()}
    code, dashboard = http(config["grafana"] + "/api/dashboards/uid/sentinel-ops", headers=auth)
    if code != 200:
        raise AssertionError("Authenticated Grafana dashboard API failed")
    code, datasource = http(config["grafana"] + "/api/datasources/uid/sentinel-prometheus", headers=auth)
    if code != 200 or datasource.get("uid") != "sentinel-prometheus" or datasource.get("type") != "prometheus":
        raise AssertionError("Provisioned Grafana Prometheus datasource does not match")
    expected_dashboard = json.loads((ROOT / "ops/grafana/dashboards/sentinel.json").read_text())
    panels = dashboard["dashboard"]["panels"]
    wanted = expected_dashboard["panels"]
    if len(panels) != len(wanted):
        raise AssertionError("Provisioned Grafana panel count differs from committed dashboard")
    for panel, configured in zip(panels, wanted):
        if (panel["id"] != configured["id"] or panel["datasource"]["uid"] != "sentinel-prometheus"
                or [target["expr"] for target in panel["targets"]] != [target["expr"] for target in configured["targets"]]):
            raise AssertionError("Provisioned Grafana panel queries differ from committed dashboard")
    grafana = {"health": grafana_health, "dashboard_uid": dashboard["dashboard"]["uid"],
               "panel_count": len(panels), "panels_and_queries_verified": panels,
               "datasource_uid": datasource["uid"], "datasource_type": datasource["type"],
               "datasource_url": datasource["url"], "authenticated_api_verified": True}
    if journal:
        journal.record("grafana_provisioning_verified", result=grafana)
    ids = compose("ps", "-q").split()
    image_format = '{"name":{{json .Name}},"requested_image":{{json .Config.Image}},"image_id":{{json .Image}}}'
    images = [json.loads(line) for line in command(["docker", "inspect", "--format", image_format, *ids]).stdout.splitlines()]
    package_source = 'import importlib.metadata,json;print(json.dumps({n:importlib.metadata.version(n) for n in ["fastapi","prometheus-client","opentelemetry-sdk","opentelemetry-exporter-otlp-proto-http","kafka-python","psycopg","scikit-learn"]}))'
    packages = json.loads(compose("exec", "-T", "gateway", "python", "-c", package_source))
    return {"health": observed, "metric_endpoints_scraped": len(expected), "targets": active,
            "grafana": grafana, "runtime_container_images": images, "gateway_package_versions": packages}


def trace_test(config, journal=None):
    trace_id = uuid.uuid4().hex
    identity = "ops-trace-" + uuid.uuid4().hex
    prediction = predict(config, identity, trace_id)
    if journal:
        journal.record("trace_prediction_published", trace_id=trace_id, prediction=prediction)

    def fetch():
        status, response = http(config["jaeger"] + "/api/traces/" + trace_id)
        if journal:
            journal.record("jaeger_query", status=status, response=response)
        if status != 200 or not response.get("data"):
            return None
        trace = response["data"][0]
        try:
            summary = validate_trace(trace, config["trace_span_names"], config["trace_services"])
        except (AssertionError, KeyError):
            return None
        return {"trace_id": trace_id, "prediction": prediction, **summary, "raw_trace": trace}

    return wait_for(fetch, config["timeout_seconds"], description="cross-service parented Jaeger trace")


def spark_test(config, journal=None):
    """Feed real bundled input rows; capture actual listener counters from Prometheus."""
    count = config["spark_probe_rows"]
    source = '''import json,os,sys
import pandas as pd
from pathlib import Path
from kafka import KafkaProducer
rows=pd.read_parquet(next(Path("gateway/fixtures").glob("*.parquet"))).head(int(sys.argv[1]))
p=KafkaProducer(bootstrap_servers=os.environ["KAFKA_BOOTSTRAP_SERVERS"].split(","),acks="all",value_serializer=lambda x:json.dumps(x).encode())
delivered=[]
for row in rows.itertuples(index=False):
 delivered.append(p.send("sentinel.transactions.v1",value={"txn_id":int(row.txn_id),"time":float(row.time),"amount":float(row.amount),"label":int(row.label),"v":list(map(float,row.v)),"produce_ts_ms":0}).get(timeout=20).offset)
p.send("sentinel.transactions.v1",value={"txn_id":-1,"time":float.fromhex("0x1.fffffffffffffp+1023"),"amount":0.,"label":0,"v":[0.]*28,"produce_ts_ms":0}).get(timeout=20)
p.close()
print(json.dumps({"dataset":"bundled OpenML 1597 real prefix; not an accuracy benchmark","rows":len(rows),"delivered_offsets":delivered,"flush_markers":1}))
'''
    def query(expression):
        status, body = http(config["prometheus"] + "/api/v1/query?" + urllib.parse.urlencode({"query": expression}))
        if status != 200 or body["status"] != "success":
            raise RuntimeError("Prometheus query failed")
        return body["data"]["result"]

    expressions = ["sentinel_spark_input_rows_total", "sentinel_spark_batch_input_rows", "sentinel_spark_batch_duration_seconds_count",
                   "sentinel_spark_batch_duration_seconds_sum", "sentinel_spark_scoring_duration_seconds_count",
                   "sentinel_spark_last_progress_timestamp_seconds", "sentinel_spark_last_activity_timestamp_seconds",
                   "sentinel_spark_query_active"]

    def snapshot():
        values = {expression: query(expression) for expression in expressions}
        if journal:
            journal.record("spark_prometheus_snapshot", queries=values)
        return values

    def scalar(snapshot, name):
        rows = snapshot[name]
        return sum(float(row["value"][1]) for row in rows)

    initial = snapshot()
    initial_rows = scalar(initial, "sentinel_spark_input_rows_total")
    initial_scores = scalar(initial, "sentinel_spark_scoring_duration_seconds_count")
    published = json.loads(compose("exec", "-T", "gateway", "python", "-c", source, str(count), timeout=120))
    if journal:
        journal.record("spark_input_published", **published)

    def completed():
        current = snapshot()
        return current if (scalar(current, "sentinel_spark_input_rows_total") - initial_rows >= count + 1
                           and scalar(current, "sentinel_spark_scoring_duration_seconds_count") - initial_scores >= count) else None

    final = wait_for(completed, config["timeout_seconds"], description="new input and successful scoring counters cover the published run")
    deltas = {"input_rows": scalar(final, "sentinel_spark_input_rows_total") - initial_rows,
              "successful_scoring_calls": scalar(final, "sentinel_spark_scoring_duration_seconds_count") - initial_scores}
    if deltas != {"input_rows": count + published["flush_markers"], "successful_scoring_calls": count}:
        raise AssertionError(f"Unexpected Spark counter deltas (including possible task recomputation): {deltas}")
    return {"published": published, "initial_prometheus_queries": initial,
            "counter_deltas": deltas, "prometheus_queries": final, "all_assertions_passed": True}


def compose_base():
    project = os.environ.get("COMPOSE_PROJECT_NAME", "sentinel-m1b-ops")
    if not re.fullmatch(r"sentinel-m1b-[a-z0-9-]+", project):
        raise ValueError("Drill project must start with sentinel-m1b- to protect unrelated containers")
    files = ["-f", "compose.yml", "-f", "ops/compose.ops.yml"]
    if os.environ.get("HOST_NETWORK") == "1":
        files += ["-f", "compose.hostnet.yml", "-f", "ops/compose.ops.hostnet.yml"]
    env_file = os.environ.get("OPS_ENV_FILE", str(ROOT / ".runtime/ops/.env"))
    return ["docker", "compose", "--env-file", env_file, "--project-name", project, *files, "--profile", "ops"]


def compose(*args, input_text=None, timeout=40):
    return command([*compose_base(), *args], input_text=input_text, timeout=timeout).stdout


def offsets():
    # OffsetFetch and ListOffsets only; this observer never joins/commits the group.
    source = '''import json,os
from kafka import KafkaConsumer,TopicPartition
c=KafkaConsumer(bootstrap_servers=os.environ["KAFKA_BOOTSTRAP_SERVERS"].split(","),group_id="sentinel-console-v1",enable_auto_commit=False)
p=TopicPartition("sentinel.alerts.v1",0)
print(json.dumps({"committed":c.committed(p),"end":c.end_offsets([p])[p]}))
c.close()
'''
    return json.loads(compose("exec", "-T", "gateway", "python", "-c", source))


def db_counts(prefix):
    if not re.fullmatch(r"ops-drill-[a-f0-9]+-", prefix):
        raise ValueError("Unsafe drill ID")
    sql = f"""SELECT json_build_object(
      'alerts',(SELECT count(*) FROM alerts WHERE entity_id LIKE '{prefix}%'),
      'cases',(SELECT count(*) FROM cases c JOIN alerts a ON c.alert_id=a.id WHERE a.entity_id LIKE '{prefix}%'),
      'unique_case_alert_ids',(SELECT count(DISTINCT c.alert_id) FROM cases c JOIN alerts a ON c.alert_id=a.id WHERE a.entity_id LIKE '{prefix}%'),
      'ingestion_events',(SELECT count(*) FROM case_events e JOIN cases c ON e.case_id=c.id JOIN alerts a ON c.alert_id=a.id WHERE a.entity_id LIKE '{prefix}%'));
    """
    return json.loads(compose("exec", "-T", "postgres", "psql", "-U", "sentinel", "-d", "sentinel", "-Atc", sql))


def drill(config, journal=None):
    # Verify exact ownership before the only fault injection: stopping this project's DB.
    cid = compose("ps", "-q", "postgres").strip()
    labels = json.loads(command(["docker", "inspect", "--format", "{{json .Config.Labels}}", cid]).stdout)
    project = compose_base()[compose_base().index("--project-name") + 1]
    if labels.get("com.docker.compose.project") != project or labels.get("com.docker.compose.service") != "postgres":
        raise AssertionError("Database container ownership does not match the selected ops project")
    baseline = wait_for(lambda: (lambda row: row if row["committed"] == row["end"] else None)(offsets()),
                        config["timeout_seconds"], description="empty initial alert backlog")
    start = time.perf_counter()
    timeline = []

    def record(event, **values):
        timeline.append({"seconds": time.perf_counter() - start, "event": event, **values})
        if journal:
            journal.record(event, **values)

    prefix = "ops-drill-" + uuid.uuid4().hex + "-"
    messages = config["drill_unique_messages"]
    restored = False
    try:
        compose("stop", "-t", "10", "postgres")
        record("postgres_stopped", container_id=cid, initial_offsets=baseline)

        def not_ready():
            states = {name: http(config[name] + "/readyz")[0] for name in ("gateway", "backend")}
            lives = {name: http(config[name] + "/healthz")[0] for name in ("gateway", "backend")}
            record("health_observation", ready=states, live=lives)
            return states if all(value == 503 for value in states.values()) and all(value == 200 for value in lives.values()) else None

        wait_for(not_ready, 30, description="both readiness checks fail with liveness intact")
        for i in range(messages):
            predict(config, prefix + str(i))
        predict(config, prefix + "0")  # A real duplicate record, idempotency must suppress it.
        high_water = offsets()["end"]
        expected_wire = messages + 1
        if high_water - baseline["end"] != expected_wire:
            raise AssertionError("The drill's Kafka record count does not match its predictions")
        record("backlog_published", unique_messages=messages, wire_records=expected_wire, end=high_water)
        samples = []
        alert = None
        deadline = time.monotonic() + config["drill_alert_timeout_seconds"]
        while time.monotonic() < deadline:
            offset = offsets()
            if offset["committed"] != baseline["committed"]:
                raise AssertionError("Consumer committed an offset while Postgres was stopped")
            code, body = http(config["prometheus"] + "/api/v1/alerts")
            if code != 200 or body["status"] != "success":
                raise AssertionError("Prometheus alerts API failed")
            firing = [row for row in body["data"]["alerts"]
                      if row["labels"]["alertname"] == "SentinelServiceNotReady" and row["state"] == "firing"]
            samples.append({"seconds": time.perf_counter() - start, "offsets": offset,
                            "alerts": body["data"]["alerts"]})
            record("outage_offset_alert_sample", offsets=offset, alerts=body["data"]["alerts"])
            if firing:
                alert = firing
                record("prometheus_alert_firing", alerts=firing)
                break
            # Readiness probes also give an independent live HTTP observation.
            not_ready()
            time.sleep(config["poll_seconds"])
        if alert is None:
            raise AssertionError("The real Prometheus readiness alert did not fire")
        alert_seconds = time.perf_counter() - start
        compose("start", "postgres")
        restored = True
        restore = time.perf_counter()
        record("postgres_restored")

        def drained():
            offset = offsets()
            record("offset_recovery", offsets=offset)
            return offset if offset["committed"] == high_water else None

        final_offsets = wait_for(drained, config["timeout_seconds"], description="backlog completely committed")
        drain_seconds = time.perf_counter() - restore
        wait_for(lambda: all(http(config[name] + "/readyz")[0] == 200 for name in ("gateway", "backend")),
                 30, description="readiness recovered")
        counts = db_counts(prefix)
        if any(counts[name] != messages for name in ("alerts", "cases", "unique_case_alert_ids", "ingestion_events")):
            raise AssertionError(f"Lost or duplicate cases/history after recovery: {counts}")
        record("database_reconciled", counts=counts)
        return {"protocol": "real project-scoped DB stop; real OffsetFetch and Prometheus API; synthetic predictions",
                "postgres_container_id": cid, "project": project, "prefix": prefix,
                "unique_messages": messages, "wire_records": expected_wire,
                "alert_time_to_fire_seconds": alert_seconds,
                "backlog_drain_seconds": drain_seconds,
                "duplicate_cases": counts["cases"] - counts["unique_case_alert_ids"],
                "database_counts": counts, "initial_offsets": baseline, "final_offsets": final_offsets,
                "offsets_during_outage": samples, "timeline": timeline, "all_assertions_passed": True}
    except Exception as error:
        record("drill_failed", error=str(error))
        raise
    finally:
        if not restored:
            # Always repair the fault we own, including on assertion failures.
            compose("start", "postgres")
            record("postgres_restored_after_failure")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("smoke", "trace", "spark", "drill"))
    parser.add_argument("--config", type=Path, default=ROOT / "ops/config.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    started = time.perf_counter()
    journal = RunJournal(args.output)
    report = {}
    try:
        journal.record("run_started", mode=args.mode, provenance=provenance(args.config, started))
        run = {"smoke": smoke, "trace": trace_test, "spark": spark_test, "drill": drill}[args.mode]
        result = wait_for(lambda: run(config, journal), config["timeout_seconds"], description="all service and scrape endpoints ready") if args.mode == "smoke" else run(config, journal)
        report = {"status": "passed", "mode": args.mode, "result": result}
    except Exception as error:
        report = {"status": "failed", "mode": args.mode, "error": str(error)}
        raise
    finally:
        report["provenance"] = provenance(args.config, started)
        report["raw_event_log"] = str(journal.path.relative_to(ROOT))
        report["progress_events"] = journal.events
        journal.close()
        write_json(args.output, report)


if __name__ == "__main__":
    main()
