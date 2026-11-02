"""Actual transport/persistence recordings and exact deterministic adapters."""
import copy

from copilot.agent.llm import canonical


class TracingTools:
    def __init__(self, client, frozen_tools=None):
        self.client, self.frozen_tools = client, frozen_tools or {}
        self.records = []
        self.violations = []

    async def call(self, name, args):
        from .protocol import call_key
        response = await self.client.call(name, args)
        self.records.append({"name": name, "arguments": copy.deepcopy(args), "response": copy.deepcopy(response)})
        wanted = self.frozen_tools.get(call_key(name, args))
        if wanted is not None and canonical(wanted) != canonical(response):
            self.violations.append({"name": name, "arguments": copy.deepcopy(args)})
            raise ValueError("Runtime MCP facts changed after protocol freeze")
        return response


class TracingModel:
    def __init__(self, model):
        self.model, self.turns = model, []

    async def complete(self, messages, schema):
        turn = await self.model.complete(messages, schema)
        self.turns.append(turn.record())
        return turn


class TracingStore:
    def __init__(self, store):
        self.store = store
        self.loads, self.saves = [], []

    def load_memory(self, case_id):
        value = self.store.load_memory(case_id)
        self.loads.append({"case_id": case_id, "memory": copy.deepcopy(value)})
        return value

    def save_turn(self, case_id, **kwargs):
        value = self.store.save_turn(case_id, **kwargs)
        self.saves.append({"case_id": case_id, **copy.deepcopy(kwargs), "receipt": copy.deepcopy(value)})
        return value


class RecordedTools:
    def __init__(self, records):
        self.records, self.position = list(records), 0

    async def call(self, name, args):
        if self.position >= len(self.records): raise AssertionError("Recorded MCP responses exhausted")
        record = self.records[self.position]
        self.position += 1
        if record["name"] != name or canonical(record["arguments"]) != canonical(args):
            raise AssertionError("Recorded MCP invocation mismatch")
        return copy.deepcopy(record["response"])


class RecordedStore:
    def __init__(self, loads, saves):
        self.loads, self.saves = list(loads), list(saves)
        self.load_position, self.save_position = 0, 0

    def load_memory(self, case_id):
        row = self.loads[self.load_position]; self.load_position += 1
        if row["case_id"] != case_id: raise AssertionError("Recorded memory case mismatch")
        return copy.deepcopy(row["memory"])

    def save_turn(self, case_id, **kwargs):
        row = self.saves[self.save_position]; self.save_position += 1
        if row["case_id"] != case_id or any(canonical(row[key]) != canonical(value) for key,value in kwargs.items()):
            raise AssertionError("Recorded controlled draft persistence mismatch")
        return copy.deepcopy(row["receipt"])
