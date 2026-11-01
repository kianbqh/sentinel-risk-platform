"""DELIBERATELY VULNERABLE demo service ("notes"). For local security-tool testing only; never deploy.

Each endpoint carries one classic bug; app/fixed.py is the same service with the bugs fixed.
Bugs: hardcoded secret + debug mode, MD5 password hashes, SQL injection, reflected XSS, server-side template
injection, OS command injection, path traversal, insecure deserialization (pickle cookie), open redirect,
missing security headers / insecure cookie flags, no login throttling, verbose errors."""
import base64
import hashlib
import os
import pickle
import sqlite3
import subprocess

from flask import Flask, g, redirect, render_template_string, request, send_file

FILES_DIR = os.path.join(os.path.dirname(__file__), "files")


def create_app(db_path=":memory:"):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "dev-secret-123"            # B105 hardcoded secret
    app.config["DB"] = db_path

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

    @app.route("/")
    def index():
        return "<html><head><title>notes</title></head><body><a href='/search?q=x'>search</a> <a href='/greet?name=guest'>greet</a> " \
               "<a href='/files?name=readme.txt'>readme</a> <a href='/admin/disk?path=.'>disk</a> " \
               "<form method='post' action='/login'><input name='username'><input name='password' type='password'>" \
               "<button>login</button></form></body></html>"

    @app.route("/login", methods=["POST"])
    def login():
        u, p = request.form.get("username", ""), request.form.get("password", "")
        h = hashlib.md5(p.encode()).hexdigest()               # weak hash
        row = db().execute(f"SELECT id FROM users WHERE name='{u}' AND pw='{h}'").fetchone()   # SQL injection
        if not row:
            return "bad credentials", 401
        resp = redirect(request.args.get("next", "/"))         # open redirect
        sess = base64.b64encode(pickle.dumps({"uid": row[0], "name": u})).decode()
        resp.set_cookie("session_data", sess)                  # no HttpOnly / Secure / SameSite
        return resp

    @app.route("/me")
    def me():
        raw = request.cookies.get("session_data", "")
        data = pickle.loads(base64.b64decode(raw))             # insecure deserialization -> RCE
        return f"hello {data['name']}"

    @app.route("/search")
    def search():
        q = request.args.get("q", "")
        rows = db().execute(f"SELECT title FROM notes WHERE title LIKE '%{q}%'").fetchall()   # SQL injection
        return f"<h1>Results for {q}</h1>" + "".join(f"<li>{r[0]}</li>" for r in rows)       # reflected XSS

    @app.route("/greet")
    def greet():
        name = request.args.get("name", "guest")
        return render_template_string("<p>Hello " + name + "</p>")   # SSTI

    @app.route("/admin/disk")
    def disk():
        path = request.args.get("path", ".")
        out = subprocess.check_output(f"du -s {path}", shell=True, text=True)   # command injection
        return out

    @app.route("/files")
    def files():
        name = request.args.get("name", "readme.txt")
        return send_file(os.path.join(FILES_DIR, name))        # path traversal

    return app


def init_db(conn):
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT UNIQUE, pw TEXT);
      CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, owner INTEGER, title TEXT);""")
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        conn.execute("INSERT OR IGNORE INTO users(name, pw) VALUES (?, ?)", ("alice", hashlib.md5(b"wonderland").hexdigest()))
        conn.execute("INSERT OR IGNORE INTO users(name, pw) VALUES (?, ?)", ("bob", hashlib.md5(b"builder").hexdigest()))
        conn.executemany("INSERT INTO notes(owner, title) VALUES (?, ?)",
                         [(1, "alice public note"), (2, "bob secret: launch codes")])
        conn.commit()


def serve(db_path, port):
    create_app(db_path).run(host="127.0.0.1", port=port, debug=True, use_reloader=False)   # debug mode


if __name__ == "__main__":
    create_app(os.environ.get("DB", "/tmp/vuln.db")).run(host="127.0.0.1", port=int(os.environ.get("PORT", 5001)),
                                                          debug=True)   # debug mode: Werkzeug console
