# Reviewed package-documentation JWT

[Workflow run 41454846096](https://github.com/kianbqh/sentinel-risk-platform/actions/runs/41454846096), security job 125308813221, reported one `jwt-token` finding in `copilot/results/ci-tools-5465aae-01/environment/pip-inspect.json`. The finding is in the installed PyJWT 2.15.1 package's description, which embeds its README example. The example encodes the payload `{'some': 'payload'}` with HS256 using the documented example key `secret`. Decoding the recorded token and verifying its HMAC with that key confirms the example. It carries no application identity or access claim.

The raw dependency evidence and raw Trivy finding remain unchanged. The gate retains this finding in `trivy-summary.json` with `disposition: reviewed_public_example`, its reason, and the reviewed hashes. Every other secret finding blocks the gate. Fixable HIGH/CRITICAL dependency findings continue to block it.

This disposition applies once per report and requires all of these bindings:

| Binding | Reviewed value |
|---|---|
| Exact target | `copilot/results/ci-tools-5465aae-01/environment/pip-inspect.json` |
| Complete source file SHA-256 | `10f7bda7eab3d1d36165b6aadce1b91288cfc4b9ac6159e255dfd8e072540a48` |
| Rule / class | `jwt-token` / `secret` |
| Source line / byte offset / token bytes | `1496` / `281365` / `105` |
| Example token SHA-256 | `bf145d9380bbe05bdc1a72525796b224f8a8c93ae0e9e102eafbc255ac1af9e8` |
| Complete finding descriptor SHA-256 | `67aff153a18be9ea3016ce83032da419dc798a93f3c11b8ed9eaa57b79dbef52` |

The descriptor hash covers every field of the original secret finding, including its masked `Match`, source range and `Code` context. It uses UTF-8 JSON with sorted keys, compact separators and ASCII escaping. Trivy already masked the token in the report; the gate separately checks the token's exact source bytes at the recorded offset.

A changed path, rule, source byte, token, position or scanner descriptor requires review and blocks the gate. A missing source file, an additional secret or a duplicate finding also blocks it. No scanner path, dependency-inspection file type or JWT rule is excluded.

`security/tests/test_trivy_gate.py` uses the original masked CI finding and the unchanged dependency-inspection evidence. It checks the accepted case, changed bindings, new and duplicate findings, raw-report preservation and the existing vulnerability policy. The existing Python CI job discovers these tests through `security/tests`; its workflow body and the Trivy invocation remain unchanged.
