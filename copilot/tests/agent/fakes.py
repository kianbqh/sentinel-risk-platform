"""Explicitly synthetic adversarial fixtures, never a model-quality benchmark."""
import copy
import json

from copilot.agent.llm import ModelTurn, request_hash
from copilot.agent.schemas import fallback
from copilot.db.persistence import validate_turn


def case(case_id=7):
    return {"case_id": case_id, "source": "transaction-api", "model_name": "logreg_balanced", "risk_score": .2,
            "summary": "Synthetic test transaction amount 17.25.", "evidence": {"features": {"Amount": 17.25},
                "note": "email a@example.invalid; card 4111 1111 1111 1111; phone +1 (202) 555-0146; IP 192.0.2.44"}}


def draft(case_id=7, **kwargs):
    value = fallback(case_id, "insufficient_evidence")
    value.update(summary="The transaction amount is 17.25.", key_evidence=[{"source": "get_case", "fact": "Amount 17.25."}],
                 risk_level="medium", confidence=.8, abstain=False, recommended_next_step="review_evidence", handoff_reason="none")
    value.update(kwargs)
    return value


class FakeTools:
    def __init__(self, value=None): self.value, self.calls = value or case(), []
    async def call(self, name, args):
        self.calls.append((name, copy.deepcopy(args)))
        if name == "get_case": return {"ok": True, "data": copy.deepcopy(self.value)}
        if name == "list_case_events": return {"ok": True, "data": {"case_id": args["case_id"], "events": []}}
        if name == "search_similar_cases": return {"ok": True, "data": {"case_id": args["case_id"], "neighbors": []}}
        return {"ok": False, "error": {"code": "unsupported_source", "message": "Synthetic unavailable tool"}}


class FakeStore:
    def __init__(self): self.memory, self.saves = {}, []
    def load_memory(self, case_id): return copy.deepcopy(self.memory.get(case_id, []))
    def save_turn(self, case_id, **kwargs):
        validate_turn(case_id, kwargs["run_id"], kwargs["redacted_memory"], kwargs["draft"])
        self.memory[case_id] = copy.deepcopy(kwargs["redacted_memory"])
        self.saves.append({"case_id": case_id, **copy.deepcopy(kwargs)})
        return {"case_id": case_id, "run_id": kwargs["run_id"], "event_id": len(self.saves), "actor": "copilot"}


class ScriptedModel:
    def __init__(self, responses): self.responses, self.messages = list(responses), []
    async def complete(self, messages, schema):
        self.messages.append(copy.deepcopy(messages))
        value = self.responses.pop(0)
        if isinstance(value, Exception): raise value
        content = value if isinstance(value, str) else json.dumps(value)
        return ModelTurn(content, {"synthetic_test_fixture": True, "choices": [{"message": {"content": content}}]},
                         {"prompt_tokens": 1, "completion_tokens": 1}, 0.0, request_hash(messages, schema), messages)
