"""Admin setup only: import frozen case fixtures into the shared opt-in case DB."""
import argparse
import json
import os
from pathlib import Path

from copilot.mcp_server.contracts import positive_id
from copilot.scripts.provenance import RunRecord,sha256


def run(config_path):
    report=RunRecord(config_path)
    try:
        import psycopg
        from psycopg.types.json import Jsonb
        config=report.config
        protocol=Path(config["protocol"])
        if sha256(protocol)!=config["protocol_sha256"]:raise ValueError("Frozen agent protocol hash mismatch")
        payload=json.loads(protocol.read_text())
        if payload.get("schema_version")!="1.0" or payload.get("stage")!="source_frozen":
            raise ValueError("Evaluation import requires source-frozen v1 protocol")
        records=payload["cases"]
        if len(records)!=config["case_id_max"]-config["case_id_min"]+1:
            raise ValueError("Evaluation fixture count does not match reserved case interval")
        mapping=[]
        with psycopg.connect(os.environ[config["admin_dsn_env"]]) as conn:
            # Explicitly reserve only this fixture's declared interval. Fail on any
            # collision rather than silently replacing a human-reviewed case.
            conn.execute("SELECT pg_advisory_xact_lock(716203116)")
            for record in records:
                case=record["case"]
                cid=positive_id(case["case_id"])
                if not config["case_id_min"]<=cid<=config["case_id_max"]:raise ValueError("Evaluation case outside reserved ID interval")
                if case["assignee_id"] is not None:raise ValueError("Frozen fixture must not reference a live analyst")
                if conn.execute("SELECT 1 FROM cases WHERE id=%s OR alert_id=%s",(cid,case["alert_id"])).fetchone():
                    raise ValueError("Frozen case ID/alert already exists; refuse overwrite")
                conn.execute("""INSERT INTO alerts(id,event_time,source,entity_type,entity_id,risk_score,threshold,severity,summary,
                    model_name,model_version,evidence,trace_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (case["alert_id"],case["event_time"],case["source"],case["entity_type"],case["entity_id"],case["risk_score"],
                     case["threshold"],case["severity"],case["summary"],case["model_name"],case["model_version"],Jsonb(case["evidence"]),case["trace_id"]))
                conn.execute("INSERT INTO cases(id,alert_id,status,assignee_id,created_at,updated_at) VALUES(%s,%s,%s,NULL,%s,%s)",
                    (cid,case["alert_id"],case["status"],case["created_at"],case["updated_at"]))
                count=0
                for event in record.get("events",[]):
                    if event.get("case_id",cid)!=cid or event.get("actor") not in {"system",None}:
                        raise ValueError("Frozen setup events must be system events for this case")
                    if event["action"]=="COPILOT_DRAFT":raise ValueError("Evaluation setup cannot pre-create a copilot draft")
                    conn.execute("INSERT INTO case_events(case_id,actor_id,action,note,at) VALUES(%s,NULL,%s,%s,%s)",
                        (cid,event["action"],event.get("note",""),event["at"]))
                    count+=1
                mapping.append({"case_id":cid,"alert_id":case["alert_id"],"events_imported":count})
            conn.execute("SELECT setval(pg_get_serial_sequence('public.cases','id'),GREATEST((SELECT COALESCE(max(id),1) FROM cases),1),true)")
        report.write("case-mapping.json",mapping)
        report.finish({"status":"complete","protocol_sha256":sha256(protocol),"cases_imported":len(mapping),
            "ground_truth_imported":False,"scope":"admin fixture setup in SAME opt-in Go-console/copilot case database; no agent SQL access"})
    except Exception as exc:
        report.fail(type(exc).__name__);raise


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--config",required=True);run(parser.parse_args().config)
