"""Orchestration-only writes via restricted database functions."""
import json
import re
from typing import Protocol

from copilot.mcp_server.contracts import positive_id

# Agent redaction owns the user-facing replacement policy. This is a final reject
# boundary, applied to memory and drafts; raw prompts must never reach storage.
PII = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<![\w.])(?:\d[ -]?){13,19}(?![\w.])|(?<!\w)(?:\d{1,3}\.){3}\d{1,3}(?!\w)|(?<![\w.])(?:\+?\d[ ()-]*){10,12}(?![\w.])")


class DraftStore(Protocol):
    def load_memory(self, case_id: int) -> list[dict]: ...
    def save_turn(self, case_id: int, *, run_id: str, redacted_memory: list[dict], draft: dict) -> dict: ...


def validate_turn(case_id, run_id, redacted_memory, draft):
    positive_id(case_id)
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", run_id):
        raise ValueError("Invalid case-scoped run_id")
    if not isinstance(redacted_memory, list) or any(not isinstance(v, dict) for v in redacted_memory) or not isinstance(draft, dict):
        raise ValueError("Memory must be a list of objects and draft an object")
    encoded = json.dumps({"memory": redacted_memory, "draft": draft}, allow_nan=False)
    if len(encoded.encode()) > 131072 or PII.search(encoded):
        raise ValueError("Unredacted or oversized turn rejected before persistence")


class PostgresDraftStore:
    def __init__(self, dsn):
        self.dsn = dsn

    def load_memory(self, case_id):
        import psycopg
        positive_id(case_id)
        with psycopg.connect(self.dsn) as conn:
            return conn.execute("SELECT copilot.load_memory(%s)", (case_id,)).fetchone()[0]

    def save_turn(self, case_id, *, run_id, redacted_memory, draft):
        import psycopg
        from psycopg.types.json import Jsonb
        validate_turn(case_id, run_id, redacted_memory, draft)
        with psycopg.connect(self.dsn) as conn:
            return conn.execute("SELECT copilot.save_turn(%s,%s,%s,%s)",
                (case_id, run_id, Jsonb(redacted_memory), Jsonb(draft))).fetchone()[0]
