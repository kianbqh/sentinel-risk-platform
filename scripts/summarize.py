"""Collect results/<run>.json + Spark progress logs into results/summary.md."""
import json
import statistics
from pathlib import Path

R = Path(__file__).resolve().parents[1] / "results"
rows = ["| run | input | events | e2e p50 ms | e2e p95 ms | e2e p99 ms | Spark rows/s (sum of batch time) | median batch ms | parity (max abs Δp, alert mismatches) |",
        "|---|---|---|---|---|---|---|---|---|"]
for run in ["live1k", "live5k", "live10k", "backlog20k", "backlog"]:
    f = R / f"{run}.json"
    if not f.exists():
        continue
    r = json.loads(f.read_text())
    prog = [json.loads(l) for l in (R / f"{run}_progress.jsonl").read_text().splitlines()]
    prog = [p for p in prog if p["numInputRows"] > 0]
    rate = sum(p["numInputRows"] for p in prog) / (sum(p["batchDuration"] for p in prog) / 1000)
    inp = f"{r['target_rate']} ev/s live" if r["mode"] == "live" else "backlog drain" + (" (20k/batch)" if "20k" in run else " (1 batch)")
    latency = [f"{r[f'e2e_latency_ms_p{p}']:,}" for p in (50, 95, 99)] if r["mode"] == "live" else ["—"] * 3
    rows.append(f"| {run} | {inp} | {r['distinct_txn_ids']:,} | {' | '.join(latency)} | "
                f"{rate:,.0f} | {statistics.median(p['batchDuration'] for p in prog):,.0f} | "
                f"{r['max_abs_proba_diff']}, {r['alert_decision_mismatches']} |")
rows += ["", "Spark input rows/s = consumed rows / summed durations of nonempty micro-batches. Each run consumes 283,726 transactions plus one flush marker.",
         "Live latency = producer event-creation timestamp to score-topic Kafka record timestamp (default CreateTime). Backlog runs measure drain throughput; their raw latency fields include preloaded queueing."]
if (R / "attempts.json").exists():
    rows += ["", "Selected attempts are recorded in attempts.json. backlog20k was rerun once after synchronizing final progress recording; its original scores were complete but its duration log lacked the last batch. The other four runs are unchanged."]
(R / "summary.md").write_text("\n".join(rows) + "\n")
print("\n".join(rows))
