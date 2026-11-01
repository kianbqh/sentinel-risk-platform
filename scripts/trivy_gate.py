"""Gate fixable HIGH/CRITICAL service vulnerabilities without dropping other findings."""
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1]
report=json.loads((root/"results/security/trivy.json").read_text())
if not isinstance(report.get("Results"),list):raise RuntimeError("Trivy report has no scan results")
findings=[];blocked=[];secrets=[]
for target in report["Results"]:
    for item in target.get("Vulnerabilities",[]):
        finding={"target":target["Target"],"id":item["VulnerabilityID"],"package":item["PkgName"],"severity":item["Severity"],"installed":item.get("InstalledVersion"),"fixed":item.get("FixedVersion","")}
        findings.append(finding)
        if finding["severity"] in ("HIGH","CRITICAL") and finding["fixed"]:blocked.append(finding)
    secrets.extend({"target":target["Target"],"rule":item["RuleID"],"severity":item["Severity"]} for item in target.get("Secrets",[]))
summary={"scanned_targets":len(report["Results"]),"vulnerabilities":findings,"fixable_high_critical":blocked,"secrets":secrets,"policy_passed":not blocked and not secrets}
(root/"results/security/trivy-summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
if not summary["policy_passed"]:raise SystemExit("Trivy policy failed; complete raw findings retained")
