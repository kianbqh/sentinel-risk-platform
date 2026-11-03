"""Provision distinct local logins; grants are controlled by the opt-in migration."""
import argparse
import os
from pathlib import Path


def provision(dsn, credential_dir):
    import psycopg
    from psycopg import sql
    with psycopg.connect(dsn) as conn:
        for kind in ("reader","writer","indexer"):
            role="copilot_local_"+kind
            password=(Path(credential_dir)/(kind+"-password")).read_text().strip()
            if conn.execute("SELECT EXISTS(SELECT FROM pg_roles WHERE rolname=%s)",(role,)).fetchone()[0]:
                raise ValueError("Local service login already exists; preserve credentials or rotate explicitly")
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION").format(
                sql.Identifier(role),sql.Literal(password)))
            conn.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier("sentinel_copilot_"+kind),sql.Identifier(role)))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--dsn-env",default="COPILOT_ADMIN_DSN")
    parser.add_argument("--credentials",required=True)
    args=parser.parse_args();provision(os.environ[args.dsn_env],args.credentials)
