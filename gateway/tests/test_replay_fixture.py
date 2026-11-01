import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from gateway import demo


class ReplayFixtureTests(unittest.TestCase):
    def test_default_is_real_versioned_input_without_a_network_fetch(self):
        manifest = json.loads((demo.FIXTURE_DIR / 'manifest.json').read_text())
        with tempfile.TemporaryDirectory() as directory, patch('sklearn.datasets.fetch_openml', side_effect=AssertionError('Unexpected network dependency')):
            path = Path(directory) / 'replay.parquet'
            frame = demo.prepare(path)
            self.assertEqual(len(frame), 10000)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), manifest['sha256'])
            self.assertEqual(frame.txn_id.tolist(), list(range(10000)))

    def test_a_modified_cache_is_rejected_before_parquet_is_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'replay.parquet'
            path.write_bytes(b'corrupted cache')
            with self.assertRaisesRegex(RuntimeError, 'Cached replay SHA-256 mismatch'):
                demo.prepare(path)

    def test_a_modified_shipped_fixture_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / 'fixtures'
            shutil.copytree(demo.FIXTURE_DIR, fixture)
            manifest = json.loads((fixture / 'manifest.json').read_text())
            (fixture / manifest['file']).write_bytes(b'corrupted bundled input')
            with patch.object(demo, 'FIXTURE_DIR', fixture), self.assertRaisesRegex(RuntimeError, 'Bundled replay SHA-256 mismatch'):
                demo.prepare(Path(directory) / 'replay.parquet')
