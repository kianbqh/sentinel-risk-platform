# appsec-detection

I was curious about the gap between spotting a bug in source, triggering it over HTTP, and noticing an abusive login pattern. This project puts those three views around a small Flask notes service:

- A deliberately vulnerable app and a fixed version with matching routes.
- Exploit tests, Bandit and Semgrep checks, and scoped OWASP ZAP scans.
- A synthetic login-event experiment comparing rules, IsolationForest and gradient boosting, plus an in-memory login throttle in the fixed app.

The dynamic-scan runner starts its own apps on `127.0.0.1:5001` and `:5002`, checks which process owns each listener, and cleans up its own processes and temporary databases. ZAP runs in protect mode with a context containing just that app's origin.

## Bugs and fixes

| Example | Vulnerable behavior | Fixed behavior |
|---|---|---|
| SQL injection | Login bypass and a UNION query expose user records | Parameterized SQLite queries |
| Reflected XSS / template injection | Input becomes markup or a Jinja expression | Template variables, autoescaping and CSP |
| Command injection | A disk-usage parameter reaches a shell | Fixed argument list and a path allow-list |
| Path traversal | A file parameter can read the app's source | `send_from_directory` and safe path joining |
| Insecure deserialization | A pickle cookie executes a supplied expression | Signed JSON sessions |
| Open redirect | Login accepts an external destination | Same-site relative redirects |
| Passwords / sessions | MD5 hashes, a hardcoded secret and loose cookie settings | Salted password hashing, an environment or generated secret, HttpOnly and SameSite cookies |
| Request handling | Missing CSRF checks, security headers and login throttling | CSRF tokens, response headers and sliding-window limits |

The tests demonstrate each exploit against the vulnerable implementation and check the fixed behavior. The online throttle tracks failures per IP and account, and distinct accounts tried by an IP; its state belongs to one application process.

## Local results

Measured on 31 October 2026 with Python 3.12.3 on Ubuntu 24.04 / WSL2, using an i7-12700KF. Exact package versions, the ZAP image digest and detector settings are in [runtime_environment.json](results/runtime_environment.json).

**Tests:** 26 passed: the original 15 app-security and 4 detector tests, plus 7 scanner-report validation cases. The latter cover finding exit codes, a failed scanner process, missing reports, empty scan sites and an unexpected target origin. See [pytest.txt](results/pytest.txt).

**SAST:** Bandit 1.9.4 and Semgrep 1.183.0, using `p/python`, `p/flask`, `p/secrets` and two local rules. These are finding counts, so several findings can describe the same bug.

| Target | Bandit High / Medium / Low | Semgrep ERROR / WARNING / INFO |
|---|---|---|
| Vulnerable | 6 / 4 / 3 | 8 / 15 / 0 |
| Fixed | 0 / 0 / 0 | 0 / 0 / 0 |

Both fixed-app CI gates also passed locally. [SAST summary](results/sast_summary.md) · [scanner return codes and report checks](results/sast_run_manifest.json).

**DAST:** ZAP 2.17.0 in Docker, with one minute of crawling per scan and a five-minute maximum active-scan duration. Each container has a two-CPU limit. The runner checks the scanner return code, parses the JSON, verifies the HTML report, and checks the reported origins and context configuration.

| Scan | Vulnerable High / Medium / Low / Info | Fixed High / Medium / Low / Info |
|---|---|---|
| Baseline | 0 / 3 / 6 / 4 | 0 / 0 / 0 / 3 |
| Full active | 8 / 5 / 6 / 5 | 1 / 0 / 1 / 4 |

The fixed app's High finding, “Source Code Disclosure – File Inclusion,” concerns `/greet?name=` and `/search?q=`. Repeating the reported `greet` / `search` values and random controls over HTTP produced exactly the template output. Source review shows template variables and a parameterized query, with no input-selected source-file read, so I classified these two instances as false positives. [Requests, responses and reviewed handlers](results/fixed_alert_triage.json).

All four scanners returned 0. Their total wall times were 83.757 / 329.323 seconds for the vulnerable baseline / full scans and 26.661 / 139.110 seconds for the fixed pair. The five-minute setting limits the active phase; a completed scan can finish sooner.

[DAST summary](results/dast_summary.md) · [run manifest](results/dast_run_manifest.json).

## Login-abuse experiment

The generator produces two independent synthetic weeks. Training uses seed 1: 140,232 events and 51,001 IP-hour windows, with no low-and-slow campaign. Testing uses seed 2: 143,416 events and 53,208 windows, including 6,017 low-and-slow attempts. Benign traffic includes password and username typos, forgotten-password bursts, and shared campus or corporate NAT addresses.

The 13 features use login-service observations such as failures, distinct usernames, unknown-user ratio, inter-arrival times and user agents. Labels are assigned to IP-hour windows. The generator separates benign and attack IP pools, so weighting these windows by their event counts also gives the event-level metrics below.

| Method | Window precision | Window recall | Attack-event recall | Low-and-slow recall | Benign-event FPR | Shared-NAT windows flagged |
|---|---|---|---|---|---|---|
| Rules R1–R4 | 0.9988 | 0.4392 | 0.8652 | 0.0000 | 0.1141% | 4 / 1,062 |
| IsolationForest | 0.7006 | 0.6984 | 0.8555 | 0.8679 | 19.0952% | 1,034 / 1,062 |
| HistGradientBoosting | 0.9971 | 0.9879 | 0.9978 | 0.9694 | 0.0619% | 1 / 1,062 |
| Rules OR gradient boosting | 0.9966 | 0.9879 | 0.9978 | 0.9694 | 0.1761% | 5 / 1,062 |

IsolationForest fits 200 trees without label targets; its `contamination` setting uses the training attack-window fraction, **5,826 / 51,001 = 11.4233%**. The gradient booster uses balanced class weights, 200 maximum iterations, learning rate 0.1 and a fixed 0.5 decision threshold. Its test-window PR-AUC is **0.99414**.

In this generated dataset, the rules miss the low-and-slow campaign's 1–3 attempts per proxy IP. IsolationForest catches many of those attempts but also flags most shared-NAT windows. The supervised model separates those cases more cleanly. [Full metrics and per-rule results](results/detector_metrics.md) · [raw metrics](results/detector_metrics.json) · [generator assumptions](detector/datagen.py).

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-lock.txt
BIN="$VIRTUAL_ENV/bin" bash scripts/run_all.sh
```

Docker is needed for DAST. Ports 5001 and 5002 must be free. `TMPDIR` selects the location of the runner's unique temporary directory; `DOCKER_BIN` and `DOCKER_HOST` can select an existing Docker engine. `ZAP_IMAGE` can pin the image digest recorded with these results.

```bash
BIN="$VIRTUAL_ENV/bin" bash scan/run_sast.sh
python -m detector.evaluate
PY="$VIRTUAL_ENV/bin/python" FULL_MINUTES=5 bash scan/run_dast.sh both
```

`SKIP_DAST=1` skips Docker scans in `run_all.sh`. The GitHub Actions workflow runs the exploit and report-validation tests, fixed-app SAST gates, and a scoped baseline of both local apps. It uploads the reports and rejects Medium or High baseline alerts on the fixed app.
