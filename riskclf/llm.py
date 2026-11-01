"""OpenAI-compatible chat client with structured output, timeout, and validation.

Works with any /v1/chat/completions server: llama.cpp `llama-server` (CPU or CUDA),
Ollama, vLLM, or a hosted provider with an API key. Configure with env vars:
LLM_BASE_URL, LLM_MODEL, LLM_API_KEY, LLM_TIMEOUT_S.
"""
import json
import os
import re
import time
from dataclasses import dataclass, field

import httpx
from pydantic import ValidationError

from .schema import JSON_SCHEMA, LlmVerdict

SYSTEM_PROMPT = (
    "You are a payments risk analyst. Classify a customer-facing text message as 'scam' "
    "(spam, phishing, prize/lottery bait, premium-rate or subscription traps, unsolicited promotions) "
    "or 'legit' (ordinary personal or business conversation). Reply with only a JSON object with keys "
    "label (scam|legit), category (phishing_or_account|prize_or_lottery|premium_rate_or_subscription|"
    "marketing|personal|other) and reason (under 20 words)."
)

FEW_SHOT = [
    # Few-shot examples are hand-written (not taken from the dataset) so they cannot leak into the test set.
    ("CONGRATS! You have been selected for a £500 shopping voucher. Call 09061234567 now to claim, 150p/min",
     {"label": "scam", "category": "prize_or_lottery", "reason": "Unsolicited prize claim with premium-rate number."}),
    ("Hey, are we still on for dinner at 7? I can pick you up.",
     {"label": "legit", "category": "personal", "reason": "Ordinary personal plan between friends."}),
    ("Your account has been suspended. Verify your details at http://secure-login-update.com now",
     {"label": "scam", "category": "phishing_or_account", "reason": "Account-suspension pretext with a login link."}),
    ("I'll transfer the rent tomorrow morning, sorry it's late",
     {"label": "legit", "category": "personal", "reason": "Normal payment note between people who know each other."}),
]


class LlmError(Exception):
    def __init__(self, kind: str, msg: str = ""):
        super().__init__(f"{kind}: {msg}")
        self.kind = kind  # timeout | http | invalid_json | schema


@dataclass
class LlmResult:
    verdict: LlmVerdict
    latency_ms: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: str = field(default="", repr=False)


def build_messages(text: str) -> list[dict]:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for ex, ans in FEW_SHOT:
        msgs.append({"role": "user", "content": ex})
        msgs.append({"role": "assistant", "content": json.dumps(ans)})
    msgs.append({"role": "user", "content": text})
    return msgs


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def parse_verdict(content: str) -> LlmVerdict:
    try:
        obj = json.loads(_FENCE.sub("", content.strip()))
    except json.JSONDecodeError as e:
        raise LlmError("invalid_json", str(e)) from e
    try:
        return LlmVerdict.model_validate(obj)
    except ValidationError as e:
        raise LlmError("schema", str(e.errors()[:1])) from e


class LlmClient:
    def __init__(self, base_url: str | None = None, model: str | None = None, api_key: str | None = None,
                 timeout_s: float | None = None, structured: bool | None = None,
                 transport: httpx.BaseTransport | None = None):
        self.base_url = (base_url or os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080/v1")).rstrip("/")
        self.model = model or os.getenv("LLM_MODEL", "qwen2.5-1.5b-instruct-q4_k_m")
        key = api_key if api_key is not None else os.getenv("LLM_API_KEY", "")
        self.timeout_s = timeout_s if timeout_s is not None else float(os.getenv("LLM_TIMEOUT_S", "10"))
        self.seed = int(os.getenv("LLM_SEED", "42"))
        # json_schema response_format is supported by llama-server, vLLM, OpenAI; set LLM_STRUCTURED=0 to disable
        self.structured = structured if structured is not None else os.getenv("LLM_STRUCTURED", "1") == "1"
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._http = httpx.Client(base_url=self.base_url, headers=headers, transport=transport,
                                  timeout=httpx.Timeout(self.timeout_s, connect=min(2.0, self.timeout_s)))

    def classify(self, text: str) -> LlmResult:
        body = {"model": self.model, "messages": build_messages(text), "temperature": 0, "max_tokens": 80, "seed": self.seed}
        if self.structured:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "risk_verdict", "strict": True, "schema": JSON_SCHEMA}}
        t0 = time.perf_counter()
        try:
            r = self._http.post("/chat/completions", json=body)
        except httpx.TimeoutException as e:
            raise LlmError("timeout", str(e)) from e
        except httpx.HTTPError as e:
            raise LlmError("http", str(e)) from e
        if r.status_code != 200:
            raise LlmError("http", f"status {r.status_code}: {r.text[:200]}")
        try:
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("completion content must be text")
            usage = data.get("usage") or {}
            prompt_tokens = int(usage.get("prompt_tokens", 0))
            completion_tokens = int(usage.get("completion_tokens", 0))
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
            raise LlmError("invalid_json", "malformed completion response") from e
        return LlmResult(parse_verdict(content), (time.perf_counter() - t0) * 1000,
                         prompt_tokens, completion_tokens, content)
