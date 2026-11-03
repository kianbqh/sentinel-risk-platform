"""Revocable opaque service tokens, independent of analyst-console JWTs."""
import hashlib
import hmac
import time

from .contracts import TOOL_SCOPES


class TokenRegistry:
    def __init__(self, records, resource):
        self.records, self.resource = records, resource
        for record in records:
            if len(record["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in record["sha256"]) or set(record["scopes"]) - set(TOOL_SCOPES.values()) or type(record["expires_at"]) is not int:
                raise ValueError("Invalid service-token registry")

    def lookup(self, token, now=None):
        now = time.time() if now is None else now
        if not isinstance(token, str) or len(token) < 32 or len(token) > 1024:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        for record in self.records:
            if hmac.compare_digest(digest, record["sha256"]):
                if record["resource"] != self.resource or record["expires_at"] <= now or record.get("not_before", 0) > now:
                    return None
                return record
        return None
