"""Fixed-origin scoring adapter: publish is unconditionally false."""
import json
import math
import urllib.request
from urllib.parse import urlsplit

from .contracts import ToolRejected, finite_features


class GatewayScorer:
    def __init__(self, origin, timeout=15, opener=None):
        parts = urlsplit(origin)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in {"", "/"}:
            raise ValueError("Gateway origin must be a configured origin without credentials or path")
        self.url = origin.rstrip("/") + "/v1/transactions/score"
        self.timeout = timeout
        # Redirects are forbidden: never forward this request to another origin.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        self.opener = opener or urllib.request.build_opener(NoRedirect())

    def score(self, features):
        body = json.dumps({"features": finite_features(features), "publish": False}, allow_nan=False).encode()
        request = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with self.opener.open(request, timeout=self.timeout) as response:
            data = json.loads(response.read(1_000_001))
        if data.get("alert_published") is not False:
            raise ToolRejected("publish_contract_violation", "Gateway did not confirm inference-only scoring")
        if type(data.get("fraud_score")) not in (float, int) or not 0 <= data["fraud_score"] <= 1:
            raise ToolRejected("invalid_gateway_result", "Gateway probability is invalid")
        if type(data.get("flagged")) is not bool or type(data.get("threshold")) not in (float, int) or not math.isfinite(data["threshold"]) or not 0 <= data["threshold"] <= 1:
            raise ToolRejected("invalid_gateway_result", "Gateway decision schema is invalid")
        return {key: data[key] for key in ("fraud_score", "flagged", "threshold", "alert_published")}
