"""ZAP packaged-scan hooks: one owned localhost origin and an explicit active-scan cap."""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import zap_common


def zap_started(zap, target):
    expected = os.environ["APPSEC_TARGET"]
    parsed = urlsplit(target)
    if target != expected or parsed.hostname != "127.0.0.1" or parsed.port not in (5001, 5002):
        raise RuntimeError("Target is outside the owned app origins")
    name = "owned-appsec-" + str(parsed.port)
    for existing in zap.context.context_list:
        zap.context.set_context_in_scope(existing, "false")
    context_id = zap.context.new_context(name)
    pattern = "^" + re.escape(expected.rstrip("/")) + r"(?:/.*)?$"
    zap.context.include_in_context(name, pattern)
    zap.context.set_context_in_scope(name, "true")
    zap_common.context_id = context_id
    zap_common.context_name = name
    zap.core.set_mode("protect")


def zap_tuned(zap):
    minutes = int(os.environ["APPSEC_ACTIVE_MINUTES"])
    zap.ascan.set_option_max_scan_duration_in_mins(minutes)
    actual = int(zap.ascan.option_max_scan_duration_in_mins)
    if actual != minutes or zap.core.mode != "protect":
        raise RuntimeError("Owned scan configuration was not applied")
    proof = {
        "target": os.environ["APPSEC_TARGET"],
        "mode": zap.core.mode,
        "context_id": zap_common.context_id,
        "context_name": zap_common.context_name,
        "include_regexs": zap.context.include_regexs(zap_common.context_name),
        "active_scan_max_minutes": actual,
    }
    Path(os.environ["APPSEC_SCOPE_PROOF"]).write_text(json.dumps(proof, indent=2) + "\n")
