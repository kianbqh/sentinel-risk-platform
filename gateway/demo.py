"""Real credit-card replay and four-producer integration check for the local Compose stack."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import time

import httpx
from kafka import KafkaConsumer, KafkaProducer, TopicPartition
import numpy as np
import pandas as pd

from .contracts import Alert
from .main import Models, Transaction


def prepare(path, source=None, limit=10000):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return pd.read_parquet(path)
    if source:
        raw = pd.read_parquet(source)
        origin = str(source)
    else:
        from sklearn.datasets import fetch_openml
        data = fetch_openml(data_id=1597, as_frame=True, data_home=str(path.parent / "openml-cache"))
        raw = data.frame
        origin = "OpenML dataset 1597 (creditcard)"
    if "txn_id" in raw:
        frame = raw.sort_values("txn_id").head(limit).copy()
    else:
        raw = raw.drop_duplicates().sort_values(["Time"] + [f"V{i}" for i in range(1,29)] + ["Amount"], kind="stable").head(limit)
        frame = pd.DataFrame({"txn_id": np.arange(len(raw), dtype=np.int64), "time": raw.Time.to_numpy(float),
                              "amount": raw.Amount.to_numpy(float), "label": raw.Class.astype(int).to_numpy(),
                              "v": list(raw[[f"V{i}" for i in range(1,29)]].to_numpy(float))})
    frame.to_parquet(path, index=False)
    provenance = {"source": origin, "rows": len(frame), "ordering": "first rows of deduplicated Time,V1..V28,Amount order",
                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "dataset": "OpenML 1597 / European credit-card transactions"}
    path.with_suffix(".json").write_text(json.dumps(provenance, indent=2) + "\n")
    return frame


def features(frame):
    times = frame.time.to_numpy(float); amounts = frame.amount.to_numpy(float)
    # Inclusive range window: all ties at a timestamp share the same window.
    starts = np.searchsorted(times, times - 3600, side="left")
    ends = np.searchsorted(times, times, side="right")
    result = []
    for i, row in enumerate(frame.itertuples(index=False)):
        window = amounts[starts[i]:ends[i]]
        mean = float(np.mean(window)); std = float(np.std(window))
        values = {f"V{j+1}": float(value) for j, value in enumerate(row.v)}
        values.update(Amount=float(row.amount), log_amount=math.log1p(row.amount), hour_of_day=math.floor((row.time % 86400)/3600),
                      txn_count_1h=len(window), amount_ratio_1h=row.amount/(mean+1), amount_z_1h=(row.amount-mean)/std if std>0 else 0.)
        result.append(values)
    return result


def graphql(client, endpoint, query, variables=None, token=None):
    headers = {"Authorization": "Bearer " + token} if token else {}
    response = client.post(endpoint, json={"query": query, "variables": variables or {}}, headers=headers)
    response.raise_for_status(); body = response.json()
    if body.get("errors") or not isinstance(body.get("data"), dict):
        raise RuntimeError(f"GraphQL operation failed: {body}")
    return body["data"]


def run(args):
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    frame = prepare(args.replay, args.source, args.limit)
    n = len(frame); rows = features(frame); models = Models()
    expected = models.score([Transaction(features=row, publish=False) for row in rows])
    bootstrap = os.environ["KAFKA_BOOTSTRAP_SERVERS"]
    topic = os.getenv("SCORES_TOPIC", "sentinel.scores.v1")
    reader = KafkaConsumer(bootstrap_servers=bootstrap.split(","), enable_auto_commit=False,
                           value_deserializer=lambda value: json.loads(value), consumer_timeout_ms=1000)
    partition = TopicPartition(topic, 0); reader.assign([partition]); reader.seek_to_beginning(partition)
    if reader.end_offsets([partition])[partition] != 0:
        raise RuntimeError("Scores topic already contains a replay. Use a fresh Compose project and volumes.")
    producer = KafkaProducer(bootstrap_servers=bootstrap.split(","), acks="all", retries=3,
                             value_serializer=lambda value: json.dumps(value, allow_nan=False).encode())
    started = time.perf_counter(); deliveries = []
    for row in frame.itertuples(index=False):
        deliveries.append(producer.send("sentinel.transactions.v1", value={"txn_id": int(row.txn_id), "time": float(row.time),
          "amount": float(row.amount), "label": int(row.label), "v": list(map(float,row.v)), "produce_ts_ms": int(time.time()*1000)}))
    deliveries.append(producer.send("sentinel.transactions.v1", value={"txn_id": -1, "time": float.fromhex('0x1.fffffffffffffp+1023'),
                           "amount": 0., "label": 0, "v": [0.]*28, "produce_ts_ms": int(time.time()*1000)}))
    for delivery in deliveries: delivery.get(timeout=60)
    producer.close(timeout=60)
    scores = []; deadline = time.monotonic()+300
    while len(scores)<n and time.monotonic()<deadline:
        for records in reader.poll(timeout_ms=1000, max_records=n).values(): scores.extend(record.value for record in records)
    reader.close()
    if len(scores)!=n or len({row["txnId"] for row in scores})!=n: raise RuntimeError(f"Lost/duplicate scores: {len(scores)}/{n}")
    by_id = {int(row.txn_id): i for i,row in enumerate(frame.itertuples(index=False))}
    delta = max(abs(row["proba"]-expected[by_id[row["txnId"]]]) for row in scores)
    if delta > 1e-10: raise RuntimeError(f"Python/Spark scoring mismatch: {delta}")
    expected_alerts = {"transaction-stream:"+str(row["txnId"]) for row in scores if row["isAlert"]}
    if not expected_alerts: raise RuntimeError("Replay produced no threshold alerts")
    replay_s = time.perf_counter()-started
    high = int(np.argmax(expected)); low = int(np.argmin(expected))
    with httpx.Client(timeout=30) as client:
        single = client.post(args.gateway+"/v1/transactions/score", json={"features": rows[high], "transaction_id": "integration-single"})
        single.raise_for_status(); single_body = single.json()
        if not single_body["flagged"] or not single_body["alert_published"]: raise RuntimeError("Single prediction not delivered")
        batch = client.post(args.gateway+"/v1/transactions/score-batch", json={"transactions":[
            {"features": rows[high], "transaction_id":"integration-batch-high"}, {"features":rows[low], "transaction_id":"integration-batch-low"}]})
        batch.raise_for_status(); batch_body=batch.json()
        if batch_body["n"]!=2 or batch_body["n_flagged"]!=1: raise RuntimeError("Batch prediction mismatch")
        text = client.post(args.gateway+"/v1/text/classify", json={"text":"Congratulations! You have won a free prize. Call 09061701461 now to claim your cash reward! Text WIN to 87121.", "message_id":"integration-message"})
        text.raise_for_status(); text_body=text.json()
        if text_body["label"]!="scam" or not text_body["alert_published"]: raise RuntimeError("Text signal was not delivered")
        # Actual synthetic detector training and publishing, not prebuilt envelopes.
        from ato.publish import run as publish_ato
        ato_path = out.parent / "ato-publish.json"
        publish_ato(limit=args.ato_limit, output=str(ato_path), prefix="integration-ato")
        ato = json.loads(ato_path.read_text())
        expected_ids = expected_alerts | {"transaction-api:integration-single","transaction-api:integration-batch-high","text-risk:integration-message"} | {event["alert_id"] for event in ato["events"]}
        email = "integration-"+str(int(time.time()))+"@example.com"
        auth = graphql(client,args.graphql, 'mutation($e:String!){register(email:$e,password:"local-integration-password"){token user{id}}}', {"e":email})["register"]
        token=auth["token"]; uid=auth["user"]["id"]
        found = {}; deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            page = graphql(client,args.graphql,'query($offset:Int!){alerts(limit:100,offset:$offset){id source riskScore threshold evidence case{id status events{action} labels{value}}}}',{"offset":0},token)["alerts"]
            found = {row["id"]:row for row in page}; offset=100
            while len(page)==100:
                page=graphql(client,args.graphql,'query($offset:Int!){alerts(limit:100,offset:$offset){id source riskScore threshold evidence case{id status events{action} labels{value}}}}',{"offset":offset},token)["alerts"]
                found.update({row["id"]:row for row in page}); offset+=100
            if expected_ids <= found.keys(): break
            time.sleep(.5)
        if set(found)!=expected_ids: raise RuntimeError(f"Ingestion mismatch: missing={expected_ids-found.keys()}, extra={found.keys()-expected_ids}")
        cid=found["transaction-api:integration-single"]["case"]["id"]
        graphql(client,args.graphql,'mutation($id:ID!,$u:ID!){assignCase(id:$id,assigneeId:$u){id status}}',{"id":cid,"u":uid},token)
        graphql(client,args.graphql,'mutation($id:ID!){labelCase(id:$id,value:FRAUD,note:"Local end-to-end review"){id}}',{"id":cid},token)
        graphql(client,args.graphql,'mutation($id:ID!){resolveCase(id:$id,note:"Reviewed"){status}}',{"id":cid},token)
        resolved=graphql(client,args.graphql,'query($u:ID!){alerts(status:RESOLVED,assigneeId:$u){id case{status events{action} labels{value}}}}',{"u":uid},token)["alerts"]
        if len(resolved)!=1 or resolved[0]["id"]!="transaction-api:integration-single" or resolved[0]["case"]["labels"][0]["value"]!="FRAUD": raise RuntimeError("Analyst flow failed")
        # Re-publish the same ID and confirm the database still exposes one case.
        duplicate=client.post(args.gateway+"/v1/transactions/score",json={"features":rows[high],"transaction_id":"integration-single"});duplicate.raise_for_status()
        time.sleep(1)
        duplicate_rows=graphql(client,args.graphql,'{alerts(search:"transaction-api:integration-single"){id case{id events{action}}}}',token=token)["alerts"]
        if len(duplicate_rows)!=1 or len(duplicate_rows[0]["case"]["events"])!=4: raise RuntimeError("Idempotent ingestion failed")
    alerts_reader=KafkaConsumer("sentinel.alerts.v1",bootstrap_servers=bootstrap.split(","),auto_offset_reset="earliest",enable_auto_commit=False,consumer_timeout_ms=1500)
    wire_events=[]
    for record in alerts_reader:
        event=Alert.model_validate_json(record.value);wire_events.append(event.model_dump(mode="json"))
    alerts_reader.close()
    if {e["alert_id"] for e in wire_events}!=expected_ids: raise RuntimeError("Wire contract IDs differ from console cases")
    (out.parent/"alerts-wire.jsonl").write_text("".join(json.dumps(e,allow_nan=False)+"\n" for e in wire_events))
    (out.parent/"scores.jsonl").write_text("".join(json.dumps(row,allow_nan=False)+"\n" for row in scores))
    report={"dataset":"OpenML 1597 credit-card replay prefix", "transactions":n,"input_records_including_flush":n+1,
            "scores":len(scores),"duplicate_scores":0,"max_probability_difference":float(delta),"stream_alerts":len(expected_alerts),
            "ato_synthetic_alerts":ato["published"],"unique_console_alerts":len(found),"wire_records_including_duplicate":len(wire_events),
            "sources":sorted({row["source"] for row in found.values()}),"single_prediction":single_body,"batch_prediction":batch_body,
            "text_prediction":text_body,"analyst_flow":["list","filter","assign","label","resolve"],"duplicate_ingestion":"one case, four history events",
            "replay_delivery_and_scoring_seconds":replay_s,"all_assertions_passed":True}
    out.write_text(json.dumps(report,indent=2)+"\n");print(json.dumps(report,indent=2))


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--replay",default="/data/replay.parquet");parser.add_argument("--source")
    parser.add_argument("--limit",type=int,default=10000);parser.add_argument("--ato-limit",type=int,default=100)
    parser.add_argument("--gateway",default=os.getenv("GATEWAY_URL","http://gateway:18040"))
    parser.add_argument("--graphql",default=os.getenv("GRAPHQL_URL","http://backend:18041/graphql"))
    parser.add_argument("--output",default="/results/integration/summary.json")
    run(parser.parse_args())
