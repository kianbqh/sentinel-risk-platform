"""Official MCP v2 Streamable HTTP adapter. No stdio entry point is exposed."""
import argparse
import json
import os
from pathlib import Path

from .contracts import TOOL_SCOPES, ToolRejected, tool_schemas, validate_call, validate_envelope
from .token_registry import TokenRegistry


def create_server(service, registry, *, resource, issuer):
    from mcp import MCPError
    from mcp.server import MCPServer
    from mcp.server.auth.middleware.auth_context import get_access_token
    from mcp.server.auth.provider import AccessToken, TokenVerifier
    from mcp.server.auth.settings import AuthSettings
    from mcp.server.mcpserver.tools import Tool
    from mcp.types import ToolAnnotations
    from pydantic import AnyHttpUrl
    from typing import Any, NotRequired, TypedDict

    class ToolFailure(TypedDict):
        code: str
        message: str

    class ToolEnvelope(TypedDict):
        ok: bool
        data: NotRequired[dict[str, Any]]
        error: NotRequired[ToolFailure]

    ToolEnvelope.__pydantic_config__ = {"extra": "forbid", "strict": True,
        "json_schema_extra": {"oneOf": [{"properties": {"ok": {"const": True}}, "required": ["data"], "not": {"required": ["error"]}},
            {"properties": {"ok": {"const": False}}, "required": ["error"], "not": {"required": ["data"]}}]}}

    class OpaqueVerifier(TokenVerifier):
        async def verify_token(self, token):
            record = registry.lookup(token)
            if record is None:
                return None
            return AccessToken(token=token, client_id=record["client_id"], scopes=record["scopes"],
                expires_at=record["expires_at"], resource=record["resource"])

    async def strict_boundary(ctx, call_next):
        if ctx.method == "tools/call":
            raw = ctx.params
            try:
                if not isinstance(raw, dict) or set(raw) - {"name", "arguments", "_meta"}:
                    raise ToolRejected("invalid_arguments", "Invalid tool call envelope")
                validate_call(raw.get("name"), raw.get("arguments"))
            except ToolRejected as exc:
                raise MCPError(code=-32602, message=str(exc)) from None
        return await call_next(ctx)

    async def invoke(name, args):
        import anyio
        token = get_access_token()
        scopes = None if token is None else set(token.scopes)
        result = await anyio.to_thread.run_sync(lambda: service.call(name, args, scopes))
        return validate_envelope(result)

    async def get_case(case_id: int) -> ToolEnvelope:
        """Read one case and its source-backed alert evidence."""
        return await invoke("get_case", {"case_id": case_id})

    async def list_case_events(case_id: int) -> ToolEnvelope:
        """Read the audit history for one case."""
        return await invoke("list_case_events", {"case_id": case_id})

    async def score_transaction(features: dict[str, float]) -> ToolEnvelope:
        """Score the complete frozen transaction feature vector with publish=false."""
        return await invoke("score_transaction", {"features": features})

    async def explain_score(case_id: int) -> ToolEnvelope:
        """Decompose a supported transaction score into exact log-odds contributions."""
        return await invoke("explain_score", {"case_id": case_id})

    async def search_similar_cases(case_id: int, k: int) -> ToolEnvelope:
        """Find source-evidence neighbors with pgvector cosine distance."""
        return await invoke("search_similar_cases", {"case_id": case_id, "k": k})

    # Publish exact, extra-forbidding JSON schemas through the public Tool API.
    # The raw middleware and handler validation independently enforce them.
    registered = [Tool.from_function(fn, structured_output=True, annotations=ToolAnnotations(
        read_only_hint=True, destructive_hint=False, open_world_hint=False)) for fn in
        (get_case, list_case_events, score_transaction, explain_score, search_similar_cases)]
    for tool in registered:
        tool.parameters = tool_schemas()[tool.name]
    server = MCPServer("sentinel-copilot-readonly", version="1.0.0", tools=registered,
        token_verifier=OpaqueVerifier(), auth=AuthSettings(issuer_url=AnyHttpUrl(issuer),
            resource_server_url=AnyHttpUrl(resource), required_scopes=[], validate_token_resource=True),
        middleware=[strict_boundary])
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    from copilot.db.reader import PostgresCaseReader
    from .explanation import ExactExplainer
    from .gateway import GatewayScorer
    from .service import ToolService
    records = json.loads(Path(os.environ[config["token_registry_env"]]).read_text())["tokens"]
    registry = TokenRegistry(records, config["resource"])
    reader = PostgresCaseReader(os.environ[config["reader_dsn_env"]], embedding_model=config["embedding_model"],
        embedding_revision=config["embedding_revision"])
    explainer = ExactExplainer(config["coefficient_export"], config["joblib_model"],
        expected_export_sha256=config["coefficient_export_sha256"])
    service = ToolService(reader, GatewayScorer(config["gateway_origin"]), explainer)
    create_server(service, registry, resource=config["resource"], issuer=config["issuer"]).run(
        transport="streamable-http", host=config["host"], port=config["port"], stateless_http=True, json_response=True)


if __name__ == "__main__":
    main()
