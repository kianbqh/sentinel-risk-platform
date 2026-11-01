"""Packaged ZAP hooks restricted to the owned analyst-console origin."""
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
import zap_common
from zap_seed import validate_seed
from zap_capture import capture, transport_probes

TARGET = "http://127.0.0.1:13040"


def zap_started(zap, target):
    if target.rstrip("/") != TARGET or urlsplit(target).hostname != "127.0.0.1":
        raise RuntimeError("Only the owned analyst console is in scope")
    for context in zap.context.context_list:
        zap.context.set_context_in_scope(context, "false")
    name = "sentinel-owned-console"
    context_id = zap.context.new_context(name)
    pattern = "^" + re.escape(TARGET) + r"(?:/.*)?$"
    zap.context.include_in_context(name, pattern)
    zap.context.set_context_in_scope(name, "true")
    zap_common.context_id, zap_common.context_name = context_id, name
    zap.core.set_mode("protect")
    token = Path("/zap/analyst-token").read_text().strip()
    for rule in zap.replacer.rules:
        if rule.get("description") == "owned analyst JWT":
            zap.replacer.remove_rule("owned analyst JWT")
    zap.replacer.add_rule("owned analyst JWT", "true", "REQ_HEADER", "false", "Authorization",
                          replacement="Bearer " + token, url=pattern)
    # Seed authenticated API requests as well as the browser entry page.
    query = json.dumps({"query": "{me{id} alerts(limit:1){id case{id status events{action} labels{value}}} stats{total}}"})
    request = "POST /graphql HTTP/1.1\r\nHost: 127.0.0.1:13040\r\nContent-Type: application/json\r\nAuthorization: Bearer " + token + "\r\nContent-Length: " + str(len(query.encode())) + "\r\n\r\n" + query
    response = zap.core.send_request(request, followredirects="false")
    analyst_id = validate_seed(response, os.environ["SENTINEL_ANALYST_ID"])
    proof = {"target": TARGET, "mode": zap.core.mode, "context_id": context_id,
             "context_name": name, "include_regexs": zap.context.include_regexs(name),
             "authenticated_graphql_seed": True, "authenticated_analyst_id": analyst_id}
    Path(os.environ["SENTINEL_SCOPE_PROOF"]).write_text(json.dumps(proof, indent=2) + "\n")


def zap_tuned(zap):
    zap.ascan.set_option_max_scan_duration_in_mins(5)
    if int(zap.ascan.option_max_scan_duration_in_mins) != 5 or zap.core.mode != "protect":
        raise RuntimeError("Active scan cap/protection not applied")
    # Retain original temporary scan messages for differential-request evidence.
    zap.ascan.set_option_persist_temporary_messages("true")
    if str(zap.ascan.option_persist_temporary_messages).lower() != "true":
        raise RuntimeError("Original scan message retention was not enabled")
    # Limit generated GraphQL exploration to bounded schema depths.
    zap.graphql.set_option_max_query_depth(3)
    zap.graphql.set_option_max_args_depth(2)
    zap.graphql.set_option_max_additional_query_depth(0)
    zap.graphql.set_option_lenient_max_query_depth_enabled("false")
    limits = {"query_depth": int(zap.graphql.option_max_query_depth),
              "args_depth": int(zap.graphql.option_max_args_depth),
              "additional_depth": int(zap.graphql.option_max_additional_query_depth),
              "lenient_depth": str(zap.graphql.option_lenient_max_query_depth_enabled).lower()}
    if limits != {"query_depth": 3, "args_depth": 2, "additional_depth": 0, "lenient_depth": "false"}:
        raise RuntimeError("GraphQL generation limits were not applied")
    imported = zap.graphql.import_url(TARGET + "/graphql")
    if str(imported).upper() != "OK":
        raise RuntimeError("GraphQL schema import failed: " + str(imported))
    proof_path = Path(os.environ["SENTINEL_SCOPE_PROOF"])
    proof = json.loads(proof_path.read_text())
    proof.update(active_scan_max_minutes=5, graphql_generation_limits=limits,
                 graphql_import_result=str(imported))
    proof_path.write_text(json.dumps(proof, indent=2) + "\n")


def zap_pre_shutdown(zap):
    """Reports already exist; preserve observations before the daemon is shut down."""
    evidence = {}
    try:
        evidence = capture(zap, TARGET)
        evidence["source_revision"] = os.environ.get("SENTINEL_SOURCE_REVISION", "unknown")
        if evidence["active_scans"]:
            evidence["constructed_transport_probes"] = transport_probes(zap, TARGET, Path("/zap/analyst-token").read_text().strip())
    except Exception as error:
        evidence.update(complete=False, capture_error=type(error).__name__ + ": " + str(error))
    Path(os.environ["SENTINEL_HTTP_EVIDENCE"]).write_text(json.dumps(evidence, indent=2) + "\n")
