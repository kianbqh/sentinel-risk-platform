"""Drive the real HTTP service (uvicorn subprocess) and record client-side latency per phase.

Phases (sequential, 1 client):
  miss     - N distinct test messages, first time seen  (LLM call per request in llm mode)
  hit      - the same N messages again                  (served from cache)
  timeout  - service restarted with a tiny LLM_TIMEOUT_S, N distinct messages -> rule fallback
  hybrid   - CLASSIFIER_MODE=hybrid on N distinct messages (LLM only for uncertain TF-IDF scores)
Writes results/loadtest.json and results/loadtest_metrics.txt (a /metrics scrape per phase).
"""
import argparse, hashlib, json, os, socket, statistics, subprocess, sys, time
from pathlib import Path

import httpx
import numpy as np
from prometheus_client.parser import text_string_to_metric_families

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from riskclf.data import split  # noqa: E402


def pct(v, q):
    return round(float(np.percentile(v, q * 100)), 4)


def serve(env, port):
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "riskclf.service:app", "--port", str(port), "--log-level", "warning"],
                         cwd=ROOT, env={**os.environ, **env})
    for _ in range(100):
        if p.poll() is not None:
            raise RuntimeError(f"service exited with {p.returncode}")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200: return p
        except httpx.HTTPError: pass
        time.sleep(0.2)
    p.kill(); p.wait(); raise RuntimeError("service did not start")


def counter_delta(before, after, metric, label=None):
    def values(text):
        totals = {}
        for family in text_string_to_metric_families(text):
            for sample in family.samples:
                if sample.name == metric:
                    key = sample.labels[label] if label else "total"
                    totals[key] = totals.get(key, 0) + sample.value
        return totals
    first, last = values(before), values(after)
    return {key: value - first.get(key, 0) for key, value in last.items() if value != first.get(key, 0)}


def run(port, texts, expected_sources=None):
    lat, src = [], {}
    with httpx.Client(timeout=60) as c:
        before = c.get(f"http://127.0.0.1:{port}/metrics"); before.raise_for_status()
        for t in texts:
            s = time.perf_counter(); r = c.post(f"http://127.0.0.1:{port}/v1/classify", json={"text": t}); r.raise_for_status()
            lat.append((time.perf_counter() - s) * 1000); k = r.json()["source"]; src[k] = src.get(k, 0) + 1
        scrape = c.get(f"http://127.0.0.1:{port}/metrics"); scrape.raise_for_status(); m = scrape.text
    delta = {
        "requests_by_source": counter_delta(before.text, m, "riskclf_requests_total", "source"),
        "latency_observations_by_source": counter_delta(before.text, m, "riskclf_request_seconds_count", "source"),
        "cache_by_result": counter_delta(before.text, m, "riskclf_cache_total", "result"),
        "llm_errors_by_kind": counter_delta(before.text, m, "riskclf_llm_errors_total", "kind"),
        "reported_tokens": counter_delta(before.text, m, "riskclf_llm_tokens_total", "type"),
        "successful_llm_calls": counter_delta(before.text, m, "riskclf_llm_call_seconds_count").get("total", 0),
    }
    assert delta["requests_by_source"] == src == delta["latency_observations_by_source"]
    assert sum(src.values()) == len(texts)
    assert delta["successful_llm_calls"] == src.get("llm", 0)
    assert sum(delta["llm_errors_by_kind"].values()) == src.get("rules", 0)
    assert delta["cache_by_result"].get("hit", 0) == src.get("cache", 0)
    assert delta["cache_by_result"].get("miss", 0) == src.get("llm", 0) + src.get("rules", 0)
    if expected_sources is not None:
        assert src == expected_sources, (src, expected_sources)
    return {"n": len(texts), "p50_ms": pct(lat, .5), "p95_ms": pct(lat, .95), "p99_ms": pct(lat, .99),
            "mean_ms": round(statistics.mean(lat), 4), "elapsed_seconds_per_1k": round(statistics.mean(lat), 4),
            "sources": src, "prometheus_delta": delta, "metrics_reconciled": True}, before.text, m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm-url", required=True); ap.add_argument("--model", required=True)
    ap.add_argument("--n", type=int, default=100); ap.add_argument("--port", type=int, default=8008)
    ap.add_argument("--timeout-s", type=float, default=0.005)
    a = ap.parse_args()
    _, te = split(); texts = list(dict.fromkeys(" ".join(text.split()) for text in te.text))
    if a.n <= 0 or len(texts) < 3 * a.n:
        ap.error("need enough distinct normalized test messages for all three sets")
    base = {"LLM_BASE_URL": a.llm_url, "LLM_MODEL": a.model}
    out, scrapes = {"llm_model": a.model, "n_per_phase": a.n, "concurrency": 1,
                    "normalized_text_order_sha256": hashlib.sha256(json.dumps(texts[:3*a.n], ensure_ascii=False).encode()).hexdigest(),
                    "quantiles": "numpy percentile, linear interpolation",
                    "metrics_scope": "per-phase counter deltas, excluding one service warm-up"}, []

    p = serve(base | {"CLASSIFIER_MODE": "llm", "LLM_TIMEOUT_S": "30"}, a.port)
    try:
        warmup = httpx.post(f"http://127.0.0.1:{a.port}/v1/classify", json={"text": "warm-up message"}, timeout=60)
        warmup.raise_for_status(); assert warmup.json()["source"] == "llm"
        out["llm_cache_miss"], before, m = run(a.port, texts[:a.n], {"llm": a.n}); scrapes.append(("miss", before, m))
        out["llm_cache_hit"], before, m = run(a.port, texts[:a.n], {"cache": a.n}); scrapes.append(("hit", before, m))
    finally:
        p.terminate(); p.wait()
    p = serve(base | {"CLASSIFIER_MODE": "llm", "LLM_TIMEOUT_S": str(a.timeout_s)}, a.port)
    try:
        out["llm_timeout_fallback"], before, m = run(a.port, texts[a.n:2 * a.n], {"rules": a.n}); out["llm_timeout_fallback"]["timeout_s"] = a.timeout_s
        assert out["llm_timeout_fallback"]["prometheus_delta"]["llm_errors_by_kind"] == {"timeout": a.n}
        scrapes.append(("timeout", before, m))
    finally:
        p.terminate(); p.wait()
    # Let the real backend finish/cancel timed-out requests before the next phase.
    from riskclf.llm import LlmClient
    LlmClient(base_url=a.llm_url, model=a.model, timeout_s=30).classify("post-timeout warm-up message")
    p = serve(base | {"CLASSIFIER_MODE": "hybrid", "LLM_TIMEOUT_S": "30"}, a.port)
    try:
        out["hybrid"], before, m = run(a.port, texts[2 * a.n:3 * a.n]); scrapes.append(("hybrid", before, m))
    finally:
        p.terminate(); p.wait()

    (ROOT / "results" / "loadtest.json").write_text(json.dumps(out, indent=2))
    keep = ("riskclf_requests_total", "riskclf_llm_errors_total", "riskclf_cache_total", "riskclf_llm_tokens_total",
            "riskclf_request_seconds_count", "riskclf_llm_call_seconds_count")
    (ROOT / "results" / "loadtest_metrics.txt").write_text("\n".join(
        f"# --- {name}: {point} ---\n" + "\n".join(l for l in text.splitlines() if l.startswith(keep))
        for name, before, after in scrapes for point, text in (("before", before), ("after", after))) + "\n")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
