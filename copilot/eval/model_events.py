"""Validate faithful recorded model boundaries without importing an LLM stack."""
import math
import re

from copilot.agent.llm import canonical, request_hash
from copilot.agent.redaction import redact


def validate_event_record(record):
    if record["status"] != "complete":
        raise ValueError("Failed cases block whole-cassette export; no case filtering")
    events=record.get("model_events")
    attempts=record.get("attempts",[])
    if not isinstance(events,list) or len(events)!=len(attempts) or (not events and not record.get("tool_trace")):
        raise ValueError("Every attempted model boundary requires an actual recorded event")
    responses,errors=[],[]
    for event,attempt in zip(events,attempts):
        if request_hash(event["messages"],event["schema"])!=event["request_sha256"]:
            raise ValueError("Recorded event request hash mismatch")
        if canonical(event["messages"])!=canonical(redact(event["messages"])):
            raise ValueError("Recorded model request was not redacted")
        elapsed=event.get("elapsed_seconds")
        if type(elapsed) not in (int,float) or not math.isfinite(elapsed) or elapsed<0:
            raise ValueError("Recorded elapsed time is invalid")
        if event["kind"]=="response":
            turn=event["turn"]
            if turn["request_sha256"]!=event["request_sha256"] or canonical(turn["messages"])!=canonical(event["messages"]) or turn["content"]!=attempt.get("content"):
                raise ValueError("Recorded response differs from its actual request/attempt")
            responses.append(turn)
        elif event["kind"]=="transport_error":
            if any(key in event for key in ("turn","raw_response","content","usage")):
                raise ValueError("Recorded error must not invent a model response")
            name=event["actual_error"]["type"]
            if not isinstance(name,str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,99}",name) or attempt.get("transport_error_type")!=name:
                raise ValueError("Actual model error type differs from its guarded attempt")
            errors.append(event)
        else:raise ValueError("Unknown actual model event kind")
    if canonical(responses)!=canonical(record.get("raw_completed_model_turns",[])):
        raise ValueError("Completed responses differ from ordered event responses")
    return {"actual_model_responses":len(responses),"recorded_model_errors":len(errors)}


def validate_tool_events(record):
    if record["status"]!="complete" or record.get("store_errors"):
        raise ValueError("Actual case/storage failures block whole-cassette export")
    responses,errors=0,0
    for event in record["tool_trace"]:
        if canonical(event["arguments"])!=canonical(redact(event["arguments"])):
            raise ValueError("Recorded MCP arguments were not redacted")
        elapsed=event.get("elapsed_seconds")
        if type(elapsed) not in (int,float) or not math.isfinite(elapsed) or elapsed<0:
            raise ValueError("Recorded MCP elapsed time is invalid")
        if event.get("kind")=="response":
            response=event["response"]
            if not isinstance(response,dict) or type(response.get("ok")) is not bool:
                raise ValueError("Actual MCP response envelope is invalid")
            responses+=1
        elif event.get("kind")=="transport_error":
            if any(key in event for key in ("response","data","ok")):
                raise ValueError("MCP transport error must not invent a response")
            name=event["actual_error"]["type"]
            if not isinstance(name,str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,99}",name):
                raise ValueError("Recorded actual MCP error type is invalid")
            errors+=1
        else:raise ValueError("Unknown actual MCP event kind")
    return {"actual_mcp_responses":responses,"recorded_mcp_transport_errors":errors}
