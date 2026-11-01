"""Each vulnerability is demonstrated against app/vulnerable.py and shown blocked in app/fixed.py."""
import base64
import pickle

import pytest

from security.fixtures.apps import fixed, vulnerable
from ato.online import LoginThrottle


def login(c, user, pw, next_=None):
    """POST /login with the CSRF token from the index page (the vulnerable app ignores it)."""
    import re
    m = re.search(rb"name='csrf_token' value='([^']+)'", c.get("/").data)
    data = {"username": user, "password": pw, "csrf_token": m.group(1).decode() if m else ""}
    return c.post("/login" + (f"?next={next_}" if next_ else ""), data=data)


@pytest.fixture
def vuln(tmp_path):
    return vulnerable.create_app(str(tmp_path / "v.db")).test_client()


@pytest.fixture
def fix(tmp_path, monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "0")
    return fixed.create_app(str(tmp_path / "f.db")).test_client()


def test_honest_login_works_in_both(vuln, fix):
    assert vuln.post("/login", data={"username": "alice", "password": "wonderland"}).status_code == 302
    assert login(fix, "alice", "wonderland").status_code == 302
    assert fix.get("/me").data == b"hello alice"


def test_sqli_auth_bypass(vuln, fix):
    payload = {"username": "bob' --", "password": "anything"}
    assert vuln.post("/login", data=payload).status_code == 302          # logged in as bob without password
    assert login(fix, payload["username"], payload["password"]).status_code == 401


def test_sqli_union_leaks_users(vuln, fix):
    q = "' UNION SELECT name || ':' || pw FROM users --"
    assert b"alice:" in vuln.get("/search", query_string={"q": q}).data
    assert b"alice:" not in fix.get("/search", query_string={"q": q}).data


def test_reflected_xss(vuln, fix):
    q = "<script>alert(1)</script>"
    assert q.encode() in vuln.get("/search", query_string={"q": q}).data
    r = fix.get("/search", query_string={"q": q})
    assert q.encode() not in r.data and b"&lt;script&gt;" in r.data
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]


def test_ssti(vuln, fix):
    assert b"49" in vuln.get("/greet", query_string={"name": "{{7*7}}"}).data
    assert b"{{7*7}}" in fix.get("/greet", query_string={"name": "{{7*7}}"}).data


def test_command_injection(vuln, fix):
    p = ".; echo PWNED_$((6*7))"
    assert b"PWNED_42" in vuln.get("/admin/disk", query_string={"path": p}).data
    r = fix.get("/admin/disk", query_string={"path": p})
    assert r.status_code == 400 and b"PWNED" not in r.data


def test_path_traversal(vuln, fix):
    name = "../vulnerable.py"
    assert b"DELIBERATELY VULNERABLE" in vuln.get("/files", query_string={"name": name}).data
    assert fix.get("/files", query_string={"name": name}).status_code == 404


def test_insecure_deserialization(vuln, fix):
    class Evil:
        def __reduce__(self):
            return (eval, ("{'name': 'pwned-' + str(6*7)}",))
    cookie = base64.b64encode(pickle.dumps(Evil())).decode()
    vuln.set_cookie("session_data", cookie)
    assert vuln.get("/me").data == b"hello pwned-42"                    # attacker-controlled code ran
    fix.set_cookie("session_data", cookie)
    assert fix.get("/me").status_code == 401


def test_open_redirect(vuln, fix):
    cred = {"username": "alice", "password": "wonderland"}
    assert vuln.post("/login?next=https://evil.example", data=cred).headers["Location"] == "https://evil.example"
    for nxt in ("https://evil.example", "//evil.example", "javascript:alert(1)"):
        assert login(fix, "alice", "wonderland", nxt).headers["Location"] == "/"


def test_cookie_and_headers(vuln, fix):
    r = vuln.post("/login", data={"username": "alice", "password": "wonderland"})
    assert "HttpOnly" not in r.headers["Set-Cookie"]
    r = login(fix, "alice", "wonderland")
    assert "HttpOnly" in r.headers["Set-Cookie"] and "SameSite=Lax" in r.headers["Set-Cookie"]
    for h in ("X-Frame-Options", "X-Content-Type-Options", "Content-Security-Policy"):
        assert h in r.headers


def test_csrf_required(fix):
    assert fix.post("/login", data={"username": "alice", "password": "wonderland"}).status_code == 400
    assert login(fix, "alice", "wonderland").status_code == 302


def test_passwords_not_md5(tmp_path):
    import sqlite3
    c = sqlite3.connect(":memory:"); fixed.init_db(c)
    pw = c.execute("SELECT pw FROM users WHERE name='alice'").fetchone()[0]
    assert pw.startswith(("scrypt:", "pbkdf2:")) and len(pw) > 60


def test_brute_force_throttled(tmp_path, monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "0")
    t = [0.0]
    c = fixed.create_app(str(tmp_path / "f.db"), LoginThrottle(clock=lambda: t[0])).test_client()
    codes = [login(c, "alice", f"guess{i}").status_code for i in range(15)]
    assert codes[:10] == [401] * 10 and set(codes[10:]) == {429}
    t[0] += 601                                                          # window expires
    assert login(c, "alice", "wonderland").status_code == 302


def test_stuffing_many_accounts_throttled(tmp_path, monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "0")
    c = fixed.create_app(str(tmp_path / "f.db"), LoginThrottle(clock=lambda: 0.0)).test_client()
    codes = [login(c, f"leak{i}", "x").status_code for i in range(12)]
    assert codes.count(429) == 4                                         # 8 distinct failed accounts per IP, then 429


def test_vulnerable_has_no_throttle(vuln):
    codes = [vuln.post("/login", data={"username": "alice", "password": f"g{i}"}).status_code for i in range(30)]
    assert set(codes) == {401}
