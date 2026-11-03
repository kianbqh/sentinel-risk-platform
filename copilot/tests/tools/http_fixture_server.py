"""Only used by explicit real-HTTP tests; no database or external gateway."""
import argparse
import hashlib
import os
from pathlib import Path
import time

from copilot.mcp_server.contracts import TOOL_SCOPES
from copilot.mcp_server.server import create_server
from copilot.mcp_server.service import ToolService
from copilot.mcp_server.token_registry import TokenRegistry

TOKENS={"valid":"http-test-valid-"+"a"*40,"limited":"http-test-limited-"+"b"*40,
    "expired":"http-test-expired-"+"c"*40,"wrong_resource":"http-test-audience-"+"d"*40}


def serve(port):
    resource=f"http://127.0.0.1:{port}/mcp"
    records=[]
    for kind,token in TOKENS.items():
        records.append({"sha256":hashlib.sha256(token.encode()).hexdigest(),"client_id":"http-test",
            "resource":resource if kind!="wrong_resource" else "http://wrong.example/mcp",
            "scopes":list(TOOL_SCOPES.values()) if kind!="limited" else ["events:read"],
            "expires_at":int(time.time())+(3600 if kind!="expired" else -3600)})
    class Reader:
        def get_case(self,case_id):return {"case_id":case_id,"source":"transaction-api","summary":"Fixture"}
        def list_case_events(self,case_id):return []
        def search_similar_cases(self,case_id,k):return {"case_id":case_id,"neighbors":[]}
    class Gateway:
        def score(self,features):return {"fraud_score":.5,"flagged":False,"threshold":.99,"alert_published":False}
    class Explainer:
        def explain(self,case):return {"case_id":case["case_id"],"units":"log_odds"}
    reader,explainer=Reader(),Explainer()
    if os.environ.get("COPILOT_HTTP_USE_DATABASE")=="1":
        from copilot.db.reader import PostgresCaseReader
        from copilot.mcp_server.explanation import ExactExplainer
        from copilot.retrieval.embedding import MODEL,REVISION
        root=Path(os.environ.get("COPILOT_REPO_ROOT",Path(__file__).resolve().parents[3]))
        reader=PostgresCaseReader(os.environ["COPILOT_READER_DSN"],embedding_model=MODEL,embedding_revision=REVISION)
        explainer=ExactExplainer(root/"streaming/model/lr_model.json",root/"serving/model/model.joblib",
            expected_export_sha256="12c063adddb9d2e2a0ecd1e9190a45281411f9b83cc7de279d48c54be06cd4a1")
    server=create_server(ToolService(reader,Gateway(),explainer),TokenRegistry(records,resource),resource=resource,issuer="http://127.0.0.1:1")
    server.run(transport="streamable-http",host="127.0.0.1",port=port,stateless_http=True,json_response=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--port",type=int,required=True);serve(parser.parse_args().port)
