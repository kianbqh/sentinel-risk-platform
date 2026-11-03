"""Index complete raw live case evidence using an explicitly separate indexer role."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from copilot.db.vectors import vector_literal
from copilot.scripts.provenance import RunRecord
from .embedding import LocalEmbedder, MODEL, REVISION
from .evidence import evidence_text


def index(config_path):
    run=RunRecord(config_path)
    try:
        import psycopg
        from psycopg.rows import dict_row
        config=run.config
        encoder=LocalEmbedder(config["embedding_snapshot"],json.loads(Path(config["embedding_reference"]).read_text()),config["device"],config["seed"])
        accepted,skipped=[],[]
        with psycopg.connect(os.environ[config["indexer_dsn_env"]],row_factory=dict_row) as conn:
            rows=conn.execute("SELECT case_id,source,entity_type,entity_id,evidence FROM copilot.case_evidence ORDER BY case_id").fetchall()
            for row in rows:
                domain="card" if row["source"] in {"transaction-api","transaction-stream"} else "ato" if row["source"]=="ato" else None
                try:
                    text=evidence_text(domain,row["evidence"].get("features",{}))
                except (ValueError,AttributeError):
                    skipped.append({"case_id":row["case_id"],"reason":"unsupported_or_incomplete_raw_features"})
                    continue
                accepted.append((row,text))
            if accepted:
                vectors=encoder.encode([text for _,text in accepted],config["embedding_batch_size"])
                for (row,text),vector in zip(accepted,vectors):
                    entity=hashlib.sha256((row["entity_type"]+":"+row["entity_id"]).encode()).hexdigest()
                    conn.execute("""INSERT INTO copilot.case_vectors(case_id,entity_key,evidence_sha256,embedding_model,embedding_revision,embedding)
                        VALUES(%s,%s,%s,%s,%s,%s::vector) ON CONFLICT(case_id) DO NOTHING""",
                        (row["case_id"],entity,hashlib.sha256(text.encode()).hexdigest(),MODEL,REVISION,vector_literal(vector.tolist())))
        run.write("skipped.json",skipped)
        run.write("embedding_files.json",encoder.hashes)
        run.finish({"status":"complete","eligible_cases":len(accepted),"skipped_cases":len(skipped),
            "embedding_model":MODEL,"embedding_revision":REVISION,"existing_vectors":"preserved; not overwritten"})
    except Exception as exc:
        run.fail(type(exc).__name__)
        raise


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--config",required=True)
    index(parser.parse_args().config)
