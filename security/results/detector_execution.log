| method | window precision | window recall | window F1 | attack-event recall | recall brute_force | recall low_slow | recall spraying | recall stuffing | benign-event FPR | NAT windows flagged |
|---|---|---|---|---|---|---|---|---|---|---|
| rules R1-R4 | 0.999 | 0.439 | 0.610 | 0.865 | 1.000 | 0.000 | 0.992 | 0.888 | 0.0011 | 4/1062 |
| rule R1_ip_failures | 1.000 | 0.114 | 0.204 | 0.588 | 0.996 | 0.000 | 0.964 | 0.381 | 0.0000 | 0/1062 |
| rule R2_ip_many_accounts | 0.998 | 0.263 | 0.417 | 0.588 | 0.000 | 0.000 | 0.992 | 0.686 | 0.0011 | 4/1062 |
| rule R3_one_account_hammered | 1.000 | 0.003 | 0.007 | 0.170 | 1.000 | 0.000 | 0.000 | 0.000 | 0.0000 | 0/1062 |
| rule R4_unknown_users | 1.000 | 0.338 | 0.505 | 0.423 | 0.000 | 0.000 | 0.000 | 0.794 | 0.0000 | 0/1062 |
| IsolationForest (unsupervised) | 0.701 | 0.698 | 0.700 | 0.855 | 1.000 | 0.868 | 0.999 | 0.747 | 0.1910 | 1034/1062 |
| GradientBoosting (supervised) | 0.997 | 0.988 | 0.992 | 0.998 | 1.000 | 0.969 | 1.000 | 1.000 | 0.0006 | 1/1062 |
| rules OR GradientBoosting | 0.997 | 0.988 | 0.992 | 0.998 | 1.000 | 0.969 | 1.000 | 1.000 | 0.0018 | 5/1062 |

Synthetic data. Train: {'events': 140232, 'attack_events': 79518, 'windows': 51001, 'attack_windows': 5826}. Test: {'events': 143416, 'attack_events': 82076, 'windows': 53208, 'attack_windows': 7358, 'by_kind': {'benign': 61340, 'stuffing': 43703, 'spraying': 18389, 'brute_force': 13967, 'low_slow': 6017}}. GradientBoosting PR-AUC 0.994. low_slow campaign absent from training.
