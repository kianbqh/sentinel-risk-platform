import numpy as np

from detector.datagen import generate
from detector.features import FEATURES, window_features
from detector.online import LoginThrottle
from detector.rules import apply_rules


def small():
    return generate(seed=7, days=2, n_users=500, scale=0.3)


def test_generator_labels_and_determinism():
    a, b = small(), small()
    assert a.equals(b)
    assert set(a["kind"]) >= {"benign", "brute_force", "stuffing", "spraying", "low_slow"}
    assert ((a["kind"] == "benign") == (a["label"] == 0)).all()
    assert (~a.loc[a.username.str.startswith("leak"), "user_exists"]).all()


def test_features_shape_and_no_nan():
    f = window_features(small())
    assert not f[FEATURES].isna().any().any()
    assert f["events"].sum() == len(small())


def test_rules_catch_brute_force_no_benign_flags():
    f = window_features(small())
    hit = apply_rules(f)
    bf = f["kind"] == "brute_force"
    assert hit[bf & (f["n"] >= 20)].all()
    assert hit[f["label"] == 0].mean() < 0.01


def test_throttle_window_and_reset():
    t = [0.0]
    th = LoginThrottle(window_s=60, max_fail_user=3, clock=lambda: t[0])
    for _ in range(3):
        th.record("1.1.1.1", "u", False)
    assert th.blocked("2.2.2.2", "u")                     # per-account limit holds across IPs
    t[0] = 61
    assert not th.blocked("2.2.2.2", "u")
