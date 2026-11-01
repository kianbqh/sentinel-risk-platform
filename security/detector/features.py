"""Per (IP, 1-hour window) features. Only uses information a login service has: timestamps, IP, username,
success, whether the username exists, user agent."""
from __future__ import annotations

import numpy as np
import pandas as pd

WINDOW = 3600
FEATURES = ["n", "n_fail", "fail_ratio", "n_users", "users_per_attempt", "unknown_user_ratio", "n_success",
            "iat_median", "iat_cv", "n_ua", "ua_tool", "max_fail_one_user", "ip_hist_users"]
TOOL_HINTS = ("python-requests", "curl", "OpenBullet", "Go-http-client", "okhttp")


def window_features(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["win"] = (d["ts"] // WINDOW).astype(np.int64)
    d["fail"] = ~d["success"]
    d["tool"] = d["user_agent"].str.startswith(TOOL_HINTS)
    d["unknown"] = ~d["user_exists"]
    d = d.sort_values(["ip", "win", "ts"], kind="stable")
    key = ["ip", "win"]
    g = d.groupby(key, sort=False)
    f = g.agg(n=("ts", "size"), n_fail=("fail", "sum"), fail_ratio=("fail", "mean"), n_users=("username", "nunique"),
              unknown_user_ratio=("unknown", "mean"), n_success=("success", "sum"), n_ua=("user_agent", "nunique"),
              ua_tool=("tool", "mean"), label=("label", "max"))
    f["users_per_attempt"] = f["n_users"] / f["n"]
    # inter-arrival stats inside the window
    d["iat"] = d.groupby(key, sort=False)["ts"].diff()
    ia = d.groupby(key, sort=False)["iat"].agg(["median", "mean", "std"])
    f["iat_median"] = ia["median"].fillna(WINDOW)
    f["iat_cv"] = (ia["std"] / (ia["mean"] + 1e-9)).fillna(0.0)
    mf = d[d["fail"]].groupby(key + ["username"], sort=False).size().groupby(level=[0, 1]).max()
    f["max_fail_one_user"] = mf.reindex(f.index).fillna(0).astype(int)
    # attack kind of the window (attack kind wins over benign)
    kd = d.sort_values("label", kind="stable").groupby(key, sort=False)["kind"].last()
    f["kind"] = kd.reindex(f.index)
    f["events"] = f["n"]
    # cumulative distinct users per IP up to and including this window (no look-ahead)
    first = d.drop_duplicates(["ip", "username"]).groupby(key).size()
    f["ip_hist_users"] = first.reindex(f.index).fillna(0).groupby(level=0).cumsum()
    f = f.reset_index()
    return f
