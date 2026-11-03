"""Import retained source envelopes into the separate opt-in copilot demo DB."""
import argparse
import json
import os
from pathlib import Path

from copilot.scripts.provenance import RunRecord,sha256


def run(config_path):
    report=RunRecord(config_path)
    try:
        import psycopg
        from psycopg.types.json import Jsonb
        config=report.config
        events={}
        sources={}
        for entry in config["sources"]:
            path=report.root/entry["path"]
            if sha256(path)!=entry["sha256"]: raise ValueError("Retained envelope source hash mismatch")
            sources[entry["path"]]=entry["sha256"]
            rows=[json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.suffix==".jsonl" else json.loads(path.read_text())["events"]
            for event in rows:
                old=events.get(event["alert_id"])
                if old is not None and old!=event: raise ValueError("Conflicting duplicate source alert")
                events[event["alert_id"]]=event
        with psycopg.connect(os.environ[config["admin_dsn_env"]]) as conn:
            inserted=0
            for event in events.values():
                result=conn.execute("""INSERT INTO public.alerts(id,event_time,source,entity_type,entity_id,risk_score,threshold,severity,summary,
                    model_name,model_version,evidence,trace_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(id) DO NOTHING RETURNING id""",(event["alert_id"],event["event_time"],event["source"],event["entity_type"],
                    event["entity_id"],event["risk_score"],event["threshold"],event["severity"],event["summary"],event["model"]["name"],
                    event["model"]["version"],Jsonb(event["evidence"]),event["trace_id"])).fetchone()
                inserted+=result is not None
                case=conn.execute("INSERT INTO public.cases(alert_id) VALUES(%s) ON CONFLICT(alert_id) DO NOTHING RETURNING id",(event["alert_id"],)).fetchone()
                if case: conn.execute("INSERT INTO public.case_events(case_id,action,note) VALUES(%s,'INGESTED',%s)",(case[0],event["source"]))
        report.finish({"status":"complete","source_hashes":sources,"unique_source_alerts":len(events),"new_alerts":inserted,
            "scope":"retained envelopes copied into isolated copilot demo DB; original DB and cases untouched"})
    except Exception as exc:
        report.fail(type(exc).__name__);raise


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--config",required=True);run(parser.parse_args().config)
