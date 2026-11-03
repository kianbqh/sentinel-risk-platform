"""Create private Compose + host client settings for ONE opt-in case database."""
import argparse
import json
import os
from pathlib import Path


def write(credentials,secrets_bind):
    folder=Path(credentials).resolve()
    password=lambda name:(folder/name).read_text().strip()
    host="127.0.0.1:15449"
    database="sentinel_copilot"
    dsn=lambda user,secret,server:f"postgresql://{user}:{secret}@{server}/{database}?sslmode=disable"
    admin=password("postgres-password")
    reader=password("reader-password")
    values={"COPILOT_SECRETS_DIR":secrets_bind,"COPILOT_JWT_SECRET":password("jwt-secret"),
        "COPILOT_BACKEND_DSN":dsn("copilot_admin",admin,"postgres:5432"),
        "COPILOT_GATEWAY_DSN":dsn("copilot_local_reader",reader,"postgres:5432"),
        "COPILOT_ADMIN_DSN":dsn("copilot_admin",admin,host),
        "COPILOT_READER_DSN":dsn("copilot_local_reader",reader,host),
        "COPILOT_WRITER_DSN":dsn("copilot_local_writer",password("writer-password"),host),
        "COPILOT_INDEXER_DSN":dsn("copilot_local_indexer",password("indexer-password"),host),
        "COPILOT_TOKEN_REGISTRY":str(folder/"registry.json"),"COPILOT_TOKEN_FILE":str(folder/"token")}
    path=folder/"stack.env"
    if any("\n" in value or "'" in value for value in values.values()):raise ValueError("Unsafe environment-file value")
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w") as handle:
        for key,value in values.items():handle.write(f"{key}='{value}'\n")
    print(json.dumps({"private_env_file":str(path),"same_database":database,"secret_values_printed":False}))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--credentials",required=True)
    parser.add_argument("--secrets-bind",required=True)
    args=parser.parse_args();write(args.credentials,args.secrets_bind)
