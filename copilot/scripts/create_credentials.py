"""Create task-local service credentials without printing secret material."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import time

from copilot.mcp_server.contracts import TOOL_SCOPES


def create(output, resource, hours=8):
    target=Path(output)
    target.mkdir(parents=True,exist_ok=False)
    token=secrets.token_urlsafe(48)
    registry={"tokens":[{"sha256":hashlib.sha256(token.encode()).hexdigest(),"client_id":"sentinel-copilot-agent",
        "scopes":list(TOOL_SCOPES.values()),"resource":resource,"expires_at":int(time.time()+hours*3600)}]}
    files={"token":token,"registry.json":json.dumps(registry,indent=2)+"\n",
        "postgres-password":secrets.token_urlsafe(32),"reader-password":secrets.token_urlsafe(32),
        "writer-password":secrets.token_urlsafe(32),"indexer-password":secrets.token_urlsafe(32),
        "jwt-secret":secrets.token_urlsafe(48)}
    for name,value in files.items():
        path=target/name
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as handle:
            handle.write(value)
    print(json.dumps({"credentials_directory":str(target.resolve()),"resource":resource,
        "created_at":datetime.now(timezone.utc).isoformat(),"secret_values_printed":False}))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--output",required=True)
    parser.add_argument("--resource",default="http://127.0.0.1:18070/mcp")
    parser.add_argument("--hours",type=int,default=8)
    args=parser.parse_args()
    if not 1<=args.hours<=24: parser.error("hours must be 1..24")
    create(args.output,args.resource,args.hours)
