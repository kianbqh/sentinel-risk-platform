"""Build a compact table source from the measured hey output files."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKLOADS = {
    "compose_single": ("compose_hey_single.txt", 1),
    "compose_batch64": ("compose_hey_batch64.txt", 64),
    "kubernetes_hpa": ("k8s_hey_150s_c50.txt", 1),
    "kubernetes_rolling_restart": ("k8s_hey_rolling_restart.txt", 1),
    "kubernetes_fresh_connections": ("k8s_hey_fresh_connections_150s_c50.txt", 1),
}


def metric(text, pattern):
    match = re.search(pattern, text)
    return float(match.group(1)) if match else None


def summarize(name, filename, batch_size):
    text = (ROOT / "results" / filename).read_text()
    status_section = text.split("Status code distribution:")[-1].split("Error distribution:")[0]
    statuses = {
        int(code): int(count)
        for code, count in re.findall(r"\[(\d{3})\]\s+(\d+)", status_section)
    }
    error_section = text.split("Error distribution:", 1)[1] if "Error distribution:" in text else ""
    transport_errors = sum(int(count) for count in re.findall(r"\[(\d+)\]", error_section))
    requests_per_second = metric(text, r"Requests/sec:\s+([\d.]+)")
    return {
        "source": filename,
        "connection_mode": "fresh" if "fresh" in name else "keepalive",
        "requests_per_second": requests_per_second,
        "transactions_per_request": batch_size,
        "transactions_per_second": requests_per_second * batch_size,
        "duration_seconds": metric(text, r"Total:\s+([\d.]+)"),
        "p50_ms": round(metric(text, r"50% in ([\d.]+)") * 1000, 4),
        "p95_ms": round(metric(text, r"95% in ([\d.]+)") * 1000, 4),
        "p99_ms": round(metric(text, r"99% in ([\d.]+)") * 1000, 4),
        "http_status_counts": statuses,
        "transport_errors": transport_errors,
        "requests": sum(statuses.values()) + transport_errors,
        "errors": sum(count for code, count in statuses.items() if code != 200) + transport_errors,
    }


if __name__ == "__main__":
    summary = {
        name: summarize(name, filename, batch_size)
        for name, (filename, batch_size) in WORKLOADS.items()
    }
    output = json.dumps(summary, indent=2)
    (ROOT / "results" / "benchmark-summary.json").write_text(output + "\n")
    print(output)