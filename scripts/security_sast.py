"""Run production-source checks and keep findings separate from scanner errors."""
import json
import os
from pathlib import Path
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]


def run():
    out=ROOT/"results/security";out.mkdir(parents=True,exist_ok=True)
    binaries=Path(os.environ.get("SECURITY_BIN",Path(os.sys.executable).parent))
    targets=["gateway","ato","text-risk/riskclf","console/backend","console/frontend/src","streaming/src/main"]
    commands={
        "bandit":[str(binaries/"bandit"),"-r","gateway","ato","text-risk/riskclf","-x","gateway/tests","-f","json","-o",str(out/"bandit.json")],
        "semgrep":[str(binaries/"semgrep"),"scan","--metrics=off","--config","p/ci","--config","p/secrets","--config","security/semgrep.yml","--exclude","*_test.go","--exclude","*.test.tsx","--exclude","generated.go","--exclude","models_gen.go","--exclude","gateway/tests","--json","--output",str(out/"semgrep.json"),*targets],
    }
    manifest=[];violations=[]
    for name,command in commands.items():
        record={"tool":name,"targets":targets if name=="semgrep" else targets[:3]};manifest.append(record)
        started=time.monotonic()
        try:
            (out/(name+".json")).unlink(missing_ok=True)
            with (out/(name+".log")).open("w") as log: result=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            record.update(returncode=result.returncode,seconds=time.monotonic()-started)
            if result.returncode not in (0,1): raise RuntimeError(f"{name} scanner failed: {result.returncode}")
            report=json.loads((out/(name+".json")).read_text())
            if not isinstance(report.get("results"),list) or report.get("errors"):raise RuntimeError(f"{name} report has infrastructure/parser errors")
            findings=report["results"];record["findings"]=len(findings)
            if name=="bandit": blocked=[f for f in findings if f["issue_severity"]=="HIGH" and f["issue_confidence"] in ("MEDIUM","HIGH")]
            else:
                if not report.get("paths",{}).get("scanned"):raise RuntimeError("Semgrep scanned no files")
                blocked=[f for f in findings if f["extra"]["severity"]=="ERROR"]
            record["policy_failures"]=len(blocked);violations.extend(blocked)
        finally:(out/"sast-manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print(json.dumps(manifest,indent=2))
    if violations:raise SystemExit("Production SAST policy failed; see complete raw findings")


if __name__=="__main__":run()
