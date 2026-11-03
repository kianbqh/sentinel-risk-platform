"""Apply only new copilot SQL, optionally bootstrap an isolated empty demo DB."""
import argparse
import hashlib
import os
from pathlib import Path


def migrate(dsn, repo_root, bootstrap=False):
    import psycopg
    root = Path(repo_root)
    sql_path = Path(__file__).parent / "migrations/001_copilot.sql"
    with psycopg.connect(dsn) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(716203115)")
        conn.execute("CREATE TABLE IF NOT EXISTS public.copilot_schema_version(version integer PRIMARY KEY, sha256 text NOT NULL)")
        digest = hashlib.sha256(sql_path.read_bytes()).hexdigest()
        prior = conn.execute("SELECT sha256 FROM public.copilot_schema_version WHERE version=1").fetchone()
        if prior:
            if prior[0] != digest:
                raise ValueError("Applied copilot migration hash changed")
        exists = conn.execute("SELECT to_regclass('public.cases') IS NOT NULL").fetchone()[0]
        if not exists:
            if prior:
                raise ValueError("Copilot ledger exists but original base schema is missing")
            if not bootstrap:
                raise ValueError("Base console schema missing; explicit --bootstrap-demo required")
            conn.execute((root / "console/backend/migrations/000001_sentinel.up.sql").read_text())
            # Mirror golang-migrate's exact version table only after the original
            # SQL succeeds atomically. The unchanged Go backend then sees v1 clean
            # and returns ErrNoChange instead of rerunning CREATE TABLE/TYPE.
            conn.execute("CREATE TABLE public.schema_migrations(version bigint NOT NULL PRIMARY KEY, dirty boolean NOT NULL)")
            conn.execute("INSERT INTO public.schema_migrations(version,dirty) VALUES(1,false)")
        else:
            version_table=conn.execute("SELECT to_regclass('public.schema_migrations') IS NOT NULL").fetchone()[0]
            if not version_table:
                raise ValueError("Existing base tables have no Go migration ledger; refuse unsafe bootstrap")
            versions=conn.execute("SELECT version,dirty FROM public.schema_migrations").fetchall()
            if versions != [(1,False)]:
                raise ValueError("Expected original Go schema at clean version 1")
        if prior:
            return
        conn.execute(sql_path.read_text())
        conn.execute("INSERT INTO public.copilot_schema_version VALUES(1,%s)", (digest,))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn-env", default="COPILOT_ADMIN_DSN")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--bootstrap-demo", action="store_true")
    args = parser.parse_args()
    migrate(os.environ[args.dsn_env], args.repo_root, args.bootstrap_demo)
