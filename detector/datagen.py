"""SYNTHETIC, clearly labeled login-event generator (no real users). Mechanics follow published descriptions of
credential stuffing / brute force / password spraying; parameters are assumptions, not measurements.

Benign: users with a home IP + mobile IP, diurnal logins, password and username typos, forgotten-password bursts,
and corporate/campus NAT IPs shared by hundreds of users (hard negatives for per-IP counters).
Attacks (each event labeled with its campaign type):
  brute_force    one account, many passwords, 1-3 IPs, fast
  stuffing       leaked combo list (mostly non-existent or wrong creds, ~1-2% success) through a proxy pool
  spraying       2-3 common passwords across many real accounts, few IPs, slow
  low_slow       stuffing through thousands of proxies, 1-3 attempts each, benign-looking user agents
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DAY = 86400
BENIGN_UAS = [f"Mozilla/5.0 browser-{i}" for i in range(60)]
TOOL_UAS = ["python-requests/2.31", "curl/8.4", "OpenBullet/1.4", "Go-http-client/1.1", "okhttp/4.9"]


def _ip(rng, prefix):
    return f"{prefix}.{rng.integers(0, 256)}.{rng.integers(0, 256)}.{rng.integers(1, 255)}"


def generate(seed=0, days=7, n_users=5000, n_nat=8, scale=1.0, include_low_slow=True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    users = [f"user{i:05d}" for i in range(n_users)]
    home = [_ip(rng, "10") for _ in users]
    mobile = [_ip(rng, "100") for _ in users]
    nats = [_ip(rng, "172") for _ in range(n_nat)]
    nat_of = {u: nats[rng.integers(n_nat)] for u in rng.choice(users, size=n_users // 5, replace=False)}
    ua_of = {u: BENIGN_UAS[rng.integers(len(BENIGN_UAS))] for u in users}
    ev = []

    def add(ts, ip, u, ok, ua, label, kind):
        ev.append((float(ts), ip, u, bool(ok), ua, label, kind))

    # ---- benign
    for i, u in enumerate(users):
        n = rng.poisson(1.5 * days)
        for _ in range(n):
            day = rng.integers(days)
            hour = np.clip(rng.normal(14, 4), 0, 23.99)
            ts = day * DAY + hour * 3600 + rng.uniform(0, 60)
            r = rng.random()
            ip = nat_of[u] if (u in nat_of and r < 0.6) else (mobile[i] if r > 0.85 else home[i])
            ua = ua_of[u]
            if rng.random() < 0.08:                                   # password typo then success
                add(ts, ip, u, False, ua, 0, "benign")
                ts += rng.uniform(3, 20)
            if rng.random() < 0.04:                                   # username typo / old email (unknown user)
                add(ts, ip, u + rng.choice(["x", "_", "1", "@old"]), False, ua, 0, "benign")
                ts += rng.uniform(3, 30)
            if rng.random() < 0.01:                                   # forgot password: 3-7 failures
                for _ in range(rng.integers(3, 8)):
                    add(ts, ip, u, False, ua, 0, "benign"); ts += rng.uniform(5, 40)
                if rng.random() < 0.5:
                    continue
            add(ts, ip, u, True, ua, 0, "benign")
    # ---- attacks
    t0 = lambda: rng.uniform(0, days * DAY - DAY / 2)
    for _ in range(int(10 * scale)):                                  # brute force
        target = users[rng.integers(n_users)]
        ips = [_ip(rng, "45") for _ in range(rng.integers(1, 4))]
        ua = TOOL_UAS[rng.integers(len(TOOL_UAS))]
        ts = t0()
        for k in range(rng.integers(200, 2000)):
            ts += rng.exponential(1 / rng.uniform(1, 5))
            add(ts, ips[k % len(ips)], target, False, ua, 1, "brute_force")
    for _ in range(int(6 * scale)):                                   # credential stuffing via proxy pool
        n_combo = rng.integers(3000, 10000)
        pool = [_ip(rng, "185") for _ in range(rng.integers(50, 500))]
        ua = TOOL_UAS[rng.integers(len(TOOL_UAS))] if rng.random() < 0.7 else BENIGN_UAS[rng.integers(60)]
        ts = t0()
        rate = rng.uniform(0.5, 3)
        for k in range(n_combo):
            ts += rng.exponential(1 / rate)
            exists = rng.random() < 0.3
            u = users[rng.integers(n_users)] if exists else f"leak{rng.integers(1e7):07d}"
            add(ts, pool[rng.integers(len(pool))], u, exists and rng.random() < 0.05, ua, 1, "stuffing")
    for _ in range(int(4 * scale)):                                   # password spraying, slow
        targets = rng.choice(users, size=min(int(rng.integers(1000, 2500)), n_users // 2), replace=False)
        ips = [_ip(rng, "91") for _ in range(rng.integers(5, 20))]
        ua = TOOL_UAS[rng.integers(len(TOOL_UAS))]
        ts = t0()
        for _pw in range(rng.integers(2, 4)):
            for u in targets:
                ts += rng.exponential(6.0)
                add(ts, ips[rng.integers(len(ips))], u, rng.random() < 0.003, ua, 1, "spraying")
    if include_low_slow:
        for _ in range(max(1, int(1 * scale))):                       # low-and-slow stuffing
            pool = [_ip(rng, "203") for _ in range(3000)]
            ts = t0()
            for ip in pool:
                for _ in range(rng.integers(1, 4)):
                    ts += rng.exponential(20.0)
                    exists = rng.random() < 0.3
                    u = users[rng.integers(n_users)] if exists else f"leak{rng.integers(1e7):07d}"
                    add(ts, ip, u, exists and rng.random() < 0.05, BENIGN_UAS[rng.integers(60)], 1, "low_slow")
    df = pd.DataFrame(ev, columns=["ts", "ip", "username", "success", "user_agent", "label", "kind"])
    df["user_exists"] = df["username"].isin(set(users))
    return df.sort_values("ts", kind="stable").reset_index(drop=True)
