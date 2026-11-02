"""These tests execute actual LangGraph with explicitly synthetic model stubs."""
import importlib.util
import json
import unittest

from copilot.agent.workflow import AnalystCopilot, AgentConfig, EvaluationProtocolError
from copilot.agent.redaction import findings
from copilot.tests.agent.fakes import FakeTools, FakeStore, ScriptedModel, draft


@unittest.skipUnless(importlib.util.find_spec("langgraph"), "Real LangGraph runtime not installed in this interpreter")
class GraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_graph_executes_model_selected_tool_and_saves_draft(self):
        model = ScriptedModel([{"action": "tool", "tool_name": "list_case_events", "arguments": {"case_id": 7}}, {"action": "draft", "draft": draft()}])
        tools, store = FakeTools(), FakeStore()
        result = await AnalystCopilot(tools, model, store).run(7, run_id="graph-valid")
        self.assertEqual([call[0] for call in tools.calls], ["get_case", "list_case_events"])
        self.assertFalse(result["draft"]["abstain"])
        self.assertEqual(result["receipt"]["actor"], "copilot")
        self.assertFalse(findings(json.dumps(model.messages)))

    async def test_schema_retry_retains_raw_failure_and_fallback_is_explicit(self):
        model = ScriptedModel(['not-json', '{"action":"draft","draft":{"status":"RESOLVED"}}'])
        result = await AnalystCopilot(FakeTools(), model, FakeStore()).run(7, run_id="graph-malformed")
        self.assertEqual(len(result["attempts"]), 2)
        self.assertEqual(result["attempts"][0]["content"], "not-json")
        self.assertEqual(result["schema_failures"], 2)
        self.assertEqual(result["schema_retry_count"], 1)
        self.assertTrue(result["draft"]["abstain"])
        self.assertEqual(result["draft"]["handoff_reason"], "invalid_model_output")

    async def test_actual_graph_enforces_total_budget_including_initial_read(self):
        model = ScriptedModel([{"action": "tool", "tool_name": "list_case_events", "arguments": {"case_id": 7}}])
        tools = FakeTools()
        result = await AnalystCopilot(tools, model, FakeStore(), AgentConfig(total_tool_budget=1)).run(7, run_id="graph-budget")
        self.assertEqual(len(tools.calls), 1)
        self.assertEqual(result["draft"]["handoff_reason"], "budget_exhausted")
        self.assertEqual(result["calls"][-1]["executed"], False)

    async def test_prompt_injection_does_not_create_write_capability(self):
        model = ScriptedModel([{"action": "tool", "tool_name": "resolve_case", "arguments": {"case_id": 7}}]*2)
        tools = FakeTools()
        result = await AnalystCopilot(tools, model, FakeStore()).run(7, run_id="graph-write")
        self.assertEqual(len(tools.calls), 1)
        self.assertEqual(result["draft"]["handoff_reason"], "invalid_model_output")

    async def test_memory_read_is_case_scoped_and_warmup_does_not_save(self):
        store = FakeStore()
        await AnalystCopilot(FakeTools(), ScriptedModel([{"action": "draft", "draft": draft()}]), store).run(7, run_id="graph-warmup", persist=False)
        self.assertEqual(store.saves, [])
        self.assertEqual(store.load_memory(7), [])

    async def test_nonempty_formal_memory_refusal_never_saves_or_changes_existing_memory(self):
        store = FakeStore()
        store.memory[7] = [{"case_id": 7, "draft": {"summary": "Existing human-reviewed memory"}}]
        before = json.dumps(store.memory, sort_keys=True, separators=(",", ":"))
        model, tools = ScriptedModel([]), FakeTools()
        with self.assertRaises(EvaluationProtocolError):
            await AnalystCopilot(tools, model, store).run(7, run_id="graph-refused-memory", require_empty_memory=True)
        self.assertEqual(json.dumps(store.memory, sort_keys=True, separators=(",", ":")), before)
        self.assertEqual(store.saves, [])
        self.assertEqual(tools.calls, [])
        self.assertEqual(model.messages, [])

    async def test_memory_load_failure_hands_off_without_saving_over_unknown_memory(self):
        class ReadFailure(FakeStore):
            def load_memory(self, case_id): raise RuntimeError("synthetic read failure")
        store = ReadFailure()
        result = await AnalystCopilot(FakeTools(), ScriptedModel([]), store).run(7, run_id="graph-memory-read-error")
        self.assertEqual(result["draft"]["handoff_reason"], "memory_error")
        self.assertIsNone(result["receipt"])
        self.assertEqual(store.saves, [])


if __name__ == "__main__": unittest.main()
