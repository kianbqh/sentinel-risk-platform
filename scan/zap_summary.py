"""ZAP JSON reports -> results/dast_summary.md (alert types by risk, vulnerable vs fixed)."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("reports", nargs="?", default="results/zap")
parser.add_argument("--output")
args = parser.parse_args()
Z = Path(args.reports)
RISK = {"3": "High", "2": "Medium", "1": "Low", "0": "Info"}
md = ["| scan | target | alert types (High/Medium/Low/Info) | total alert types | High+Medium alerts |", "|---|---|---|---|---|"]
detail = []
for scan in ("baseline", "full"):
    for tgt in ("vulnerable", "fixed"):
        f = Z / f"{scan}_{tgt}.json"
        if not f.exists():
            continue
        alerts = [a for s in json.loads(f.read_text())["site"] for a in s["alerts"]]
        c = {r: 0 for r in RISK.values()}
        for a in alerts:
            c[RISK[a["riskcode"]]] += 1
        md.append(f"| {scan} | {tgt} | {c['High']}/{c['Medium']}/{c['Low']}/{c['Info']} | {len(alerts)} | {c['High'] + c['Medium']} |")
        detail.append(f"\n### {scan} / {tgt}\n")
        detail += [f"- [{RISK[a['riskcode']]}] {a['name']} ({a['count']} instances)"
                   for a in sorted(alerts, key=lambda a: -int(a["riskcode"]))]
Path(args.output or Z.parent / "dast_summary.md").write_text("\n".join(md + detail) + "\n")
print("\n".join(md))
