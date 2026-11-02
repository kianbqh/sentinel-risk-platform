import asyncio
import json
import unittest

from copilot.agent.redaction import findings, redact
from copilot.agent.schemas import strict_loads, validate, validate_draft, decision_schema
from copilot.agent.workflow import AnalystCopilot, AgentConfig
from copilot.eval.faithfulness import check_text, tokens
from copilot.eval.metrics import tool_accuracy, summarize
from copilot.tests.agent.fakes import FakeTools, FakeStore, ScriptedModel, draft, case


class RedactionTests(unittest.TestCase):
    def test_all_required_patterns_and_ipv6_removed(self):
        raw = "a@example.invalid 4111 1111 1111 1111 +1 (202) 555-0146 192.0.2.44 2001:db8::7"
        self.assertEqual({item["kind"] for item in findings(raw)}, {"email", "card", "phone", "ipv4", "ipv6"})
        self.assertFalse(findings(redact(raw)))

    def test_recursion_key_redaction_does_not_mutate_source(self):
        value = {"ip": "arbitrary-protected-value", "nested": [{"email": "nonstandard"}], "other": "a@example.invalid"}
        clean = redact(value)
        self.assertEqual(clean["ip"], "[REDACTED_FIELD]")
        self.assertEqual(value["ip"], "arbitrary-protected-value")
        self.assertFalse(findings(json.dumps(clean)))

    def test_long_decimals_scientific_notation_and_feature_ids_survive(self):
        value = "score 0.1234567890123456789; V12=-1.234567890123456e-10; case 1000001"
        self.assertEqual(redact(value), value)


class FaithfulnessTests(unittest.TestCase):
    def test_documented_normalizations_without_rounding(self):
        self.assertTrue(check_text("+1,234.5000, −0.000, 1.0e-3", [1234.5, 0, .001])["faithful"])
        self.assertFalse(check_text("Amount 1234.51.", {"Amount": 1234.5})["faithful"])

    def test_long_decimal_is_not_rounded_by_decimal_context(self):
        self.assertFalse(check_text("9.8000000000000000000000000000001", ["9.8"])["faithful"])

    def test_percent_currency_and_identifiers_are_atomic_and_strict(self):
        self.assertFalse(check_text("20%", [.2])["faithful"])
        self.assertFalse(check_text("$100", [100])["faithful"])
        self.assertTrue(check_text("20% and $100.00", ["20%", "$100"])["faithful"])
        self.assertFalse(check_text("case123", ["case124", 123])["faithful"])
        self.assertFalse(check_text("V12", ["V1", "V2", 12])["faithful"])

    def test_sentence_punctuation_does_not_hide_unknown_amount(self):
        self.assertFalse(check_text("The amount was 999.77.", [17.25])["faithful"])

    def test_uuid_and_hash_cannot_pass_using_individual_numbers(self):
        value = "e9aef079-24b0-45f8-9499-9b6c6b12dd50"
        self.assertFalse(check_text(value, [24, 45, 9499])["faithful"])
        self.assertTrue(check_text(value, [value])["faithful"])


class SchemaTests(unittest.TestCase):
    def test_duplicate_nonfinite_and_non_json_model_output_rejected(self):
        for text in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":1e999}', '```json\n{}\n```'):
            with self.subTest(text=text), self.assertRaises(ValueError): strict_loads(text)

    def test_case_binding_boolean_and_write_fields_rejected(self):
        for value in ({**draft(), "case_id": 8}, {**draft(), "case_id": True}, draft(status="RESOLVED")):
            with self.subTest(value=value), self.assertRaises(Exception): validate_draft(value, 7)

    def test_allowlist_and_strict_arguments(self):
        for decision in ({"action": "tool", "tool_name": "resolve_case", "arguments": {"case_id": 7}},
                         {"action": "tool", "tool_name": "get_case", "arguments": {"case_id": 7, "token": "bad"}},
                         {"action": "tool", "tool_name": "get_case", "arguments": {"case_id": True}}):
            with self.subTest(decision=decision), self.assertRaises(Exception): validate(decision, decision_schema())


class BoundaryNodeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tools, self.store = FakeTools(), FakeStore()
        self.agent = AnalystCopilot(self.tools, ScriptedModel([]), self.store)
        self.state = {"case_id": 7, "run_id": "unit", "task": "Review", "persist": True, "require_empty_memory": False,
                      "calls": [], "attempts": [], "observations": [], "tool_count": 0, "model_count": 0, "schema_failures": 0, "guard_reasons": []}
        self.state.update(await self.agent.initialize(self.state))

    async def test_initial_read_and_prompts_redacted_without_raw_memory(self):
        self.assertEqual(self.tools.calls, [("get_case", {"case_id": 7})])
        self.assertFalse(findings(json.dumps(self.state["observations"])))
        self.assertFalse(findings(json.dumps(self.agent._messages(self.state))))
        self.assertEqual(self.state["calls"][0]["mandatory"], True)

    async def test_cross_case_and_duplicate_calls_do_not_reach_mcp(self):
        for args in ({"case_id": 8}, {"case_id": 7}):
            self.state["decision"] = {"action": "tool", "tool_name": "get_case", "arguments": args}
            update = await self.agent.tool_step(self.state)
            self.assertFalse(update["calls"][-1]["executed"])
        self.assertEqual(len(self.tools.calls), 1)

    async def test_changed_score_features_do_not_reach_gateway_tool(self):
        from copilot.mcp_server.contracts import FEATURE_NAMES
        self.state["decision"] = {"action": "tool", "tool_name": "score_transaction", "arguments": {"features": {key: 0.0 for key in FEATURE_NAMES}}}
        update = await self.agent.tool_step(self.state)
        self.assertEqual(update["calls"][-1]["rejection"], "score_features_not_current_case")
        self.assertEqual(len(self.tools.calls), 1)

    async def test_guard_abstains_on_pii_or_unknown_amount(self):
        for value, reason in ((draft(summary="Call a@example.invalid"), "pii_output"), (draft(summary="Amount 999.77."), "unfaithful_output")):
            self.state["draft"] = value
            update = await self.agent.guard_step(self.state)
            self.assertTrue(update["draft"]["abstain"])
            self.assertEqual(update["draft"]["handoff_reason"], reason)

    async def test_low_confidence_and_unsupported_evidence_source_handoff(self):
        self.state["draft"] = draft(confidence=.2)
        self.assertEqual((await self.agent.guard_step(self.state))["draft"]["handoff_reason"], "low_confidence")
        self.state["draft"] = draft(key_evidence=[{"source": "explain_score", "fact": "Amount 17.25."}])
        self.assertEqual((await self.agent.guard_step(self.state))["draft"]["handoff_reason"], "unfaithful_output")

    async def test_budget_rejection_is_recorded_but_not_executed(self):
        self.state.update(tool_count=5, model_count=1, attempts=[{"content": json.dumps({"action": "tool", "tool_name": "list_case_events", "arguments": {"case_id": 7}})}])
        update = await self.agent.validate_step(self.state)
        self.assertEqual(update["error"], "budget_exhausted")
        self.assertFalse(update["calls"][-1]["executed"])
        self.assertEqual(len(self.tools.calls), 1)

    async def test_memory_is_case_scoped_and_redacted_before_storage(self):
        self.state["draft"] = draft()
        await self.agent.persist_step(self.state)
        self.assertEqual(self.store.load_memory(8), [])
        self.assertFalse(findings(json.dumps(self.store.load_memory(7))))
        self.assertEqual(self.store.saves[0]["case_id"], 7)


class MetricTests(unittest.TestCase):
    def test_set_match_keeps_arguments_but_ignores_order_and_duplicates(self):
        calls = [{"name": "search_similar_cases", "arguments": {"case_id": 7, "k": 5}}]
        result = tool_accuracy(calls, calls+calls)
        self.assertFalse(result["tool_exact_match"]); self.assertTrue(result["tool_set_match"])
        self.assertFalse(tool_accuracy(calls, [{"name": "search_similar_cases", "arguments": {"case_id": 7, "k": 4}}])["tool_set_match"])

    def test_failed_cases_are_not_removed_from_denominator(self):
        result = summarize([{"status": "failed", "wall_seconds": 2.0}])
        self.assertEqual(result["final_schema_valid_rate"], 0)
        self.assertEqual(result["tool_exact_accuracy"], 0)
        self.assertEqual(result["latency_seconds"]["p95"], 2.0)


if __name__ == "__main__": unittest.main()
