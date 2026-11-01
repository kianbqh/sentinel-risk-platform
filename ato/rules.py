"""Threshold rules over (IP, hour) windows, the kind a WAF / auth service would ship by default."""
import pandas as pd

RULES = {
    "R1_ip_failures": lambda f: f["n_fail"] >= 20,
    "R2_ip_many_accounts": lambda f: (f["n_users"] >= 10) & (f["fail_ratio"] >= 0.5),
    "R3_one_account_hammered": lambda f: f["max_fail_one_user"] >= 10,
    "R4_unknown_users": lambda f: (f["n"] >= 5) & (f["unknown_user_ratio"] >= 0.6),
}


def apply_rules(f: pd.DataFrame) -> pd.Series:
    hit = pd.Series(False, index=f.index)
    for r in RULES.values():
        hit |= r(f)
    return hit
