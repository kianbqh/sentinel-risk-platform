"""Prepare an ephemeral real-PG tool test, never print generated DSNs/secrets."""
import argparse
import json
import math
import os
from pathlib import Path
import secrets
from urllib.parse import quote,urlsplit

from copilot.db.migrate import migrate
from copilot.mcp_server.explanation import MODEL_SHA256
from copilot.scripts.create_credentials import create
from copilot.scripts.provision_roles import provision


def prepare(repo,private_dir,env_output,output_dir):
    import psycopg
    from psycopg.types.json import Jsonb
    root=Path(repo).resolve()
    directory=Path(private_dir).resolve()
    create(directory,"http://127.0.0.1:18070/mcp",hours=1)
    admin=os.environ["COPILOT_ADMIN_DSN"]
    migrate(admin,root,bootstrap=True)
    migrate(admin,root,bootstrap=False)  # Applied hash/Go ledger idempotence.
    provision(admin,directory)
    origin=urlsplit(admin)
    values={"COPILOT_HTTP_USE_DATABASE":"1","COPILOT_REPO_ROOT":str(root),
        "COPILOT_TOKEN_REGISTRY":str(directory/"registry.json"),"COPILOT_TOKEN_FILE":str(directory/"token")}
    for kind in ("reader","writer","indexer"):
        password=(directory/(kind+"-password")).read_text().strip()
        values["COPILOT_"+kind.upper()+"_DSN"]="postgresql://copilot_local_"+kind+":"+quote(password,safe="")+"@"+origin.netloc.split("@")[-1]+origin.path+"?sslmode=disable"
    a=json.loads((root/"streaming/model/lr_model.json").read_text())
    p=1/(1+math.exp(-a["intercept"]))
    with psycopg.connect(admin) as conn:
        conn.execute("""INSERT INTO alerts(id,event_time,source,entity_type,entity_id,risk_score,threshold,severity,summary,
            model_name,model_version,evidence,trace_id) VALUES('copilot-ci:1',now(),'transaction-api','transaction','copilot-ci:1',%s,%s,
            'low','Synthetic CI boundary fixture','logreg_balanced',%s,%s,'copilot-ci:1')""",
            (p,a["threshold"],MODEL_SHA256,Jsonb({"features":dict(zip(a["features"],a["mean"])),"synthetic":True})))
        cid=conn.execute("INSERT INTO cases(alert_id) VALUES('copilot-ci:1') RETURNING id").fetchone()[0]
        if cid!=1:raise ValueError("CI requires a fresh isolated database with fixture case1")
    configs=directory/"configs"
    configs.mkdir()
    checks={"repo_root":str(root),"environment":"GitHub-hosted runner","output_dir":str(Path(output_dir)/"checks"),
        "real_http":True,"real_database":True,"timeout_seconds":300,"require_clean_source":True}
    parity=json.loads((root/"copilot/configs/model-parity.json").read_text())
    parity.update(repo_root=str(root),environment="GitHub-hosted runner",output_dir=str(Path(output_dir)/"model-parity"),require_clean_source=True)
    for name,value in (("checks.json",checks),("model-parity.json",parity)):
        with (configs/name).open("x") as handle:json.dump(value,handle,indent=2)
    values["COPILOT_CI_CONFIG_DIR"]=str(configs)
    with Path(env_output).open("a") as handle:
        for key,value in values.items():
            if "\n" in value:raise ValueError("Unsafe CI environment value")
            handle.write(key+"="+value+"\n")
    print(json.dumps({"status":"prepared","database":"ephemeral real PG17+pgvector",
        "config_directory":str(configs),"secrets_printed":False,"fixture_is_synthetic":True}))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--repo",default=".")
    parser.add_argument("--private-dir",required=True);parser.add_argument("--env-output",required=True)
    parser.add_argument("--output-dir",required=True)
    args=parser.parse_args();prepare(args.repo,args.private_dir,args.env_output,args.output_dir)
