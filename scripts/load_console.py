"""Bounded local GraphQL load; validate every body and retain each request latency."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
from urllib.parse import urlsplit
import uuid

import httpx
import numpy as np
from gateway.client import graphql

QUERY = """{me{id} alerts(limit:50){id source severity riskScore threshold case{id status events{action at} labels{value actorId}}} stats{total open investigating resolved assigned}}"""


def validate(body, expected_ids, analyst):
    if body.get("errors") or not isinstance(body.get("data"), dict):
        raise ValueError("GraphQL errors or missing data")
    data=body["data"]
    if data["me"]["id"]!=analyst: raise ValueError("Wrong analyst")
    rows=data["alerts"]
    if not isinstance(rows,list) or tuple(row["id"] for row in rows)!=expected_ids: raise ValueError("Changed or incomplete alert page")
    stats=data["stats"]
    if stats["total"]!=stats["open"]+stats["investigating"]+stats["resolved"] or stats["total"]<len(rows): raise ValueError("Bad case totals")
    for row in rows:
        if row["source"] not in ("transaction-stream","transaction-api","ato","text-risk"): raise ValueError("Invalid source")
        if row["severity"] not in ("high","medium","low"): raise ValueError("Invalid severity")
        for number in (row["riskScore"],row["threshold"]):
            if number is not None and (not isinstance(number,(int,float)) or not math.isfinite(number) or not 0<=number<=1): raise ValueError("Invalid probability")
        case=row["case"]
        if not case["id"] or case["status"] not in ("OPEN","INVESTIGATING","RESOLVED") or not case["events"] or not isinstance(case["labels"],list): raise ValueError("Incomplete case history")
    return len(rows)


def phase(endpoint,token,analyst,expected,concurrency,count,output):
    limits=httpx.Limits(max_connections=concurrency,max_keepalive_connections=concurrency)
    headers={"Authorization":"Bearer "+token}
    def request(index):
        started=time.perf_counter();record={"request":index,"concurrency":concurrency,"http_status":None,"valid":False}
        try:
            response=client.post(endpoint,json={"query":QUERY},headers=headers)
            record["http_status"]=response.status_code
            response.raise_for_status()
            record["validated_alerts"]=validate(response.json(),expected,analyst)
            record["valid"]=True
        except Exception as error:
            record["error"]=type(error).__name__+": "+str(error)
        record["latency_ms"]=(time.perf_counter()-started)*1000
        return record
    with httpx.Client(timeout=30,limits=limits) as client:
        warmup=[request(-i-1) for i in range(10)]
        if not all(record["valid"] for record in warmup): raise RuntimeError("Warm-up response validation failed")
        started=time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as executor: records=list(executor.map(request,range(count)))
        wall=time.perf_counter()-started
    output.write_text("".join(json.dumps(record,allow_nan=False)+"\n" for record in records))
    output.with_suffix(".warmup.json").write_text(json.dumps(warmup,indent=2)+"\n")
    latencies=[record["latency_ms"] for record in records]
    return {"kind":"read-50-cases-with-history","concurrency":concurrency,"attempts":len(records),"valid_successes":sum(record["valid"] for record in records),
            "errors":sum(not record["valid"] for record in records),"wall_s":wall,"attempts_per_second":len(records)/wall,
            "valid_requests_per_second":sum(record["valid"] for record in records)/wall,
            "p50_ms":float(np.percentile(latencies,50)),"p95_ms":float(np.percentile(latencies,95)),"p99_ms":float(np.percentile(latencies,99)),
            "max_ms":max(latencies),"raw":output.name,"ten_warmups_excluded":True}


def write_phase(endpoint,token,analyst,count,out):
    concurrency=10
    if count<10 or count%10:raise ValueError("Write count must be a positive multiple of ten")
    query='mutation($id:ID!,$value:LabelValue!,$note:String!){labelCase(id:$id,value:$value,note:$note){id labels{actorId value note}}}'
    headers={"Authorization":"Bearer "+token};per_worker=count//concurrency
    with httpx.Client(timeout=30,limits=httpx.Limits(max_connections=concurrency,max_keepalive_connections=concurrency)) as client:
        cases=graphql(client,endpoint,'{alerts(source:"ato",limit:10){case{id events{action actorId note}}}}',token=token)["alerts"]
        if len(cases)!=10:raise RuntimeError("Ten independent synthetic cases are required for writes")
        setup=[]
        for row in cases:
            cid=row["case"]["id"]
            assigned=graphql(client,endpoint,'mutation($id:ID!,$u:ID!){assignCase(id:$id,assigneeId:$u){id assigneeId status events{action actorId note}}}',{"id":cid,"u":analyst},token)["assignCase"]
            if assigned["id"]!=cid or assigned["assigneeId"]!=analyst:raise RuntimeError("Write setup assignment failed")
            setup.append({"id":cid,"initial_history_count":len(assigned["events"])})
        (out/"write-setup.json").write_text(json.dumps(setup,indent=2)+"\n")
        def worker(index):
            cid=setup[index]["id"];records=[]
            for i in range(per_worker):
                value=("FRAUD","BENIGN","UNCERTAIN")[i%3];note=f"load-write:{index}:{i}"
                started=time.perf_counter();record={"request":index*per_worker+i,"worker":index,"case_id":cid,"actor_id":analyst,"label":value,"note":note,"http_status":None,"valid":False}
                try:
                    response=client.post(endpoint,json={"query":query,"variables":{"id":cid,"value":value,"note":note}},headers=headers)
                    record["http_status"]=response.status_code;response.raise_for_status();body=response.json()
                    if body.get("errors") or not body.get("data"):raise ValueError("GraphQL mutation failed")
                    changed=body["data"]["labelCase"]
                    own=[label for label in changed["labels"] if label["actorId"]==analyst]
                    if changed["id"]!=cid or len(own)!=1 or own[0]["value"]!=value or own[0]["note"]!=note:raise ValueError("Mutation body did not acknowledge this write")
                    record["valid"]=True
                except Exception as error:record["error"]=type(error).__name__+": "+str(error)
                record["latency_ms"]=(time.perf_counter()-started)*1000;records.append(record)
            return records
        started=time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as executor: groups=list(executor.map(worker,range(concurrency)))
        wall=time.perf_counter()-started;records=[record for group in groups for record in group]
        (out/"writes-c10.jsonl").write_text("".join(json.dumps(record,allow_nan=False)+"\n" for record in records))
        readback=[]
        for index,initial in enumerate(setup):
            case=graphql(client,endpoint,'query($id:ID!){case(id:$id){id assigneeId events{action actorId note} labels{actorId value note}}}',{"id":initial["id"]},token)["case"]
            expected={f"{('FRAUD','BENIGN','UNCERTAIN')[i%3]}: load-write:{index}:{i}" for i in range(per_worker)}
            actual={event["note"] for event in case["events"] if event["action"]=="LABELED" and event["actorId"]==analyst}
            own=[label for label in case["labels"] if label["actorId"]==analyst]
            valid=(len(case["events"])==initial["initial_history_count"]+per_worker and actual==expected and len(own)==1 and own[0]["note"]==f"load-write:{index}:{per_worker-1}" and case["assigneeId"]==analyst)
            readback.append({"case":case,"expected_new_events":per_worker,"verified":valid})
        (out/"write-readback.json").write_text(json.dumps(readback,indent=2)+"\n")
    latencies=[record["latency_ms"] for record in records];successes=sum(record["valid"] for record in records)
    return {"kind":"label-case-write","concurrency":concurrency,"independent_cases":10,"attempts":len(records),"valid_successes":successes,"errors":len(records)-successes,
            "wall_s":wall,"valid_requests_per_second":successes/wall,"p50_ms":float(np.percentile(latencies,50)),"p95_ms":float(np.percentile(latencies,95)),"p99_ms":float(np.percentile(latencies,99)),"max_ms":max(latencies),
            "raw":"writes-c10.jsonl","setup_assignments_excluded":10,"readback_verified":all(row["verified"] for row in readback),"new_history_events":sum(len(row["case"]["events"])-initial["initial_history_count"] for row,initial in zip(readback,setup))}


def run(args):
    address=urlsplit(args.endpoint)
    if address.hostname not in ("127.0.0.1","localhost") or address.port!=13040 or address.path!="/graphql":
        raise ValueError("Only the owned console localhost:13040/graphql is allowed")
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    with httpx.Client(timeout=30) as client:
        auth=graphql(client,args.endpoint,'mutation($e:String!){register(email:$e,password:"local-load-test-account"){token user{id}}}',{"e":"load-"+uuid.uuid4().hex+"@example.com"})["register"]
        token=auth["token"];analyst=auth["user"]["id"]
        data=graphql(client,args.endpoint,QUERY,token=token)
        expected=tuple(row["id"] for row in data["alerts"])
        if len(expected)!=50: raise RuntimeError("Need 50 real ingested cases before the load run")
        validate({"data":data},expected,analyst)
    report={"started_utc":datetime.now(timezone.utc).isoformat(),"endpoint":args.endpoint,"query":QUERY,
            "page_size":50,"page_ids_sha256":hashlib.sha256(json.dumps(expected).encode()).hexdigest(),
            "validation":"HTTP200, no GraphQL errors, exact expected page IDs, analyst, sources, probabilities, case histories and count invariants",
            "phases":[]}
    for concurrency,count in ((1,args.serial_requests),(20,args.concurrent_requests)):
        result=phase(args.endpoint,token,analyst,expected,concurrency,count,out/f"c{concurrency}.jsonl")
        report["phases"].append(result)
        (out/"summary.json").write_text(json.dumps(report,indent=2)+"\n")
        if result["errors"]: raise RuntimeError("Load contained failed or invalid responses; raw failures retained")
    writes=write_phase(args.endpoint,token,analyst,args.write_requests,out);report["phases"].append(writes)
    (out/"summary.json").write_text(json.dumps(report,indent=2)+"\n")
    if writes["errors"] or not writes["readback_verified"]:raise RuntimeError("Write body validation or persisted audit-history check failed")
    report["finished_utc"]=datetime.now(timezone.utc).isoformat()
    (out/"summary.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--endpoint",default="http://127.0.0.1:13040/graphql")
    parser.add_argument("--serial-requests",type=int,default=200);parser.add_argument("--concurrent-requests",type=int,default=1000)
    parser.add_argument("--write-requests",type=int,default=200)
    parser.add_argument("--output",default="results/load")
    run(parser.parse_args())
