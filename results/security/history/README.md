# Earlier scan observations

These runs used the proxy configuration before explicit upstream connection reuse. The archive manifest records the source commit, GitHub artifact digest and SHA-256 of each retained file. HTTP captures use lossless gzip; `gzip -dk full-http.json.gz` restores the original JSON bytes.

| Run | Recorded outcome |
|---|---|
| [41299200564](https://github.com/kianbqh/sentinel-risk-platform/actions/runs/41299200564) | Full scan: one HIGH / LOW-confidence Path Traversal type, two instances, ZAP alert reference `6-5`. The gate failed. Original alert request/response bodies were not retained in this run. |
| [41303187547](https://github.com/kianbqh/sentinel-risk-platform/actions/runs/41303187547) | Full scan: zero HIGH/MEDIUM types. Evidence collection failed because 85,679 active message IDs exceeded the original 50,000-ID capture cap; all 50,000 attempted reads succeeded. The retained HTTP evidence contains 6,104 selected messages. |

The later capture records ZAP 2.17.0, ascanrules 84.0.0 and GraphQL add-on 0.35.0. The older HIGH run also logged 6,369 nginx upstream `Address not available` errors and 6,369 matching 502 access-log entries. In the later capture, 1,092 of the selected messages were nginx 502 responses; that selected count is not the total error count for the scan. These records exposed a real proxy connection-availability problem.

ZAP's [`ascanrules-v84` rule](https://github.com/zaproxy/zap-extensions/blob/ascanrules-v84/addOns/ascanrules/src/main/java/org/zaproxy/zap/extension/ascanrules/PathTraversalScanRule.java) uses a differential response heuristic for `6-5`. The later original-message capture contains typed GraphQL variable pairs rejected with HTTP422, and other pairs affected by HTTP502. The six post-scan requests are explicitly marked constructed probes. This later traffic does not recover the first run's missing original alert bodies, so the historical two instances remain recorded without a definitive false-positive classification.

The connection fix uses an explicit upstream and keepalive cache. nginx 1.29.8's [initialization code](https://github.com/nginx/nginx/blob/release-1.29.8/src/http/modules/ngx_http_upstream_keepalive_module.c#L496-L520) skips implicit upstreams; its HTTP/1.1 and empty Connection-header defaults alone did not enable that cache. Subsequent scans retain the original HIGH/MEDIUM gate, capture the complete finite message-ID snapshot, and separately reject HTTP502 or upstream address-exhaustion errors.
