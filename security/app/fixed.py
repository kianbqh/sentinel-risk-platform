"""The notes service with every bug from app/vulnerable.py fixed (same routes, same behavior for honest users)."""
import os
import re
import secrets
import sqlite3
import subprocess  # nosec B404
from urllib.parse import urlparse

from flask import Flask, abort, g, redirect, render_template_string, request, send_from_directory, session
from markupsafe import escape
from werkzeug.security import check_password_hash, generate_password_hash

from detector.online import LoginThrottle

FILES_DIR = os.path.join(os.path.dirname(__file__), "files")
DISK_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "files"))
SAFE_REL = re.compile(r"^[A-Za-z0-9_./-]{1,128}$")


def create_app(db_path=":memory:", throttle: LoginThrottle | None = None):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("APP_SECRET_KEY") or secrets.token_hex(32)   # from env, never in code
    app.config.update(DB=db_path, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") == "1",
                      MAX_CONTENT_LENGTH=64 * 1024)
    thr = throttle or LoginThrottle()

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DB"])
        return g.db

    with sqlite3.connect(db_path) as c0:          # seed once, before serving (no first-request race)
        init_db(c0)
    if db_path == ":memory:":
        raise ValueError("use a file path: each request opens its own connection")

    @app.teardown_appcontext
    def close(_):
        d = g.pop("db", None)
        if d is not None:
            d.close()

    @app.after_request
    def headers(resp):
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self'; "
                                                   "img-src 'self'; form-action 'self'; frame-ancestors 'none'; "
                                                   "base-uri 'self'; object-src 'none'")
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        resp.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Server"] = "notes"
        return resp

    @app.route("/")
    def index():
        tok = session.setdefault("csrf", secrets.token_urlsafe(32))
        return render_template_string(INDEX, csrf=tok)

    INDEX = ("<html><head><title>notes</title></head><body><a href='/search?q=x'>search</a> <a href='/greet?name=guest'>greet</a> " \
               "<a href='/files?name=readme.txt'>readme</a> <a href='/admin/disk?path=.'>disk</a> " \
               "<form method='post' action='/login'><input type='hidden' name='csrf_token' value='{{ csrf }}'>" \
               "<input name='username'><input name='password' type='password'>" \
               "<button>login</button></form></body></html>")

    @app.route("/robots.txt")
    def robots():
        return "User-agent: *\nDisallow: /admin/\n", 200, {"Content-Type": "text/plain"}

    @app.route("/login", methods=["POST"])
    def login():
        tok = session.get("csrf")
        if not tok or not secrets.compare_digest(tok, request.form.get("csrf_token", "")):
            abort(400)                                              # CSRF token missing / wrong
        u, p = request.form.get("username", ""), request.form.get("password", "")
        ip = request.remote_addr or "?"
        if thr.blocked(ip, u):
            return "too many attempts", 429
        row = db().execute("SELECT id, pw FROM users WHERE name = ?", (u,)).fetchone()   # parameterized
        ok = bool(row) and check_password_hash(row[1], p)                                  # salted PBKDF2/scrypt
        thr.record(ip, u, ok)
        if not ok:
            return "bad credentials", 401
        session.clear()                                             # new session on login (fixation)
        session["uid"], session["name"], session["csrf"] = row[0], u, secrets.token_urlsafe(32)                 # signed cookie, JSON-serialized
        nxt = request.args.get("next", "/")
        if urlparse(nxt).netloc or not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/"                                               # only same-site relative redirects
        return redirect(nxt)

    @app.route("/me")
    def me():
        if "name" not in session:
            abort(401)
        return f"hello {escape(session['name'])}"

    @app.route("/search")
    def search():
        q = request.args.get("q", "")
        rows = db().execute("SELECT title FROM notes WHERE title LIKE ?", (f"%{q}%",)).fetchall()
        return render_template_string("<h1>Results for {{ q }}</h1>{% for r in rows %}<li>{{ r[0] }}</li>{% endfor %}",
                                      q=q, rows=rows)            # Jinja autoescaping

    @app.route("/greet")
    def greet():
        return render_template_string("<p>Hello {{ name }}</p>", name=request.args.get("name", "guest"))

    @app.route("/admin/disk")
    def disk():
        rel = request.args.get("path", ".")
        full = os.path.realpath(os.path.join(DISK_ROOT, rel))
        if not SAFE_REL.match(rel) or os.path.commonpath([full, DISK_ROOT]) != DISK_ROOT:
            abort(400)
        # fixed argv (no shell), path validated against DISK_ROOT above
        return subprocess.run(["du", "-s", full], capture_output=True, text=True, check=False).stdout  # nosec B603 B607

    @app.route("/files")
    def files():
        return send_from_directory(FILES_DIR, request.args.get("name", "readme.txt"))   # safe_join inside

    return app


def init_db(conn):
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT UNIQUE, pw TEXT);
      CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, owner INTEGER, title TEXT);""")
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        conn.execute("INSERT OR IGNORE INTO users(name, pw) VALUES (?, ?)", ("alice", generate_password_hash("wonderland")))
        conn.execute("INSERT OR IGNORE INTO users(name, pw) VALUES (?, ?)", ("bob", generate_password_hash("builder")))
        conn.executemany("INSERT INTO notes(owner, title) VALUES (?, ?)",
                         [(1, "alice public note"), (2, "bob secret: launch codes")])
        conn.commit()


def serve(db_path, port):
    from werkzeug.serving import WSGIRequestHandler
    WSGIRequestHandler.server_version, WSGIRequestHandler.sys_version = "notes", ""   # no version banner
    create_app(db_path).run(host="127.0.0.1", port=port)


if __name__ == "__main__":
    serve(os.environ.get("DB", os.path.join(os.path.dirname(__file__), "fixed.db")), int(os.environ.get("PORT", 5002)))
