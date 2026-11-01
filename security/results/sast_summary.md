| target | bandit findings | bandit H/M/L | semgrep findings | semgrep ERROR/WARNING/INFO |
|---|---|---|---|---|
| vulnerable | 13 | 6/4/3 | 23 | 8/15/0 |
| fixed | 0 | 0/0/0 | 0 | 0/0/0 |

### vulnerable

- bandit B105 hardcoded_password_string (LOW) line 21
- bandit B108 hardcoded_tmp_directory (MEDIUM) line 107
- bandit B201 flask_debug_true (HIGH) line 103
- bandit B201 flask_debug_true (HIGH) line 108
- bandit B301 blacklist (MEDIUM) line 62
- bandit B324 hashlib (HIGH) line 50
- bandit B324 hashlib (HIGH) line 95
- bandit B324 hashlib (HIGH) line 96
- bandit B403 blacklist (LOW) line 10
- bandit B404 blacklist (LOW) line 12
- bandit B602 subprocess_popen_with_shell_equals_true (HIGH) line 79
- bandit B608 hardcoded_sql_expressions (MEDIUM) line 51
- bandit B608 hardcoded_sql_expressions (MEDIUM) line 68
- semgrep avoid-insecure-deserialization (ERROR) line 55
- semgrep avoid-insecure-deserialization (ERROR) line 62
- semgrep dangerous-subprocess-use (ERROR) line 79
- semgrep debug-enabled (WARNING) line 103
- semgrep debug-enabled (WARNING) line 107
- semgrep directly-returned-format-string (WARNING) line 63
- semgrep flask-login-without-throttle (WARNING) line 47
- semgrep insecure-hash-algorithm-md5 (WARNING) line 50
- semgrep insecure-hash-algorithm-md5 (WARNING) line 95
- semgrep insecure-hash-algorithm-md5 (WARNING) line 96
- semgrep pickle-from-request (ERROR) line 62
- semgrep raw-html-format (WARNING) line 69
- semgrep raw-html-format (WARNING) line 74
- semgrep sql-injection-db-cursor-execute (WARNING) line 49
- semgrep sql-injection-db-cursor-execute (WARNING) line 67
- semgrep subprocess-injection (ERROR) line 79
- semgrep subprocess-shell-true (ERROR) line 79
- semgrep tainted-sql-string (ERROR) line 51
- semgrep tainted-sql-string (ERROR) line 68

### fixed

