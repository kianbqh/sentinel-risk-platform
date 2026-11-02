# Recorded findings

The original production reports in `ci/` and authenticated console reports in `zap/` belong to source revision `dbd3069eebb9b6b9f160715d8ade1a851afdb07b` and [workflow run 41306587753](https://github.com/kianbqh/sentinel-risk-platform/actions/runs/41306587753). `ci-provenance.json` and `zap/evidence-index.json` record the verified artifact digests and file hashes. Earlier local reports remain in `local-zap/`; earlier CI observations remain in [history](history/README.md).

| Check | Actual findings | Gate |
|---|---|---|
| Semgrep | 0 findings, 0 errors | Pass |
| Bandit | 1 MEDIUM severity / HIGH confidence, B310 | Pass under the documented HIGH-severity gate |
| Trivy | 1 UNKNOWN advisory without a fixed version; 0 secrets; 0 fixable HIGH/CRITICAL | Pass |
| ZAP baseline | 0 high, 0 medium, 4 low, 5 informational alert types | Pass |
| ZAP full, five-minute active cap | 0 high, 0 medium, 4 low, 5 informational alert types | Pass |

Bandit's B310 is the `urllib.request.urlopen(URL)` call in `text-risk/riskclf/data.py:26`, used by the UCI SMS downloader. `URL` is a fixed HTTPS constant, and the downloaded ZIP must match its pinned SHA-256 before the named dataset member is extracted. The finding remains in the original report.

Trivy retains `GO-2026-5932` for `golang.org/x/crypto` version 0.58.0 with UNKNOWN severity and no fixed version in that scan's database. It remains an open dependency observation. The earlier fixable HIGH finding in `klauspost/compress` was corrected by upgrading to 1.18.7, without reducing the gate.

The four ZAP low-risk types are missing COEP, COOP and CORP browser isolation headers, plus Unix timestamp disclosure. The timestamp instances include the application's audit fields; their severity and evidence are retained. The five informational types identify authentication, GraphQL introspection and implementation, the web application, and non-storable content. Both scans contain 25 alert instances; type counts are not instance counts.

Both scope proofs identify the registered scanner analyst, protect mode, the single localhost origin, query depth 3, argument depth 2, additional depth 0, lenient depth disabled and the five-minute active limit. Baseline took 75.293 seconds and the full runner took 475.374 seconds, including report generation and original-message collection.

The full run accounted for all 80,028 active message IDs without missing or truncated reads: 40,130 HTTP200, 28 HTTP400, 4 HTTP404 and 39,866 HTTP422 responses. It retained 5,564 selected original sentinel/candidate messages, followed by six separately marked constructed probes. The console logs and response counts contain no HTTP502 or upstream `Address not available` errors. The actual nginx 1.29.8 configuration is in `zap/nginx-config.txt`.

HTTP evidence and console logs are stored as lossless gzip to keep the repository small. For example, `gzip -dk zap/full-http.json.gz` restores the exact JSON filename referenced by the original scanner manifest. The evidence index records hashes of both original and stored bytes. Published copies are checked after decompression for scanner JWTs, the scanner password and local environment-secret values; redaction counts remain in the scanner manifest.
