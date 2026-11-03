"""Actual transport/persistence recordings and exact deterministic adapters."""
import copy
import re
import time

from copilot.agent.llm import canonical, request_hash
from copilot.agent.redaction import redact


class TracingTools:
    def __init__(self, client, frozen_tools=None):
        self.client, self.frozen_tools = client, frozen_tools or {}
        self.records = []
        self.violations = []

    async def call(self, name, args):
        from .protocol import call_key
        started=time.perf_counter()
        request={"name":name,"arguments":redact(copy.deepcopy(args))}
        try:
            response = await self.client.call(name, args)
        except Exception as exc:
            self.records.append({**request,"kind":"transport_error","elapsed_seconds":time.perf_counter()-started,
                "actual_error":{"type":type(exc).__name__,"module":type(exc).__module__},
                "boundary":"ToolClient.call raised before returning a usable MCP envelope"})
            raise
        self.records.append({**request,"kind":"response","elapsed_seconds":time.perf_counter()-started,"response":copy.deepcopy(response)})
        wanted = self.frozen_tools.get(call_key(name, args))
        if wanted is not None and canonical(wanted) != canonical(response):
            self.violations.append({"name": name, "arguments": copy.deepcopy(args)})
            raise ValueError("Runtime MCP facts changed after protocol freeze")
        return response


class TracingModel:
    def __init__(self, model):
        self.model, self.turns = model, []
        self.events = []

    async def complete(self, messages, schema):
        clean = redact(copy.deepcopy(messages))
        schema_copy = copy.deepcopy(schema)
        request = {"request_sha256": request_hash(clean, schema_copy), "messages": clean, "schema": schema_copy}
        started = time.perf_counter()
        try:
            turn = await self.model.complete(clean, schema_copy)
        except Exception as exc:
            self.events.append({**request, "kind": "transport_error", "elapsed_seconds": time.perf_counter()-started,
                "actual_error": {"type": type(exc).__name__, "module": type(exc).__module__},
                "boundary": "model.complete raised before returning a usable ModelTurn; response availability is unknown"})
            raise
        self.turns.append(turn.record())
        self.events.append({**request, "kind": "response", "elapsed_seconds": time.perf_counter()-started, "turn": turn.record()})
        return turn


class TracingStore:
    def __init__(self, store):
        self.store = store
        self.loads, self.saves = [], []
        self.errors = []

    def load_memory(self, case_id):
        started=time.perf_counter()
        try:value = self.store.load_memory(case_id)
        except Exception as exc:
            self.errors.append({"operation":"load_memory","case_id":case_id,"actual_error":{"type":type(exc).__name__,"module":type(exc).__module__},"elapsed_seconds":time.perf_counter()-started})
            raise
        self.loads.append({"case_id": case_id, "memory": copy.deepcopy(value)})
        return value

    def save_turn(self, case_id, **kwargs):
        started=time.perf_counter()
        try:value = self.store.save_turn(case_id, **kwargs)
        except Exception as exc:
            self.errors.append({"operation":"save_turn","case_id":case_id,"actual_error":{"type":type(exc).__name__,"module":type(exc).__module__},"elapsed_seconds":time.perf_counter()-started})
            raise
        self.saves.append({"case_id": case_id, **copy.deepcopy(kwargs), "receipt": copy.deepcopy(value)})
        return value


class RecordedTools:
    def __init__(self, records):
        self.records, self.position = list(records), 0
        self.violations = []

    async def call(self, name, args):
        if self.position >= len(self.records):
            self.violations.append("recorded_tool_responses_exhausted")
            raise AssertionError("Recorded MCP responses exhausted")
        record = self.records[self.position]
        if record["name"] != name or canonical(record["arguments"]) != canonical(args):
            self.violations.append("recorded_tool_invocation_mismatch")
            raise AssertionError("Recorded MCP invocation mismatch")
        self.position += 1
        if record.get("kind","response")=="transport_error":
            name=record["actual_error"]["type"]
            if not isinstance(name,str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,99}",name):
                self.violations.append("invalid_recorded_tool_error_type")
                raise AssertionError("Invalid recorded tool exception type")
            error_type=type(name,(RuntimeError,),{"recorded_tool_error":True})
            raise error_type("Replay of recorded actual MCP adapter exception")
        if record.get("kind","response")!="response":
            self.violations.append("unknown_recorded_tool_event")
            raise AssertionError("Unknown recorded MCP event")
        return copy.deepcopy(record["response"])


class RecordedStore:
    def __init__(self, loads, saves):
        self.loads, self.saves = list(loads), list(saves)
        self.load_position, self.save_position = 0, 0
        self.violations = []

    def load_memory(self, case_id):
        if self.load_position>=len(self.loads):
            self.violations.append("recorded_memory_loads_exhausted")
            raise AssertionError("Recorded memory loads exhausted")
        row = self.loads[self.load_position]
        if row["case_id"] != case_id:
            self.violations.append("recorded_memory_case_mismatch")
            raise AssertionError("Recorded memory case mismatch")
        self.load_position += 1
        return copy.deepcopy(row["memory"])

    def save_turn(self, case_id, **kwargs):
        if self.save_position>=len(self.saves):
            self.violations.append("recorded_memory_saves_exhausted")
            raise AssertionError("Recorded memory saves exhausted")
        row = self.saves[self.save_position]
        expected_keys={"run_id","redacted_memory","draft"}
        if row["case_id"] != case_id or set(kwargs)!=expected_keys or any(canonical(row[key]) != canonical(value) for key,value in kwargs.items()):
            self.violations.append("recorded_persistence_mismatch")
            raise AssertionError("Recorded controlled draft persistence mismatch")
        self.save_position += 1
        return copy.deepcopy(row["receipt"])
