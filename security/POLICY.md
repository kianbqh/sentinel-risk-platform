# Platform checks

The active services are the shared Python gateway/ATO publisher, the Scala stream, and the Go/React console. `fixtures/apps/` contains the deliberately vulnerable notes apps used only by regression tests; neither app is included in the platform images or Compose stack.

- Semgrep scans production source with the registry `p/ci` and `p/secrets` packs plus the local rules. Every finding stays in the JSON report; ERROR findings fail the gate.
- Bandit scans production Python. HIGH-severity findings at MEDIUM/HIGH confidence fail; the full report includes lower-severity observations. Loading the committed scikit-learn artifacts is a trusted-artifact boundary.
- Trivy scans platform dependency manifests and secrets. Fixable HIGH/CRITICAL vulnerabilities and detected secrets fail the gate. The raw report also retains lower-severity and unfixed vulnerabilities. Terraform has a separate formatting/validation job.
- Generated GraphQL code, tests, imported CI examples, research outputs and intentional fixtures are outside the production SAST target. The dependency scan still includes the service lockfiles.
- The bounded ZAP run targets only the owned console at `127.0.0.1:13040`, including authenticated GraphQL requests through its same-origin proxy. Its context is in protect mode and its active scanner has a five-minute cap. Scanner exit codes and parseable JSON/HTML reports are checked separately from finding counts.

Raw findings are retained under `../results/security/`; triage notes accompany any accepted observation. A failed scanner startup is a failed check.
