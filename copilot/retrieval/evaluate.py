"""Measure actual PostgreSQL cosine retrieval against fixed random and TF-IDF."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import statistics
import time

from copilot.db.vectors import vector_literal
from copilot.scripts.provenance import RunRecord, sha256
from .embedding import LocalEmbedder, MODEL, REVISION
from .evidence import validate_protocol


def eligible(query, gallery):
    return [row for row in gallery if row["document_id"] != query["document_id"]
        and row["entity_key"] != query["entity_key"] and row["evidence_sha256"] != query["evidence_sha256"]]


def precision(query, neighbors, k=5):
    if len(neighbors) != k:
        raise ValueError("Retrieval must return exactly k eligible neighbors")
    return sum(row["gold_label"] == query["gold_label"] for row in neighbors)/k


def evaluate(config_path):
    run = RunRecord(config_path)
    try:
        import numpy as np
        import psycopg
        from psycopg.rows import dict_row
        from sklearn.feature_extraction.text import TfidfVectorizer
        config = run.config
        protocol_path = Path(config["protocol"])
        if sha256(protocol_path) != config["protocol_sha256"]:
            raise ValueError("Fixed protocol SHA-256 mismatch")
        protocol = json.loads(protocol_path.read_text())
        rows = protocol["records"]
        counts = validate_protocol(rows)
        queries = [row for row in rows if row["split"] == "query"]
        gallery = [row for row in rows if row["split"] == "gallery"]
        reference = json.loads(Path(config["embedding_reference"]).read_text())
        encoder = LocalEmbedder(config["embedding_snapshot"], reference, config["device"], config["seed"])
        vectors = encoder.encode([row["evidence_text"] for row in rows], config["embedding_batch_size"])
        tfidf = TfidfVectorizer(ngram_range=(1,2), min_df=1)
        gallery_tfidf = tfidf.fit_transform([row["evidence_text"] for row in gallery])
        query_tfidf = tfidf.transform([row["evidence_text"] for row in queries])
        # A new dataset namespace prevents overwriting any previous experiment.
        dataset = config["protocol_sha256"] + ":" + hashlib.sha256(str(run.output).encode()).hexdigest()[:16]
        indexed = {row["document_id"]: row for row in rows}
        vector_by_id = {row["document_id"]: vector_literal(vectors[i].tolist()) for i,row in enumerate(rows)}
        samples = []
        with psycopg.connect(os.environ[config["indexer_dsn_env"]], row_factory=dict_row) as conn:
            for row in rows:
                conn.execute("""INSERT INTO copilot.retrieval_documents(dataset_id,document_id,split,entity_key,evidence_sha256,
                  domain,gold_label,evidence_text,embedding_model,embedding_revision,embedding)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)""",
                  (dataset,row["document_id"],row["split"],row["entity_key"],row["evidence_sha256"],row["domain"],
                   row["gold_label"],row["evidence_text"],MODEL,REVISION,vector_by_id[row["document_id"]]))
            conn.commit()
            for qi,query in enumerate(queries):
                started = time.perf_counter()
                matches = conn.execute("""SELECT document_id,1-(embedding <=> %s::vector) AS similarity
                  FROM copilot.retrieval_documents WHERE dataset_id=%s AND split='gallery'
                  AND document_id<>%s AND entity_key<>%s AND evidence_sha256<>%s
                  ORDER BY embedding <=> %s::vector,document_id LIMIT 5""",
                  (vector_by_id[query["document_id"]],dataset,query["document_id"],query["entity_key"],
                   query["evidence_sha256"],vector_by_id[query["document_id"]])).fetchall()
                latency = time.perf_counter()-started
                selected = [indexed[m["document_id"]] for m in matches]
                allowed = eligible(query,gallery)
                rng = random.Random(config["random_seed"] + qi)
                random_neighbors = rng.sample(sorted(allowed,key=lambda v:v["document_id"]),5)
                scores = (gallery_tfidf @ query_tfidf[qi].T).toarray().ravel()
                score_by_id = {row["document_id"]: float(scores[i]) for i,row in enumerate(gallery)}
                tfidf_neighbors = sorted(allowed,key=lambda row:(-score_by_id[row["document_id"]],row["document_id"]))[:5]
                for method,neighbors in (("pgvector_bge_cosine",selected),("random",random_neighbors),("tfidf_cosine",tfidf_neighbors)):
                    samples.append({"query_id":query["document_id"],"domain":query["domain"],"gold_label":query["gold_label"],
                        "method":method,"precision_at_5":precision(query,neighbors),
                        "neighbors":[row["document_id"] for row in neighbors],
                        "pgvector_query_wall_seconds":latency if method=="pgvector_bge_cosine" else None})
            server = conn.execute("SELECT version() AS server").fetchone()["server"]
            extension = conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()["extversion"]
        run.write("per_query.json",samples)
        run.write("embedding_files.json",encoder.hashes)
        aggregates = {}
        for method in ("pgvector_bge_cosine","random","tfidf_cosine"):
            aggregates[method] = {domain: statistics.mean(s["precision_at_5"] for s in samples if s["method"]==method
                and (domain=="all" or s["domain"]==domain)) for domain in ("all","card","ato")}
        import torch
        run.context["cuda"] = torch.version.cuda
        run.finish({"status":"complete","precision_at_5":aggregates,"counts":counts,"protocol_sha256":sha256(protocol_path),
            "embedding_model":MODEL,"embedding_revision":REVISION,"embedding_device":config["device"],
            "database_server":server,"pgvector_version":extension,"dataset_namespace":dataset,
            "protocol":"fixed balanced query/gallery, no shared entity or exact evidence; gold omitted from embeddings; TF-IDF fit on gallery only",
            "timer":"per-query pgvector wall time includes database round trip and exact cosine SELECT; excludes encoding/index insert/TF-IDF; no throughput claim"})
    except Exception as exc:
        run.fail(type(exc).__name__)
        raise


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",required=True)
    evaluate(parser.parse_args().config)
