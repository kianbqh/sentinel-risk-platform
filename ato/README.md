# Synthetic login-abuse signals

This module generates synthetic login attempts: ordinary users, shared NATs, password typos and four attack patterns. It compares completed IP-hour windows with thirteen features. The supervised classifier is trained on seed1 without the low-and-slow campaign and evaluated on an independent seed2 batch that includes it.

`python -m ato.publish` fits the histogram gradient booster, evaluates the test batch, and publishes a bounded number of flagged windows to `sentinel.alerts.v1`. Every envelope contains `evidence.synthetic:true`, its window features and model version. The fixed January2026 timestamps are a synthetic timeline. Labels and attack-kind ground truth stay in the evaluation report.

The window metrics are retrospective completed-hour classification. The separate `online.LoginThrottle` is the sliding-window rule component. IsolationForest in `evaluate.py` fits without supervised labels but uses the training attack-window proportion for `contamination`; its threshold therefore uses that label prior.

The integrated run writes fresh metrics and the exact published signals to `../results/integration/ato-publish.json`. The generator has disjoint benign/attack IP pools; its event-weighted recall assigns attempts the classification of their completed window. These are measurements of synthetic traffic.
