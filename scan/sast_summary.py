"""Summarize bandit + semgrep JSON for app/vulnerable.py vs app/fixed.py -> results/sast_summary.md"""
import json
import sys
from collections import Counter
from pathlib import Path

R = Path(sys.argv[1] if len(sys.argv) > 1 else "results")
rows, detail = [], []
for tgt in ("vulnerable", "fixed"):
    b = json.loads((R / f"bandit_{tgt}.json").read_text())["results"]
    s = json.loads((R / f"semgrep_{tgt}.json").read_text())["results"]
    bsev = Counter(r["issue_severity"] for r in b)
    ssev = Counter(r["extra"]["severity"] for r in s)
    rows.append(f"| {tgt} | {len(b)} | {bsev.get('HIGH', 0)}/{bsev.get('MEDIUM', 0)}/{bsev.get('LOW', 0)} | "
                f"{len(s)} | {ssev.get('ERROR', 0)}/{ssev.get('WARNING', 0)}/{ssev.get('INFO', 0)} |")
    detail.append(f"\n### {tgt}\n")
    detail += sorted({f"- bandit {r['test_id']} {r['test_name']} ({r['issue_severity']}) line {r['line_number']}" for r in b})
    detail += sorted({f"- semgrep {r['check_id'].split('.')[-1]} ({r['extra']['severity']}) line {r['start']['line']}" for r in s})
md = ["| target | bandit findings | bandit H/M/L | semgrep findings | semgrep ERROR/WARNING/INFO |", "|---|---|---|---|---|"] + rows
(R / "sast_summary.md").write_text("\n".join(md + detail) + "\n")
print("\n".join(md))
