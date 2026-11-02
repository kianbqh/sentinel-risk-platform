"""Small authenticated ASGI draft endpoint; human mutations stay in the console.

Launch with ``uvicorn copilot.agent.api:create_app --factory --host 127.0.0.1``
after setting COPILOT_AGENT_CONFIG to a private JSON configuration file.
"""
import asyncio
import hmac
import json
import os
from pathlib import Path
import re
import uuid

from .llm import LocalChatModel
from .schemas import strict_loads
from .workflow import AgentConfig, AnalystCopilot


class DraftAPI:
    def __init__(self, config, *, runner=None):
        self.config, self.runner = config, runner
        self.token = Path(config["api_token_file"]).read_text().strip()
        if len(self.token) < 32:
            raise ValueError("A private API token with at least thirty-two characters is required")
        self.lock = asyncio.Lock()

    async def _run(self, case_id, task):
        from copilot.db.persistence import PostgresDraftStore
        from copilot.mcp_server.client import MCPToolClient
        token = Path(self.config["mcp_token_file"]).read_text().strip()
        store = PostgresDraftStore(os.environ[self.config["writer_dsn_env"]])
        async with MCPToolClient(self.config["mcp_endpoint"], token) as tools, LocalChatModel(**self.config["llm"]) as model:
            agent = AnalystCopilot(tools, model, store, AgentConfig(**self.config["agent"]))
            return await agent.run(case_id, run_id="api-"+uuid.uuid4().hex, task=task)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                if event["type"] == "lifespan.startup": await send({"type": "lifespan.startup.complete"})
                elif event["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"}); return
            return
        if scope["type"] != "http":
            return
        async def reply(status, body):
            data = json.dumps(body, allow_nan=False).encode()
            await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": data})
        values = [value for key, value in scope.get("headers", []) if key.lower() == b"authorization"]
        supplied = values[0] if len(values) == 1 else b""
        if not hmac.compare_digest(supplied, b"Bearer "+self.token.encode()):
            await reply(401, {"error": "unauthorized"}); return
        match = re.fullmatch(r"/v1/copilot/cases/([1-9][0-9]{0,18})/drafts", scope.get("path", ""))
        if scope.get("method") != "POST" or not match:
            await reply(404, {"error": "not_found"}); return
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.disconnect": return
            body += message.get("body", b"")
            if len(body) > 16384:
                await reply(413, {"error": "request_too_large"}); return
            if not message.get("more_body", False): break
        try:
            value = strict_loads(body.decode())
            if not isinstance(value, dict) or set(value) != {"task"}:
                raise ValueError("Invalid request")
            case_id = int(match[1])
            task = value["task"]
            if not isinstance(task, str) or not task or len(task) > 6000 or case_id > 9223372036854775807:
                raise ValueError("Invalid request")
        except (ValueError, UnicodeDecodeError):
            await reply(400, {"error": "invalid_request"}); return
        # A bounded single-slot endpoint prevents unbounded queued GPU work.
        if self.lock.locked():
            await reply(429, {"error": "copilot_busy"}); return
        async with self.lock:
            try:
                result = await (self.runner or self._run)(case_id, task)
                await reply(201, {"draft": result["draft"], "receipt": result["receipt"], "applied": False})
            except asyncio.TimeoutError:
                await reply(504, {"error": "copilot_timeout"})
            except Exception:
                await reply(503, {"error": "copilot_unavailable"})


def create_app():
    return DraftAPI(json.loads(Path(os.environ["COPILOT_AGENT_CONFIG"]).read_text()))
