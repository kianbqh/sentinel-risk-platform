# Platform security checks

The platform runs Semgrep, Bandit and Trivy against its active services and dependency manifests, plus a bounded ZAP scan of its own analyst console. [The policy](POLICY.md) defines each gate and the full raw reports remain in `../results/security/`.

ZAP is restricted to `http://127.0.0.1:13040`. The runner verifies that this listener belongs to the expected Compose console, seeds an authenticated GraphQL request, and restricts its token replacement and scan context to that origin. Baseline and full scans record their exit codes, parsed JSON/HTML reports and context proof; the active phase has a five-minute cap. The [ZAP GraphQL add-on](https://www.zaproxy.org/docs/desktop/addons/graphql-support/) imports a bounded set of requests.

The [recorded findings](../results/security/TRIAGE.md) identify the actual source revision, retained low-risk observations and earlier scan history. Original selected HTTP messages and console logs are published as gzip with SHA-256 hashes. The runner accounts for the completed scan's entire message-ID snapshot and separately checks proxy availability.

`fixtures/apps/` contains the intentionally vulnerable and fixed notes applications from the original security experiment. They are test fixtures, excluded from production images and production-service scans. The regression suite demonstrates local SQL injection, XSS, path traversal, command execution, unsafe deserialization and authentication defenses without deploying those apps in the platform.

`ato/` at the repository root is the synthetic login-abuse detector. Its dataset and fresh integration results are described in that module's README; it is the only detector component that publishes platform alerts.
