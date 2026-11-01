"""Scan only the console container belonging to this isolated Compose project."""
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import uuid

import httpx
from gateway.demo import graphql
from security.scan.run_dast import validate_report

ROOT = Path(__file__).resolve().parents[1]
TARGET = "http://127.0.0.1:13040"


def publish_redacted(raw, reports, token):
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
    with httpx.Client(timeout=30) as client:
        response = client.get(TARGET);response.raise_for_status()
        if "Sentinel" not in response.text: raise RuntimeError("Wrong application at console port")
        auth = graphql(client,TARGET+"/graphql",'mutation($e:String!){register(email:$e,password:"local-zap-scan-account"){token user{id}}}',{"e":"scan-"+uuid.uuid4().hex+"@example.com"})
    token_path = private / "zap-analyst-token"
    token_path.write_text(auth["register"]["token"]);token_path.chmod(0o644)
    reports = ROOT / "results/security/zap";reports.mkdir(parents=True,exist_ok=True);reports.chmod(0o777)
    raw = private / ("zap-raw-" + uuid.uuid4().hex)
    raw.mkdir(mode=0o777);raw.chmod(0o777)
    image = os.getenv("ZAP_IMAGE","ghcr.io/zaproxy/zaproxy:stable")
    manifest = {"target":TARGET,"project":project,"owned_console_container":containers[0],"image":image,
                "redaction":"Original reports remain in the private runtime; public copies remove scanner JWTs and passwords without changing findings.","scans":[]}
    try:
        for mode in ("baseline","full"):
            name = "sentinel-f1-zap-"+mode+"-"+uuid.uuid4().hex[:8]
            command=[docker,"run","--rm","--name",name,"--network=host","--cpus=2",
                     "-v",f"{raw}:/zap/wrk:rw","-v",f"{ROOT/'security/platform_scope.py'}:/zap/owned_scope.py:ro",
                     "-v",f"{ROOT/'security/zap_seed.py'}:/zap/zap_seed.py:ro","-e",f"SENTINEL_ANALYST_ID={auth['register']['user']['id']}",
                     "-v",f"{token_path}:/zap/analyst-token:ro","-e",f"SENTINEL_SCOPE_PROOF=/zap/wrk/{mode}-scope.json",image,
                     "zap-baseline.py" if mode=="baseline" else "zap-full-scan.py","-t",TARGET,"-P","18049","-m","1","-T","5","-I",
                     "--hook","/zap/owned_scope.py","-J",mode+".json","-r",mode+".html"]
            if mode=="baseline": command.append("--autooff")
            record={"mode":mode,"container":name};manifest["scans"].append(record)
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
                proof=json.loads((raw/(mode+"-scope.json")).read_text())
                if proof["target"]!=TARGET or not proof["authenticated_graphql_seed"] or proof["active_scan_max_minutes"]!=5:raise RuntimeError("Scope/auth proof missing")
            finally:
                # This unique scanner is ours; leave every platform/shared service running.
                subprocess.run([docker,"rm","-f",name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                manifest["redacted_occurrences"] = publish_redacted(raw, reports, auth["register"]["token"])
                (reports/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    finally:
        token_path.unlink(missing_ok=True)
    print(json.dumps(manifest,indent=2))


if __name__=="__main__":run()
