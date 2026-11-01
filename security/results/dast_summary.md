| scan | target | alert types (High/Medium/Low/Info) | total alert types | High+Medium alerts |
|---|---|---|---|---|
| baseline | vulnerable | 0/3/6/4 | 13 | 3 |
| baseline | fixed | 0/0/0/3 | 3 | 0 |
| full | vulnerable | 8/5/6/5 | 24 | 13 |
| full | fixed | 1/0/1/4 | 6 | 1 |

### baseline / vulnerable

- [Medium] Absence of Anti-CSRF Tokens (2 instances)
- [Medium] Content Security Policy (CSP) Header Not Set (5 instances)
- [Medium] Missing Anti-clickjacking Header (5 instances)
- [Low] Cross-Origin-Embedder-Policy Header Missing or Invalid (3 instances)
- [Low] Cross-Origin-Opener-Policy Header Missing or Invalid (3 instances)
- [Low] Cross-Origin-Resource-Policy Header Missing or Invalid (3 instances)
- [Low] Permissions Policy Header Not Set (5 instances)
- [Low] Server Leaks Version Information via "Server" HTTP Response Header Field (5 instances)
- [Low] X-Content-Type-Options Header Missing (5 instances)
- [Info] Authentication Request Identified (1 instances)
- [Info] Non-Storable Content (1 instances)
- [Info] Storable and Cacheable Content (5 instances)
- [Info] Storable but Non-Cacheable Content (1 instances)

### baseline / fixed

- [Info] Authentication Request Identified (1 instances)
- [Info] Non-Storable Content (5 instances)
- [Info] Session Management Response Identified (2 instances)

### full / vulnerable

- [High] Cross Site Scripting (DOM Based) (2 instances)
- [High] Cross Site Scripting (Reflected) (2 instances)
- [High] Path Traversal (1 instances)
- [High] Remote OS Command Injection (1 instances)
- [High] Remote OS Command Injection (Time Based) (1 instances)
- [High] SQL Injection (2 instances)
- [High] Server Side Template Injection (1 instances)
- [High] Source Code Disclosure - File Inclusion (2 instances)
- [Medium] Absence of Anti-CSRF Tokens (2 instances)
- [Medium] Buffer Overflow (2 instances)
- [Medium] Content Security Policy (CSP) Header Not Set (5 instances)
- [Medium] Integer Overflow Error (2 instances)
- [Medium] Missing Anti-clickjacking Header (5 instances)
- [Low] Cross-Origin-Embedder-Policy Header Missing or Invalid (4 instances)
- [Low] Cross-Origin-Opener-Policy Header Missing or Invalid (4 instances)
- [Low] Cross-Origin-Resource-Policy Header Missing or Invalid (4 instances)
- [Low] Permissions Policy Header Not Set (5 instances)
- [Low] Server Leaks Version Information via "Server" HTTP Response Header Field (5 instances)
- [Low] X-Content-Type-Options Header Missing (5 instances)
- [Info] Authentication Request Identified (1 instances)
- [Info] Non-Storable Content (1 instances)
- [Info] Storable and Cacheable Content (5 instances)
- [Info] Storable but Non-Cacheable Content (1 instances)
- [Info] User Agent Fuzzer (3 instances)

### full / fixed

- [High] Source Code Disclosure - File Inclusion (2 instances)
- [Low] Cookie Slack Detector (3 instances)
- [Info] Authentication Request Identified (1 instances)
- [Info] Non-Storable Content (5 instances)
- [Info] Session Management Response Identified (2 instances)
- [Info] User Agent Fuzzer (3 instances)
