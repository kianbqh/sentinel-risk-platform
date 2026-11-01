#!/usr/bin/env python3
"""Checked benchmark setup and pre/postflight GraphQL probes."""
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

url = os.environ.get("URL", "http://localhost:8080/query")


def request(query, variables=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, json.dumps({"query": query, "variables": variables or {}}).encode(), headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        result = json.load(response)
    if result.get("errors") or result.get("data") is None:
        raise RuntimeError(f"GraphQL request failed: {result}")
    return result["data"]


def verify(token):
    query = json.loads(Path("loadtest/list_query.json").read_text())["query"]
    result = request(query, token=token)
    assert len(result["applications"]) == 20
    assert all(item["events"] for item in result["applications"])
    assert result["stats"]["total"] >= 50


if sys.argv[1] == "health":
    for _ in range(150):
        try:
            with urllib.request.urlopen(url.removesuffix("/query") + "/healthz", timeout=1) as response:
                assert response.status == 200
            break
        except (OSError, urllib.error.URLError):
            time.sleep(0.2)
    else:
        raise TimeoutError("Benchmark API did not become healthy")
elif sys.argv[1] == "seed":
    result = request("mutation($e:String!,$p:String!){register(email:$e,password:$p){token}}",
                     {"e": f"bench-{uuid.uuid4().hex}@example.invalid", "p": "benchmark-password"})
    token = result["register"]["token"]
    for number in range(50):
        request("mutation($in:NewApplication!){createApplication(input:$in){id}}",
                {"in": {"company": f"Company{number}", "role": "Engineer", "status": "APPLIED"}}, token)
    verify(token)
    print(token)
elif sys.argv[1] == "verify":
    verify(sys.argv[2])
elif sys.argv[1] == "create-body":
    path = Path(sys.argv[2])
    path.write_text(json.dumps({"query": 'mutation{createApplication(input:{company:"Bench",role:"Engineer",status:APPLIED}){id}}'}))
    request(json.loads(path.read_text())["query"], token=sys.argv[3])
else:
    raise ValueError("Unknown command")
