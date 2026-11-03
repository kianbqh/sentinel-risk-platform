"""Same-case integration: MCP draft, console GraphQL decisions and live scoring."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import urllib.request
import uuid

from copilot.db.persistence import PostgresDraftStore
from copilot.mcp_server.client import MCPToolClient
from copilot.scripts.provenance import RunRecord


def graphql(url,query,variables,token=None):
    headers={"Content-Type":"application/json"}
    if token:headers["Authorization"]="Bearer "+token
    request=urllib.request.Request(url,data=json.dumps({"query":query,"variables":variables}).encode(),headers=headers)
    with urllib.request.urlopen(request,timeout=30) as response:body=json.load(response)
    if body.get("errors"):raise RuntimeError("Console GraphQL operation failed")
    return body["data"]


def verify(config_path):
    run=RunRecord(config_path)
    try:
        import psycopg
        from psycopg.rows import dict_row
        config=run.config
        suffix=uuid.uuid4().hex
        admin=os.environ[config["admin_dsn_env"]]
        with psycopg.connect(admin,row_factory=dict_row) as conn:
            ledger=conn.execute("SELECT version,dirty FROM schema_migrations").fetchall()
            if ledger != [{"version":1,"dirty":False}]:raise ValueError("Original Go migration is not clean v1")
            source=conn.execute("SELECT * FROM copilot.case_evidence WHERE case_id=%s",(config["source_case_id"],)).fetchone()
            if source is None or source["source"]!="transaction-api" or "features" not in source["evidence"]:
                raise ValueError("Human-review source must be a real card case with complete evidence")
            alert_id="copilot-human-review:"+suffix
            conn.execute("""INSERT INTO alerts(id,event_time,source,entity_type,entity_id,risk_score,threshold,severity,summary,
                model_name,model_version,evidence,trace_id) SELECT %s,event_time,source,entity_type,entity_id,risk_score,threshold,
                severity,summary,model_name,model_version,evidence,%s FROM alerts WHERE id=%s""",(alert_id,suffix,source["alert_id"]))
            cid=conn.execute("INSERT INTO cases(alert_id) VALUES(%s) RETURNING id",(alert_id,)).fetchone()["id"]

        def snapshot():
            with psycopg.connect(admin,row_factory=dict_row) as conn:
                case=conn.execute("SELECT status,assignee_id,updated_at FROM cases WHERE id=%s",(cid,)).fetchone()
                labels=conn.execute("SELECT actor_id,value,note,at FROM case_labels WHERE case_id=%s ORDER BY actor_id",(cid,)).fetchall()
                return case,labels

        before=snapshot()
        draft={"risk_level":"high","confidence":.8,"key_evidence":["Source-backed transaction evidence is available"],
            "recommended_next_step":"Ask an analyst to review the complete evidence"}
        store=PostgresDraftStore(os.environ[config["writer_dsn_env"]])
        event=store.save_turn(cid,run_id="same-case-human-review",redacted_memory=[{"role":"assistant","content":"Human review required"}],draft=draft)
        if snapshot()!=before:raise ValueError("Draft changed a human decision field")

        async def read_and_score():
            async with MCPToolClient(config["mcp_endpoint"],Path(os.environ[config["token_file_env"]]).read_text().strip()) as tools:
                case=await tools.call("get_case",{"case_id":cid})
                history=await tools.call("list_case_events",{"case_id":cid})
                score=await tools.call("score_transaction",{"features":source["evidence"]["features"]})
                explanation=await tools.call("explain_score",{"case_id":cid})
            if not all(value["ok"] for value in (case,history,score,explanation)):raise ValueError("Live MCP read/scoring failed")
            if score["data"]["alert_published"] is not False:raise ValueError("Tool scoring published an alert")
            if abs(score["data"]["fraud_score"]-source["risk_score"])>1e-10:raise ValueError("Live gateway differs from stored source score")
            if not any(row["actor"]=="copilot" and row["action"]=="COPILOT_DRAFT" for row in history["data"]["events"]):
                raise ValueError("MCP cannot see its draft on the same case")
            return score["data"],explanation["data"]["probability"]

        score,probability=asyncio.run(read_and_score())
        # Use the console's real /graphql reverse proxy and the unchanged analyst
        # auth/mutations. No service token is used for human decision operations.
        auth=graphql(config["console_graphql"],"mutation($e:String!,$p:String!){register(email:$e,password:$p){token user{id}}}",
            {"e":"copilot-review-"+suffix+"@example.invalid","p":secrets.token_urlsafe(32)})["register"]
        token=auth["token"]
        query="query($id:ID!){case(id:$id){id status assigneeId events{action note} labels{value}}}"
        read=graphql(config["console_graphql"],query,{"id":str(cid)},token)["case"]
        if read["status"]!="OPEN" or read["labels"] or not any(row["action"]=="COPILOT_DRAFT" for row in read["events"]):
            raise ValueError("Console cannot read the same unapplied draft")
        graphql(config["console_graphql"],"mutation($id:ID!){labelCase(id:$id,value:BENIGN,note:\"Explicit human integration review\"){id}}",{"id":str(cid)},token)
        graphql(config["console_graphql"],"mutation($id:ID!){resolveCase(id:$id,note:\"Explicit human integration review\"){id}}",{"id":str(cid)},token)
        final=graphql(config["console_graphql"],query,{"id":str(cid)},token)["case"]
        if final["status"]!="RESOLVED" or [row["value"] for row in final["labels"]]!=["BENIGN"]:
            raise ValueError("Explicit human label/resolve did not persist")
        with psycopg.connect(admin,row_factory=dict_row) as conn:
            stored=conn.execute("SELECT actor,action,note FROM case_events WHERE id=%s",(event["event_id"],)).fetchone()
            if stored["actor"]!="copilot" or stored["action"]!="COPILOT_DRAFT" or json.loads(stored["note"])!=draft:
                raise ValueError("Human workflow changed the copilot draft")
        run.finish({"status":"passed","case_id":cid,"source_case_id":config["source_case_id"],"alert_id":alert_id,
            "database":"same opt-in sentinel_copilot for Go console, MCP reader and controlled draft writer",
            "go_schema_migrations":ledger,"draft_preserved_decisions":True,"draft_actor":"copilot", "draft_event_id":event["event_id"],
            "console_graphql":config["console_graphql"],"before_human_status":read["status"],"final_human_status":final["status"],
            "final_human_labels":[row["value"] for row in final["labels"]],"draft_unchanged_after_human_review":True,
            "actual_gateway_scoring":score,"exact_explanation_probability":probability,
            "scope":"new cloned integration case only; fixed evaluation fixtures and old platform DB are untouched"})
    except Exception as exc:
        run.fail(type(exc).__name__);raise


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--config",required=True);verify(parser.parse_args().config)
