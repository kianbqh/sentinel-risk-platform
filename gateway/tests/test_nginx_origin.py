"""Exercise the same POSIX origin parser sourced by the console image."""
import os
from pathlib import Path
import subprocess
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / 'console/frontend/19-backend-upstream.envsh'


class OriginTests(unittest.TestCase):
    def parse(self, origin):
        command = '. "$1"; printf "%s\\n" "$BACKEND_SCHEME" "$BACKEND_AUTHORITY" "$BACKEND_UPSTREAM_SERVER" "$BACKEND_TLS_NAME"'
        return subprocess.run(['sh', '-ec', command, 'test', str(SCRIPT)],
                              env={**os.environ, 'BACKEND_URL': origin}, capture_output=True, text=True)

    def test_http_https_hostname_ipv4_and_ipv6(self):
        cases = {
            'http://backend:18041': ['http', 'backend:18041', 'backend:18041', 'backend'],
            'http://127.0.0.1:18041/': ['http', '127.0.0.1:18041', '127.0.0.1:18041', '127.0.0.1'],
            'http://backend': ['http', 'backend', 'backend:80', 'backend'],
            'https://api.example.com': ['https', 'api.example.com', 'api.example.com:443', 'api.example.com'],
            'http://[::1]:18041': ['http', '[::1]:18041', '[::1]:18041', '::1'],
            'https://[2001:db8::1]/': ['https', '[2001:db8::1]', '[2001:db8::1]:443', '2001:db8::1'],
        }
        for origin, expected in cases.items():
            with self.subTest(origin=origin):
                result = self.parse(origin)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), expected)

    def test_rejects_ambiguous_origins_and_configuration_injection(self):
        for origin in ('', 'ftp://backend', 'http://user:password@backend', 'http://backend/path',
                       'http://backend?query=x', 'http://backend#fragment', 'http://::1:80',
                       'http://backend:0', 'http://backend:65536', 'http://backend:abc',
                       'http://backend;include /tmp/injected', 'http://backend\nother'):
            with self.subTest(origin=origin):
                self.assertNotEqual(self.parse(origin).returncode, 0)
