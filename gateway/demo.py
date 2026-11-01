"""Real credit-card replay and four-producer integration check for the local Compose stack."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import time

import httpx
from kafka import KafkaConsumer, KafkaProducer, TopicPartition
import numpy as np
import pandas as pd

from .contracts import Alert
from .client import graphql
from .main import Models, Transaction
from .verification import completed_progress, probability, verify_alert_values, verify_stream


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def prepare(path, source=None, limit=10000, download_full=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if source is None and not download_full:
        manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text())
        fixture = FIXTURE_DIR / manifest["file"]
        if not 1 <= limit <= manifest["rows"]:
            raise ValueError("The bundled replay contains 10000 rows; use --source or --download-full for a larger replay")
        if hashlib.sha256(fixture.read_bytes()).hexdigest() != manifest["sha256"]:
            raise RuntimeError("Bundled replay SHA-256 mismatch")
        if not path.exists():
            shutil.copyfile(fixture, path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["sha256"]:
            raise RuntimeError("Cached replay SHA-256 mismatch")
        frame = pd.read_parquet(path)
        if len(frame) != manifest["rows"]:
            raise RuntimeError("Bundled replay row count mismatch")
        path.with_suffix(".json").write_text(json.dumps({"dataset": manifest["dataset"], "source": "versioned real OpenML prefix",
            "sha256": manifest["sha256"], "rows_in_file": len(frame), "selected_rows": limit,
            "full_replay_sha256": manifest["full_preprocessed_replay"]["sha256"]}, indent=2)+"\n")
        return frame.head(limit).copy()
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


def run(args):
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    frame = prepare(args.replay, args.source, args.limit, args.download_full)
    n = len(frame); rows = features(frame); models = Models()
    expected = models.score([Transaction(features=row, publish=False) for row in rows])
    bootstrap = os.environ["KAFKA_BOOTSTRAP_SERVERS"]
    topic = os.getenv("SCORES_TOPIC", "sentinel.scores.v1")
    reader = KafkaConsumer(bootstrap_servers=bootstrap.split(","), enable_auto_commit=False,
                           value_deserializer=lambda value: json.loads(value), consumer_timeout_ms=1000)
    partition = TopicPartition(topic, 0); reader.assign([partition]); reader.seek_to_beginning(partition)
    if reader.end_offsets([partition])[partition] != 0:
        raise RuntimeError("Scores topic already contains a replay. Use a fresh Compose project and volumes.")
    input_topic = "sentinel.transactions.v1"
    input_partition = TopicPartition(input_topic, 0)
    if reader.end_offsets([input_partition])[input_partition] != 0:
        raise RuntimeError("Transactions topic is not empty before the bounded replay")
    producer = KafkaProducer(bootstrap_servers=bootstrap.split(","), acks="all", retries=3,
                             value_serializer=lambda value: json.dumps(value, allow_nan=False).encode())
    started = time.perf_counter(); deliveries = []
    for row in frame.itertuples(index=False):
        deliveries.append(producer.send("sentinel.transactions.v1", value={"txn_id": int(row.txn_id), "time": float(row.time),
          "amount": float(row.amount), "label": int(row.label), "v": list(map(float,row.v)), "produce_ts_ms": int(time.time()*1000)}))
    deliveries.append(producer.send("sentinel.transactions.v1", value={"txn_id": -1, "time": float.fromhex('0x1.fffffffffffffp+1023'),
                           "amount": 0., "label": 0, "v": [0.]*28, "produce_ts_ms": int(time.time()*1000)}))
    delivered = [delivery.get(timeout=60) for delivery in deliveries]
    terminal_offset = delivered[-1].offset + 1
    if terminal_offset != n + 1:
        raise RuntimeError("Unexpected terminal input offset")
    producer.close(timeout=60)
    progress_path = out.parent.parent / "streaming/progress.jsonl"
    terminal = None; deadline = time.monotonic() + 300
    while terminal is None and time.monotonic() < deadline:
        if progress_path.exists():
            lines = progress_path.read_text().splitlines(keepends=True)
            progress = [json.loads(line) for line in lines if line.endswith("\n")]
            terminal = completed_progress(progress, input_topic, terminal_offset, n + 1)
        if terminal is None:
            time.sleep(.5)
    if terminal is None:
        raise RuntimeError("No completed Spark batch covering the terminal flush marker")
    high_water = reader.end_offsets([partition])[partition]
    scores = []; score_offsets = []; deadline = time.monotonic() + 90
    while reader.position(partition) < high_water and time.monotonic() < deadline:
        for records in reader.poll(timeout_ms=1000, max_records=5000).values():
            for record in records:
                if record.offset < high_water:
                    scores.append(record.value); score_offsets.append(record.offset)
    if reader.position(partition) < high_water:
        raise RuntimeError("Could not drain the completed score-topic high water")
    reader.close()
    actual_ids = {row["txnId"] for row in scores}
    input_ids = {int(identity) for identity in frame.txn_id}
    score_counts = {"total": len(scores), "unique": len(actual_ids), "duplicates": len(scores)-len(actual_ids),
                    "unexpected_ids": sorted(actual_ids-input_ids), "missing_ids": sorted(input_ids-actual_ids)}
    terminal.update(score_topic=topic, score_high_water=high_water, drained_offsets=score_offsets, score_counts=score_counts)
    (out.parent/"terminal-progress.json").write_text(json.dumps(terminal,indent=2)+"\n")
    (out.parent/"scores.jsonl").write_text("".join(json.dumps(row,allow_nan=False)+"\n" for row in scores))
    reference = {int(row.txn_id): float(expected[i]) for i, row in enumerate(frame.itertuples(index=False))}
    threshold = float(models.metadata["threshold"])
    delta, expected_alerts = verify_stream(scores, reference, threshold)
    if not expected_alerts: raise RuntimeError("Replay produced no threshold alerts")
    replay_s = time.perf_counter()-started
    high = int(np.argmax(expected)); low = int(np.argmin(expected))
    with httpx.Client(timeout=30) as client:
        single = client.post(args.gateway+"/v1/transactions/score", json={"features": rows[high], "transaction_id": "integration-single"})
        single.raise_for_status(); single_body = single.json()
        if single_body["transaction_id"] != "integration-single" or not single_body["flagged"] or not single_body["alert_published"]: raise RuntimeError("Single prediction not delivered")
        if abs(probability(single_body["fraud_score"], "API score") - expected[high]) > 1e-10 or single_body["threshold"] != threshold:
            raise RuntimeError("Single API score/threshold differs from Python")
        batch = client.post(args.gateway+"/v1/transactions/score-batch", json={"transactions":[
            {"features": rows[high], "transaction_id":"integration-batch-high"}, {"features":rows[low], "transaction_id":"integration-batch-low"}]})
        batch.raise_for_status(); batch_body=batch.json()
        if batch_body["n"]!=2 or batch_body["n_flagged"]!=1 or len(batch_body["results"]) != 2 or [row["transaction_id"] for row in batch_body["results"]] != ["integration-batch-high", "integration-batch-low"]:
            raise RuntimeError("Batch prediction count/identity mismatch")
        for prediction, index in zip(batch_body["results"], (high, low)):
            if abs(probability(prediction["fraud_score"], "batch API score") - expected[index]) > 1e-10 or prediction["threshold"] != threshold or prediction["flagged"] != bool(expected[index] >= threshold):
                raise RuntimeError("Batch API score/threshold/flag differs from Python")
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
        # OffsetFetch is read-only: do not join or commit to the backend's group.
        audit=KafkaConsumer(bootstrap_servers=bootstrap.split(","),group_id=os.getenv("ALERTS_GROUP","sentinel-console-v1"),enable_auto_commit=False)
        alerts_partition=TopicPartition("sentinel.alerts.v1",0)
        alerts_high_water=audit.end_offsets([alerts_partition])[alerts_partition]
        committed=None;deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            committed=audit.committed(alerts_partition)
            if committed is not None and committed>=alerts_high_water:break
            time.sleep(.5)
        audit.close()
        if committed is None or committed<alerts_high_water:
            raise RuntimeError("Console consumer has not committed the deliberately duplicated alert")
        duplicate_rows=graphql(client,args.graphql,'{alerts(search:"transaction-api:integration-single"){id case{id events{action}}}}',token=token)["alerts"]
        if len(duplicate_rows)!=1 or len(duplicate_rows[0]["case"]["events"])!=4: raise RuntimeError("Idempotent ingestion failed")
    alerts_reader=KafkaConsumer(bootstrap_servers=bootstrap.split(","),enable_auto_commit=False)
    alerts_reader.assign([alerts_partition]);alerts_reader.seek_to_beginning(alerts_partition)
    wire_events=[];deadline=time.monotonic()+90
    while alerts_reader.position(alerts_partition)<alerts_high_water and time.monotonic()<deadline:
        for records in alerts_reader.poll(timeout_ms=1000,max_records=5000).values():
            for record in records:
                if record.offset<alerts_high_water:
                    event=Alert.model_validate_json(record.value);wire_events.append(event.model_dump(mode="json"))
    if alerts_reader.position(alerts_partition)<alerts_high_water:
        raise RuntimeError("Could not drain the completed alert-topic high water")
    alerts_reader.close()
    if {e["alert_id"] for e in wire_events}!=expected_ids: raise RuntimeError("Wire contract IDs differ from console cases")
    producer_values = {
        f"transaction-stream:{identity}": {"source": "transaction-stream", "risk_score": score, "threshold": threshold}
        for identity, score in reference.items() if score >= threshold
    }
    for identity in ("integration-single", "integration-batch-high"):
        producer_values["transaction-api:" + identity] = {"source": "transaction-api", "risk_score": float(expected[high]), "threshold": threshold}
    producer_values["text-risk:integration-message"] = {"source": "text-risk", "risk_score": text_body["risk_score"], "threshold": .5 if text_body["source"] == "tfidf" else None}
    producer_values.update({event["alert_id"]: {key: event[key] for key in ("source", "risk_score", "threshold")} for event in ato["events"]})
    values_checked = verify_alert_values(wire_events, found, producer_values)
    (out.parent/"alerts-wire.jsonl").write_text("".join(json.dumps(e,allow_nan=False)+"\n" for e in wire_events))
    (out.parent/"scores.jsonl").write_text("".join(json.dumps(row,allow_nan=False)+"\n" for row in scores))
    (out.parent/"reference-predictions.jsonl").write_text("".join(json.dumps({"txn_id": identity, "probability": score, "threshold": threshold, "is_alert": score >= threshold}, allow_nan=False)+"\n" for identity, score in reference.items()))
    report={"dataset":"OpenML 1597 credit-card replay prefix", "transactions":n,"input_records_including_flush":n+1,
            "scores":len(scores),"unique_scores":score_counts["unique"],"duplicate_scores":score_counts["duplicates"],
            "score_topic_high_water":high_water,"completed_input_rows":terminal["cumulative_input_rows"],
            "unexpected_score_ids":score_counts["unexpected_ids"],"max_probability_difference":float(delta),"stream_alerts":len(expected_alerts),
            "ato_synthetic_alerts":ato["published"],"unique_console_alerts":len(found),"wire_records_including_duplicate":len(wire_events),
            "sources":sorted({row["source"] for row in found.values()}),"single_prediction":single_body,"batch_prediction":batch_body,
            "text_prediction":text_body,"analyst_flow":["list","filter","assign","label","resolve"],
            "duplicate_ingestion":"duplicate offset committed; one case, four history events",
            "alerts_high_water":alerts_high_water,"console_committed_offset":committed,
            "replay_delivery_and_scoring_seconds":replay_s,"independent_threshold_flags_verified":n,
            "wire_and_console_values_checked":values_checked,"all_assertions_passed":True}
    out.write_text(json.dumps(report,indent=2)+"\n");print(json.dumps(report,indent=2))


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--replay",default="/data/replay.parquet");parser.add_argument("--source")
    parser.add_argument("--download-full",action="store_true",help="Explicitly fetch the complete OpenML dataset with its upstream checksum checks")
    parser.add_argument("--limit",type=int,default=10000);parser.add_argument("--ato-limit",type=int,default=100)
    parser.add_argument("--gateway",default=os.getenv("GATEWAY_URL","http://gateway:18040"))
    parser.add_argument("--graphql",default=os.getenv("GRAPHQL_URL","http://backend:18041/graphql"))
    parser.add_argument("--output",default="/results/integration/summary.json")
    run(parser.parse_args())
