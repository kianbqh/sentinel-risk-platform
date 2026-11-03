"""Actual localhost TCP/HTTP transport test, opt-in to respect resource windows."""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

from copilot.mcp_server.client import MCPToolClient
from copilot.mcp_server.contracts import FEATURE_NAMES,TOOL_SCOPES
from copilot.tests.tools.http_fixture_server import TOKENS


@unittest.skipUnless(os.environ.get("RUN_COPILOT_HTTP")=="1","set RUN_COPILOT_HTTP=1 in isolated runtime to run real Streamable HTTP tests")
class RealHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sock=socket.socket();sock.bind(("127.0.0.1",0));port=sock.getsockname()[1];sock.close()
        cls.endpoint=f"http://127.0.0.1:{port}/mcp"
        cls.proc=subprocess.Popen([sys.executable,"-m","copilot.tests.tools.http_fixture_server","--port",str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            if cls.proc.poll() is not None:raise RuntimeError("HTTP fixture server exited before readiness")
            try:
                with socket.create_connection(("127.0.0.1",port),timeout=.2):return
            except OSError:time.sleep(.05)
        cls.proc.terminate();cls.proc.wait(timeout=10)
        raise RuntimeError("HTTP fixture server did not become ready")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:cls.proc.kill();cls.proc.wait(timeout=5)

    def test_missing_invalid_expired_and_audience_rejected_over_http(self):
        for token in (None,"not-a-token",TOKENS["expired"],TOKENS["wrong_resource"]):
            headers={"Content-Type":"application/json","Accept":"application/json, text/event-stream"}
            if token:headers["Authorization"]="Bearer "+token
            request=urllib.request.Request(self.endpoint,data=json.dumps({"jsonrpc":"2.0","id":1,"method":"tools/list"}).encode(),headers=headers)
            with self.subTest(token_kind="missing" if token is None else "provided"),self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request,timeout=5)
            self.assertEqual(error.exception.code,401)

    def test_authorized_tool_and_per_tool_scope_over_http(self):
        async def check():
            async with MCPToolClient(self.endpoint,TOKENS["valid"]) as client:
                result=await client.call("get_case",{"case_id":1})
                self.assertEqual(set(result),{"ok","data"})
                self.assertTrue(result["ok"])
                self.assertEqual(result["data"]["case_id"],1)
                result=await client.call("score_transaction",{"features":dict.fromkeys(FEATURE_NAMES,0.)})
                self.assertIs(result["data"]["alert_published"],False)
            async with MCPToolClient(self.endpoint,TOKENS["limited"]) as client:
                result=await client.call("get_case",{"case_id":1})
                self.assertEqual(result["error"]["code"],"forbidden")
                self.assertEqual(set(result),{"ok","error"})
                self.assertTrue((await client.call("list_case_events",{"case_id":1}))["ok"])
        asyncio.run(check())

    def test_server_allowlist_and_raw_extra_arguments(self):
        async def check():
            from mcp import MCPError
            async with MCPToolClient(self.endpoint,TOKENS["valid"]) as client:
                listed=await client._client.list_tools()
                self.assertEqual({tool.name for tool in listed.tools},set(TOOL_SCOPES))
                for tool in listed.tools:self.assertFalse(tool.input_schema["additionalProperties"])
                for tool in listed.tools:
                    self.assertIsNotNone(tool.output_schema)
                    self.assertIn("oneOf",tool.output_schema)
                for name,args in (("label_case",{"case_id":1}),("get_case",{"case_id":1,"publish":True}),
                    ("get_case",{"case_id":True}),("score_transaction",{"features":{},"publish":True})):
                    try:
                        result=await client._client.call_tool(name,args)
                    except MCPError as exc:
                        self.assertIn(exc.code,(-32601,-32602))
                        continue
                    self.assertTrue(result.is_error,"server accepted a forbidden/raw invalid call")
        asyncio.run(check())

    @unittest.skipUnless(os.environ.get("COPILOT_HTTP_USE_DATABASE")=="1","real HTTP database adapter is not enabled")
    def test_mcp_reads_and_rejected_writes_leave_database_unchanged(self):
        import psycopg
        def snapshot():
            with psycopg.connect(os.environ["COPILOT_ADMIN_DSN"]) as conn:
                return [conn.execute(query).fetchall() for query in (
                    "SELECT id,status,assignee_id,updated_at FROM cases ORDER BY id",
                    "SELECT id,case_id,action,note,at FROM case_events ORDER BY id",
                    "SELECT case_id,actor_id,value,note,at FROM case_labels ORDER BY case_id,actor_id",
                    "SELECT id,evidence FROM alerts ORDER BY id")]
        before=snapshot()
        async def check():
            from mcp import MCPError
            async with MCPToolClient(self.endpoint,TOKENS["valid"]) as client:
                self.assertTrue((await client.call("get_case",{"case_id":1}))["ok"])
                self.assertTrue((await client.call("list_case_events",{"case_id":1}))["ok"])
                await client.call("explain_score",{"case_id":1})
                await client.call("search_similar_cases",{"case_id":1,"k":5})
                for name in ("label_case","resolve_case","assign_case","save_turn","sql"):
                    try:result=await client._client.call_tool(name,{"case_id":1})
                    except MCPError as exc:
                        self.assertIn(exc.code,(-32601,-32602))
                        continue
                    self.assertTrue(result.is_error)
        asyncio.run(check())
        self.assertEqual(snapshot(),before)
