"""Scan only the console container belonging to this isolated Compose project."""
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import uuid

import httpx
from gateway.client import graphql
from security.scan.run_dast import validate_report
from security.availability import availability

ROOT = Path(__file__).resolve().parents[1]
TARGET = "http://127.0.0.1:13040"


def publish_redacted(raw, reports, token, local_secrets=()):
    """Keep original scanner evidence private and remove account secrets from copies."""
    counts = {}
    for path in raw.iterdir():
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        count = content.count(token)
        content = content.replace(token, "[REDACTED_ANALYST_JWT]")
        content, extra = re.subn(
            r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
            "[REDACTED_JWT]", content,
        )
        password = "local-zap-scan-account"
        count += extra + content.count(password)
        content = content.replace(password, "[REDACTED_SCANNER_PASSWORD]")
        for value in (*local_secrets, "local-load-test-account", "local-integration-password"):
            if value:
                count += content.count(value)
                content = content.replace(value, "[REDACTED_LOCAL_CREDENTIAL]")
        (reports / path.name).write_text(content, encoding="utf-8")
        counts[path.name] = count
    return counts


def run():
    docker = os.getenv("DOCKER_BIN", "docker")
    project = os.getenv("COMPOSE_PROJECT_NAME", "sentinel-risk")
    containers = subprocess.check_output([docker,"ps","--filter",f"label=com.docker.compose.project={project}","--filter","label=com.docker.compose.service=console","--format","{{.ID}}"],text=True).split()
    if len(containers)!=1:
        raise RuntimeError("Expected exactly one owned Compose console")
    with socket.socket() as probe: probe.bind(("127.0.0.1",18049))
    private = Path(os.getenv("SENTINEL_PRIVATE", ROOT/".runtime")); private.mkdir(parents=True,exist_ok=True)
    zap_home = private / "zap-home";zap_home.mkdir(exist_ok=True);zap_home.chmod(0o777)
    with httpx.Client(timeout=30) as client:
        response = client.get(TARGET);response.raise_for_status()
        if "Sentinel" not in response.text: raise RuntimeError("Wrong application at console port")
        auth = graphql(client,TARGET+"/graphql",'mutation($e:String!){register(email:$e,password:"local-zap-scan-account"){token user{id}}}',{"e":"scan-"+uuid.uuid4().hex+"@example.com"})
    token_path = private / "zap-analyst-token"
    token_path.write_text(auth["register"]["token"]);token_path.chmod(0o644)
    reports = ROOT / "results/security/zap";reports.mkdir(parents=True,exist_ok=True);reports.chmod(0o777)
    raw = private / ("zap-raw-" + uuid.uuid4().hex)
    raw.mkdir(mode=0o777);raw.chmod(0o777)
    local_secrets = [line.partition("=")[2].strip().strip("\"'") for line in (ROOT/".env").read_text().splitlines() if "=" in line]
    image = os.getenv("ZAP_IMAGE","ghcr.io/zaproxy/zaproxy:stable")
    source_revision = os.getenv("GITHUB_SHA") or subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    manifest = {"target":TARGET,"project":project,"owned_console_container":containers[0],"image":image,"source_revision":source_revision,
                "policy":"Unresolved high or medium alert types fail the gate; low and informational findings remain in the reports.",
                "redaction":"Original reports remain in the private runtime; public copies remove scanner JWTs and passwords without changing findings.","scans":[]}
    config = subprocess.check_output([docker,"exec",containers[0],"nginx","-T"],stderr=subprocess.STDOUT,text=True)
    (raw/"nginx-config.txt").write_text(config)
    manifest["console_nginx_version"] = subprocess.check_output([docker,"exec",containers[0],"nginx","-v"],stderr=subprocess.STDOUT,text=True).strip()
    try:
        for mode in ("baseline","full"):
            name = "sentinel-f1-zap-"+mode+"-"+uuid.uuid4().hex[:8]
            command=[docker,"run","--rm","--name",name,"--network=host","--cpus=2",
                     "-v",f"{raw}:/zap/wrk:rw","-v",f"{ROOT/'security/platform_scope.py'}:/zap/owned_scope.py:ro",
                     "-v",f"{zap_home}:/home/zap/.ZAP:rw",
                     "-v",f"{ROOT/'security/zap_capture.py'}:/zap/zap_capture.py:ro",
                     "-v",f"{ROOT/'security/zap_seed.py'}:/zap/zap_seed.py:ro","-e",f"SENTINEL_ANALYST_ID={auth['register']['user']['id']}",
                     "-e",f"SENTINEL_HTTP_EVIDENCE=/zap/wrk/{mode}-http.json","-e",f"SENTINEL_SOURCE_REVISION={source_revision}",
                     "-v",f"{token_path}:/zap/analyst-token:ro","-e",f"SENTINEL_SCOPE_PROOF=/zap/wrk/{mode}-scope.json",image,
                     "zap-baseline.py" if mode=="baseline" else "zap-full-scan.py","-t",TARGET,"-P","18049","-m","1","-T","5","-I",
                     "--hook","/zap/owned_scope.py","-J",mode+".json","-r",mode+".html"]
            if mode=="baseline": command.append("--autooff")
            record={"mode":mode,"container":name,"started_utc":datetime.now(timezone.utc).isoformat()};manifest["scans"].append(record)
            started=time.monotonic()
            try:
                with (raw/(mode+".log")).open("w") as log:
                    result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=17*60)
                record.update(returncode=result.returncode,wall_seconds=time.monotonic()-started)
                record.update(validate_report(raw/(mode+".json"),raw/(mode+".html"),TARGET,result.returncode))
                report=json.loads((raw/(mode+".json")).read_text());counts={str(i):0 for i in range(4)}
                for site in report["site"]:
                    for alert in site["alerts"]:counts[str(alert["riskcode"])]+=1
                record["risk_type_counts"]={"high":counts["3"],"medium":counts["2"],"low":counts["1"],"informational":counts["0"]}
                evidence=json.loads((raw/(mode+"-http.json")).read_text())
                if evidence.get("complete") is not True or evidence.get("temporary_messages_enabled") != "true":
                    raise RuntimeError("Original HTTP evidence capture incomplete; raw diagnostics retained")
                if (counts["3"] or counts["2"]) and not evidence["alerts"]:
                    raise RuntimeError("Reported high/medium findings have no original HTTP evidence")
                record["http_evidence"]={"file":mode+"-http.json","selected_messages":len(evidence["messages"]),
                    "alert_messages":len(evidence["alerts"]),"history_truncated":evidence["history_truncated"]}
                console_log = subprocess.check_output([docker,"logs","--since",record["started_utc"],containers[0]],stderr=subprocess.STDOUT,text=True)
                (raw/(mode+"-console.log")).write_text(console_log)
                record["availability"] = availability(evidence, console_log)
                if not record["availability"]["passed"]:
                    raise RuntimeError("Scanner observed HTTP502 or nginx upstream address exhaustion; raw evidence retained")
                proof=json.loads((raw/(mode+"-scope.json")).read_text())
                if (proof["target"]!=TARGET or not proof["authenticated_graphql_seed"]
                    or proof["authenticated_analyst_id"] != auth["register"]["user"]["id"]
                    or proof["active_scan_max_minutes"]!=5
                    or proof["graphql_generation_limits"]!={"query_depth":3,"args_depth":2,"additional_depth":0,"lenient_depth":"false"}):
                    raise RuntimeError("Scope/auth/generation-bound proof missing")
            finally:
                # This unique scanner is ours; leave every platform/shared service running.
                subprocess.run([docker,"rm","-f",name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                manifest["redacted_occurrences"] = publish_redacted(raw, reports, auth["register"]["token"], local_secrets)
                manifest["image_identity"] = json.loads(subprocess.check_output([docker,"image","inspect","--format",'{"id":{{json .Id}},"repo_digests":{{json .RepoDigests}}}',image],text=True))
                (reports/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    finally:
        token_path.unlink(missing_ok=True)
    manifest["security_gate_passed"] = not any(scan["risk_type_counts"][level] for scan in manifest["scans"] for level in ("high","medium"))
    (reports/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    if not manifest["security_gate_passed"]:
        raise RuntimeError("Unresolved high/medium console findings; reports retained for correction and triage")
    print(json.dumps(manifest,indent=2))


if __name__=="__main__":run()
