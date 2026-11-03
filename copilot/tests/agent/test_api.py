import json
from pathlib import Path
import tempfile
import unittest

from copilot.agent.api import DraftAPI
from copilot.tests.agent.fakes import draft


class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.token = "synthetic-test-token-"+"a"*32
        token_file = Path(self.tmp.name)/"token"
        token_file.write_text(self.token)
        self.calls = []
        async def runner(case_id, task):
            self.calls.append((case_id, task))
            return {"draft": draft(case_id=case_id), "receipt": {"case_id": case_id, "actor": "copilot"}}
        self.app = DraftAPI({"api_token_file": str(token_file)}, runner=runner)

    async def request(self, *, authorization=None, body=b'{"task":"Review evidence"}', path="/v1/copilot/cases/7/drafts"):
        messages = []
        auth = authorization if authorization is not None else [(b"authorization", ("Bearer "+self.token).encode())]
        scope = {"type": "http", "method": "POST", "path": path, "headers": auth}
        async def receive(): return {"type": "http.request", "body": body, "more_body": False}
        async def send(message): messages.append(message)
        await self.app(scope, receive, send)
        return messages[0]["status"], json.loads(messages[1]["body"])

    async def test_auth_rejects_missing_duplicate_nonascii_and_wrong_tokens(self):
        for headers in ([], [(b"authorization", b"Bearer wrong")], [(b"authorization", b"\xff")],
                        [(b"authorization", ("Bearer "+self.token).encode())]*2):
            status, _ = await self.request(authorization=headers)
            self.assertEqual(status, 401)
        self.assertEqual(self.calls, [])

    async def test_valid_request_returns_only_unapplied_draft_and_receipt(self):
        status, value = await self.request()
        self.assertEqual(status, 201)
        self.assertFalse(value["applied"])
        self.assertEqual(value["receipt"]["actor"], "copilot")
        self.assertEqual(self.calls, [(7, "Review evidence")])

    async def test_mutations_unknown_fields_body_limit_and_case_bounds_refused(self):
        for body in (b'{"task":"Review","status":"RESOLVED"}', b'{"task":"x","task":"y"}', b'{"task":true}'):
            self.assertEqual((await self.request(body=body))[0], 400)
        self.assertEqual((await self.request(body=b"x"*16385))[0], 413)
        self.assertEqual((await self.request(path="/v1/copilot/cases/9223372036854775808/drafts"))[0], 400)
        self.assertEqual(self.calls, [])

    async def test_busy_endpoint_does_not_queue_more_inference(self):
        await self.app.lock.acquire()
        try: self.assertEqual((await self.request())[0], 429)
        finally: self.app.lock.release()
        self.assertEqual(self.calls, [])


if __name__ == "__main__": unittest.main()
