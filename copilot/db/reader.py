"""SELECT-only adapter; its role cannot access analyst credentials or labels."""
from .vectors import vector_literal
from copilot.mcp_server.contracts import ToolRejected, positive_id


def json_value(value):
    from datetime import date, datetime
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


class PostgresCaseReader:
    def __init__(self, dsn, *, embedding_model, embedding_revision):
        self.dsn = dsn
        self.embedding_model, self.embedding_revision = embedding_model, embedding_revision

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self.dsn, row_factory=dict_row, options="-c default_transaction_read_only=on -c statement_timeout=10000")

    def get_case(self, case_id):
        positive_id(case_id)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM copilot.case_evidence WHERE case_id = %s", (case_id,)).fetchone()
        return None if row is None else {k: json_value(v) for k, v in row.items()}

    def list_case_events(self, case_id):
        positive_id(case_id)
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM copilot.case_history WHERE case_id = %s ORDER BY at, event_id", (case_id,)).fetchall()
        return [{k: json_value(v) for k, v in row.items()} for row in rows]

    def search_similar_cases(self, case_id, k):
        with self._connect() as conn:
            query = conn.execute("SELECT * FROM copilot.case_vectors_read WHERE case_id=%s AND embedding_model=%s AND embedding_revision=%s",
                (case_id, self.embedding_model, self.embedding_revision)).fetchone()
            if query is None:
                raise ToolRejected("embedding_unavailable", "Case has no matching pinned evidence embedding")
            rows = conn.execute("""SELECT v.case_id, e.alert_id, e.source, e.summary,
                1-(v.embedding <=> %s::vector) AS similarity
                FROM copilot.case_vectors_read v JOIN copilot.case_evidence e USING(case_id)
                WHERE v.case_id<>%s AND v.entity_key<>%s AND v.evidence_sha256<>%s
                AND v.embedding_model=%s AND v.embedding_revision=%s
                ORDER BY v.embedding <=> %s::vector, v.case_id LIMIT %s""",
                (str(query["embedding"]), case_id, query["entity_key"], query["evidence_sha256"],
                 self.embedding_model, self.embedding_revision, str(query["embedding"]), k)).fetchall()
        return {"case_id": case_id, "metric": "cosine", "embedding_model": self.embedding_model,
            "embedding_revision": self.embedding_revision, "neighbors": [dict(row) for row in rows]}
