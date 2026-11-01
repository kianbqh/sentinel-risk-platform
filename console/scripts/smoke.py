#!/usr/bin/env python3
"""Exercise the running UI proxy, authenticated lifecycle, history and filters."""
import argparse
import json
import time
import urllib.error
import urllib.request
import uuid

parser = argparse.ArgumentParser()
parser.add_argument("--ui", default="http://localhost:3000")
parser.add_argument("--api", default="http://localhost:8080")
args = parser.parse_args()
for _ in range(60):
    try:
        with urllib.request.urlopen(args.api + "/healthz", timeout=2) as response:
            assert response.status == 200
        break
    except (OSError, urllib.error.URLError):
        time.sleep(1)
else:
    raise TimeoutError("API did not become healthy")
with urllib.request.urlopen(args.ui, timeout=10) as response:
    assert "<html" in response.read().decode().lower()


def graphql(query, variables=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(args.ui + "/query", json.dumps({"query": query, "variables": variables or {}}).encode(), headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.load(response)
    assert not body.get("errors") and body.get("data") is not None, body
    return body["data"]


email = f"smoke-{uuid.uuid4().hex}@example.invalid"
password = "smoke-password-123"
registration = graphql("mutation($e:String!,$p:String!){register(email:$e,password:$p){token}}", {"e": email, "p": password})
token = registration["register"]["token"]
assert graphql("mutation($e:String!,$p:String!){login(email:$e,password:$p){token}}", {"e": email, "p": password})["login"]["token"]
application = graphql("mutation{createApplication(input:{company:\"Smoke Company\",role:\"Engineer\",status:APPLIED}){id status events{status}}}", token=token)["createApplication"]
assert application["status"] == "APPLIED" and len(application["events"]) == 1
updated = graphql("mutation($id:ID!){updateStatus(id:$id,status:INTERVIEW){status events{status at}}}", {"id": application["id"]}, token)["updateStatus"]
assert updated["status"] == "INTERVIEW" and [item["status"] for item in updated["events"]] == ["APPLIED", "INTERVIEW"]
listing = graphql('{applications(status:INTERVIEW,search:"Smoke"){id company} stats{total}}', token=token)
assert len(listing["applications"]) == 1 and listing["stats"]["total"] == 1
assert graphql("mutation($id:ID!){deleteApplication(id:$id)}", {"id": application["id"]}, token)["deleteApplication"] is True
assert graphql("{applications{id} stats{total}}", token=token)["stats"]["total"] == 0
print(json.dumps({"ui": args.ui, "api": args.api, "checks": ["ui_html", "health", "register", "login", "create", "status_update", "status_history", "search_filter", "stats", "delete"], "result": "passed"}, indent=2))
