"""Synthetic boundary regressions. No live model, network or database is used."""
import copy
import unittest

from copilot.agent.llm import ModelTurn,RecordedChatModel,request_hash
from copilot.eval.model_events import validate_event_record,validate_tool_events
from copilot.eval.recording import TracingModel,TracingTools,TracingStore,RecordedTools,RecordedStore


def immediate(coroutine):
    """These stub await chains contain no suspension or event-loop I/O."""
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    raise AssertionError("A synthetic stub unexpectedly required an event loop")


class SyntheticAdapterFailure(Exception):pass


class RecordingEventTests(unittest.TestCase):
    def setUp(self):
        self.messages=[{"role":"user","content":"Synthetic recording regression fixture"}]
        self.schema={"type":"object"}

    def failed_model(self):
        class Failure:
            async def complete(inner,messages,schema):raise SyntheticAdapterFailure("sensitive exception text is deliberately not recorded")
        model=TracingModel(Failure())
        with self.assertRaises(SyntheticAdapterFailure):immediate(model.complete(self.messages,self.schema))
        return model

    def test_successful_model_response_remains_original_response(self):
        class Response:
            async def complete(inner,messages,schema):
                return ModelTurn("{}",{"synthetic_unit_fixture":True},{"prompt_tokens":2,"completion_tokens":1},.01,request_hash(messages,schema),messages)
        model=TracingModel(Response());turn=immediate(model.complete(self.messages,self.schema))
        record={"status":"complete","model_events":model.events,"attempts":[{"content":"{}"}],"raw_completed_model_turns":model.turns}
        self.assertEqual(validate_event_record(record),{"actual_model_responses":1,"recorded_model_errors":0})
        replay=RecordedChatModel(events=model.events)
        self.assertEqual(immediate(replay.complete(self.messages,self.schema)).raw_response,turn.raw_response)

    def test_actual_model_error_has_no_fabricated_content_usage_or_response(self):
        model=self.failed_model();event=model.events[0]
        self.assertEqual(model.turns,[])
        for key in ("turn","content","usage","raw_response"):self.assertNotIn(key,event)
        self.assertEqual(event["actual_error"]["type"],"SyntheticAdapterFailure")
        record={"status":"complete","model_events":model.events,"attempts":[{"transport_error_type":"SyntheticAdapterFailure"}],"raw_completed_model_turns":[]}
        self.assertEqual(validate_event_record(record),{"actual_model_responses":0,"recorded_model_errors":1})
        replay=RecordedChatModel(events=model.events)
        with self.assertRaises(RuntimeError) as error:immediate(replay.complete(self.messages,self.schema))
        self.assertEqual(type(error.exception).__name__,"SyntheticAdapterFailure")
        self.assertEqual(replay.position,1)

    def test_prompt_mismatch_does_not_consume_and_violation_is_permanent(self):
        model=RecordedChatModel(events=self.failed_model().events)
        with self.assertRaises(AssertionError):immediate(model.complete([{"role":"user","content":"changed"}],self.schema))
        self.assertEqual(model.position,0)
        with self.assertRaises(RuntimeError):immediate(model.complete(self.messages,self.schema))
        self.assertEqual(model.position,1)
        self.assertEqual(model.violations,["recorded_request_hash_mismatch"])

    def test_schema_mismatch_does_not_consume_event(self):
        model=RecordedChatModel(events=self.failed_model().events)
        with self.assertRaises(AssertionError):immediate(model.complete(self.messages,{"type":"string"}))
        self.assertEqual(model.position,0)
        self.assertTrue(model.violations)

    def test_actual_mcp_transport_error_replays_type_without_fake_response(self):
        class Failure:
            async def call(inner,name,args):raise SyntheticAdapterFailure()
        tools=TracingTools(Failure())
        with self.assertRaises(SyntheticAdapterFailure):immediate(tools.call("get_case",{"case_id":7}))
        self.assertNotIn("response",tools.records[0])
        record={"status":"complete","tool_trace":tools.records}
        self.assertEqual(validate_tool_events(record),{"actual_mcp_responses":0,"recorded_mcp_transport_errors":1})
        replay=RecordedTools(tools.records)
        with self.assertRaises(RuntimeError) as error:immediate(replay.call("get_case",{"case_id":7}))
        self.assertEqual(type(error.exception).__name__,"SyntheticAdapterFailure")
        self.assertEqual(replay.position,1)

    def test_application_ok_false_is_preserved_as_response(self):
        response={"ok":False,"error":{"code":"embedding_unavailable","message":"No matching vector"}}
        class Rejection:
            async def call(inner,name,args):return response
        tools=TracingTools(Rejection())
        self.assertEqual(immediate(tools.call("search_similar_cases",{"case_id":7,"k":5})),response)
        self.assertEqual(tools.records[0]["kind"],"response")
        self.assertEqual(validate_tool_events({"status":"complete","tool_trace":tools.records}),{"actual_mcp_responses":1,"recorded_mcp_transport_errors":0})

    def test_tool_mismatch_does_not_consume_and_violation_is_permanent(self):
        row={"name":"get_case","arguments":{"case_id":7},"response":{"ok":True,"data":{"case_id":7}}}
        tools=RecordedTools([row])
        with self.assertRaises(AssertionError):immediate(tools.call("get_case",{"case_id":8}))
        self.assertEqual(tools.position,0)
        immediate(tools.call("get_case",{"case_id":7}))
        self.assertEqual(tools.position,1)
        self.assertEqual(tools.violations,["recorded_tool_invocation_mismatch"])

    def test_store_load_mismatch_does_not_consume_and_violation_is_permanent(self):
        store=RecordedStore([{"case_id":7,"memory":[]}],[])
        with self.assertRaises(AssertionError):store.load_memory(8)
        self.assertEqual(store.load_position,0)
        self.assertEqual(store.load_memory(7),[])
        self.assertEqual(store.violations,["recorded_memory_case_mismatch"])

    def test_store_save_mismatch_or_missing_kwarg_does_not_consume(self):
        row={"case_id":7,"run_id":"unit","redacted_memory":[],"draft":{"fixture":True},"receipt":{"case_id":7,"actor":"copilot"}}
        store=RecordedStore([],[row])
        with self.assertRaises(AssertionError):store.save_turn(7,run_id="unit",redacted_memory=[])
        self.assertEqual(store.save_position,0)
        with self.assertRaises(AssertionError):store.save_turn(7,run_id="unit",redacted_memory=[],draft={"changed":True})
        self.assertEqual(store.save_position,0)
        self.assertEqual(store.save_turn(7,run_id="unit",redacted_memory=[],draft={"fixture":True}),row["receipt"])
        self.assertEqual(store.violations,["recorded_persistence_mismatch","recorded_persistence_mismatch"])

    def test_genuine_failed_case_refuses_whole_export(self):
        model=self.failed_model()
        record={"status":"failed","model_events":model.events,"attempts":[{"transport_error_type":"SyntheticAdapterFailure"}],"raw_completed_model_turns":[]}
        with self.assertRaisesRegex(ValueError,"Failed cases block"):validate_event_record(record)
        with self.assertRaisesRegex(ValueError,"failures block"):validate_tool_events({"status":"failed","tool_trace":[]})

    def test_database_failure_never_records_fake_memory_or_receipt(self):
        class Failure:
            def load_memory(inner,case_id):raise SyntheticAdapterFailure()
            def save_turn(inner,case_id,**kwargs):raise SyntheticAdapterFailure()
        store=TracingStore(Failure())
        with self.assertRaises(SyntheticAdapterFailure):store.load_memory(7)
        with self.assertRaises(SyntheticAdapterFailure):store.save_turn(7,run_id="unit",redacted_memory=[],draft={})
        self.assertEqual(store.loads,[]);self.assertEqual(store.saves,[])
        self.assertEqual(len(store.errors),2)
        with self.assertRaisesRegex(ValueError,"storage failures"):
            validate_tool_events({"status":"complete","tool_trace":[],"store_errors":store.errors})

    def test_error_records_cannot_include_an_invented_response(self):
        model=self.failed_model();record={"status":"complete","model_events":copy.deepcopy(model.events),"attempts":[{"transport_error_type":"SyntheticAdapterFailure"}],"raw_completed_model_turns":[]}
        record["model_events"][0]["usage"]={"completion_tokens":0}
        with self.assertRaisesRegex(ValueError,"must not invent"):validate_event_record(record)
        tool={"kind":"transport_error","name":"get_case","arguments":{"case_id":7},"elapsed_seconds":.01,
              "actual_error":{"type":"SyntheticAdapterFailure","module":__name__},"response":{"ok":False}}
        with self.assertRaisesRegex(ValueError,"must not invent"):validate_tool_events({"status":"complete","tool_trace":[tool]})


if __name__=="__main__":unittest.main()
