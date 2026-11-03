"""Record explicit native/HTTP/PG test selection with provenance and safe logs."""
import argparse
import os
import re
import subprocess
import sys
from urllib.parse import unquote,urlsplit

from copilot.scripts.provenance import RunRecord


def scrub(value):
    for key in ("COPILOT_ADMIN_DSN","COPILOT_READER_DSN","COPILOT_WRITER_DSN","COPILOT_INDEXER_DSN"):
        secret=os.environ.get(key)
        if secret:
            value=value.replace(secret,"[REDACTED_DSN]")
            password=urlsplit(secret).password if "://" in secret else None
            if password:value=value.replace(password,"[REDACTED]").replace(unquote(password),"[REDACTED]")
    return value


def check(config_path):
    run=RunRecord(config_path)
    config=run.config
    env=os.environ.copy()
    env["RUN_COPILOT_HTTP"]="1" if config["real_http"] else "0"
    env["RUN_COPILOT_DB"]="1" if config["real_database"] else "0"
    env["COPILOT_HTTP_USE_DATABASE"]="1" if config["real_http"] and config["real_database"] else "0"
    env["PYTHONDONTWRITEBYTECODE"]="1"
    command=[sys.executable,"-m","unittest","discover","-s","copilot/tests/tools","-v"]
    process=subprocess.run(command,cwd=run.root,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=config["timeout_seconds"])
    log=scrub(process.stdout)
    with (run.output/"unittest.log").open("x") as handle:handle.write(log)
    count=re.search(r"Ran (\d+) tests?",log)
    skips=re.search(r"skipped=(\d+)",log)
    skipped=int(skips.group(1)) if skips else 0
    passed=process.returncode==0 and not (config["real_http"] and config["real_database"] and skipped)
    run.finish({"status":"passed" if passed else "failed","test_command":command,"exit_code":process.returncode,
        "tests_run":int(count.group(1)) if count else None,"skipped":skipped,
        "real_http_enabled":config["real_http"],"real_database_enabled":config["real_database"]})
    if not passed:raise SystemExit(1)


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--config",required=True);check(parser.parse_args().config)
