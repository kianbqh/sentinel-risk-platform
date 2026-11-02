"""Local-only llama.cpp HTTP adapter and exact request-hash replay."""
from dataclasses import dataclass
import hashlib
import json
import time
from urllib.parse import urlsplit

from .redaction import redact, findings


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def request_hash(messages, schema):
    return hashlib.sha256(canonical({"messages": messages, "schema": schema}).encode()).hexdigest()


@dataclass
class ModelTurn:
    content: str
    raw_response: dict
    usage: dict
    latency_seconds: float
    request_sha256: str
    messages: list

    def record(self):
        return {"content": self.content, "raw_response": self.raw_response, "usage": self.usage,
                "latency_seconds": self.latency_seconds, "request_sha256": self.request_sha256,
                "messages": self.messages}


class LocalChatModel:
    def __init__(self, endpoint, model, *, max_tokens=700, seed=42, timeout_seconds=45, schema_constrained=True):
        parsed = urlsplit(endpoint)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("LLM endpoint must be unauthenticated local HTTP")
        if parsed.path not in {"/v1", "/v1/"}:
            raise ValueError("LLM endpoint must end in /v1")
        self.url = endpoint.rstrip("/") + "/chat/completions"
        self.model, self.max_tokens, self.seed = model, max_tokens, seed
        self.timeout_seconds, self.schema_constrained = timeout_seconds, schema_constrained
        self._client = None

    async def __aenter__(self):
        import httpx
        self._client = httpx.AsyncClient(timeout=self.timeout_seconds, trust_env=False, follow_redirects=False)
        return self

    async def __aexit__(self, *exc):
        await self._client.aclose()
        self._client = None

    async def complete(self, messages, schema):
        if self._client is None:
            raise RuntimeError("LocalChatModel is not connected")
        # A second boundary also protects a caller outside the graph.
        clean = redact(messages)
        if findings(canonical(clean)):
            raise ValueError("Prompt redaction failed")
        payload = {"model": self.model, "messages": clean, "temperature": 0.0, "seed": self.seed,
                   "max_tokens": self.max_tokens, "stream": False}
        if self.schema_constrained:
            payload["response_format"] = {"type": "json_object", "schema": schema}
        started = time.perf_counter()
        # Streaming the transport lets us bound allocation before JSON parsing;
        # model output itself is intentionally requested without SSE.
        async with self._client.stream("POST", self.url, json=payload) as response:
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 1048576:
                    raise ValueError("Oversized model HTTP response")
                chunks.append(chunk)
        raw = json.loads(b"".join(chunks))
        content = raw["choices"][0]["message"].get("content")
        if not isinstance(content, str):
            raise ValueError("Local model returned no text content")
        usage = raw.get("usage", {})
        return ModelTurn(content, raw, usage, time.perf_counter()-started, request_hash(clean, schema), clean)


class RecordedChatModel:
    """Only replay a cassette exported from a completed actual-model run."""
    def __init__(self, turns):
        self.turns = list(turns)
        self.position = 0

    async def complete(self, messages, schema):
        if self.position >= len(self.turns):
            raise AssertionError("Recorded model turn exhausted")
        row = self.turns[self.position]
        self.position += 1
        if request_hash(redact(messages), schema) != row["request_sha256"]:
            raise AssertionError("Recorded prompt/schema differs from actual-model request")
        return ModelTurn(row["content"], row["raw_response"], row["usage"], 0.0, row["request_sha256"], row["messages"])
