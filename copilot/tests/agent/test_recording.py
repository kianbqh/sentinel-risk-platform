import json
import unittest

from copilot.agent.llm import RecordedChatModel, request_hash
from copilot.agent.schemas import decision_schema, fallback
from copilot.eval.metrics import evaluate_case, token_usage, summarize
from copilot.eval.recording import RecordedTools, TracingTools
from copilot.tests.agent.fakes import draft, case


class RecordingTests(unittest.IsolatedAsyncioTestCase):
    async def test_recorded_model_rejects_changed_prompt_or_schema(self):
        messages = [{"role": "user", "content": "Frozen synthetic fixture"}]
        row = {"content": "{}", "raw_response": {"synthetic": True}, "usage": {}, "messages": messages,
               "request_sha256": request_hash(messages, decision_schema())}
        model = RecordedChatModel([row])
        with self.assertRaisesRegex(AssertionError, "prompt/schema"):
            await model.complete([{"role": "user", "content": "A different request"}], decision_schema())

    async def test_recorded_tools_require_exact_arguments_and_call_order(self):
        record = {"name": "search_similar_cases", "arguments": {"case_id": 7, "k": 5}, "response": {"ok": True, "data": {}}}
        with self.assertRaisesRegex(AssertionError, "invocation mismatch"):
            await RecordedTools([record]).call("search_similar_cases", {"case_id": 7, "k": 4})

    async def test_runtime_source_drift_is_retained_as_evaluation_violation(self):
        from copilot.eval.protocol import call_key
        class Changed:
            async def call(self, name, args): return {"ok": True, "data": {"case_id": 7, "risk_score": .9}}
        wanted = {"ok": True, "data": {"case_id": 7, "risk_score": .2}}
        trace = TracingTools(Changed(), {call_key("get_case", {"case_id": 7}): wanted})
        with self.assertRaises(ValueError): await trace.call("get_case", {"case_id": 7})
        self.assertEqual(len(trace.records), 1)
        self.assertEqual(len(trace.violations), 1)


class FailureMetricTests(unittest.TestCase):
    def test_missing_usage_is_unknown_rather_than_zero(self):
        value = token_usage([{"usage": {"prompt_tokens": 10, "completion_tokens": 3}}, {"usage": {}}])
        self.assertIsNone(value["input_tokens"])
        self.assertIsNone(value["output_tokens"])

    def test_passing_fallback_does_not_hide_invalid_raw_model_draft(self):
        row = {"case": case(), "expected_model_calls": [], "ground_truth": {"allowed_facts": [case()], "expected_abstain": True}}
        raw = draft(summary="Contact x@example.invalid about amount 999.77.")
        state = {"draft": fallback(7, "pii_output"), "attempts": [{"content": json.dumps({"action": "draft", "draft": raw}), "schema_valid": True,
                 "pii_findings": [{"kind": "email"}], "usage": {"prompt_tokens": 10, "completion_tokens": 8}}],
                 "calls": [], "wall_seconds": 2.0, "schema_failures": 0, "schema_retry_count": 0, "guard_reasons": ["pii_output"]}
        metrics = evaluate_case(row, state)
        self.assertEqual(metrics["pre_guard_pii_leaks"], 1)
        self.assertEqual(metrics["final_pii_leaks"], 0)
        self.assertFalse(metrics["pre_guard_faithfulness"]["faithful"])
        self.assertTrue(metrics["final_faithfulness"]["faithful"])
        self.assertTrue(metrics["pre_guard_schema_valid"])

    def test_case_storage_failure_keeps_raw_pii_and_token_accounting(self):
        content = json.dumps({"action": "draft", "draft": draft(summary="Contact x@example.invalid")})
        attempt = {"content": content, "raw_response": {"choices": [{"message": {"content": content}}]}, "usage": {"prompt_tokens": 10, "completion_tokens": 8}}
        metrics = summarize([{"status": "failed", "wall_seconds": 2., "raw_completed_model_turns": [attempt]}])
        self.assertEqual(metrics["pre_guard_pii_leak_count"], 1)
        self.assertEqual(metrics["tokens_per_case"]["input_tokens"]["mean"], 10)
        self.assertEqual(metrics["final_schema_valid_rate"], 0)


if __name__ == "__main__": unittest.main()
