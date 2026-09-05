import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('predeploy', ROOT / 'deploy/cashback/predeploy-backup.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class PredeployBackupTests(unittest.TestCase):
    def make_source(self, root):
        for relative in M.CONFIGS:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('private-config-value')
        db = root / M.DATABASE
        db.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db)
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE cashback_events (id TEXT PRIMARY KEY, amount INTEGER)')
        connection.execute("INSERT INTO cashback_events VALUES ('live-event', 12345)")
        connection.execute('CREATE TABLE push_subscriptions (id TEXT PRIMARY KEY, secret TEXT)')
        connection.execute("INSERT INTO push_subscriptions VALUES ('push', 'private-push-key')")
        connection.commit()
        return connection

    def test_exact_private_snapshot_includes_wal_config_and_push_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'stacks'
            with self.make_source(root) as live:
                destination = Path(temporary) / 'backup'
                # Keep source connection open to retain committed WAL frames.
                self.assertTrue(Path(str(root / M.DATABASE) + '-wal').exists())
                manifest = M.snapshot(root, destination, 'registry/cashback@sha256:' + 'a' * 64, {'private': 'env'})
                self.assertEqual(manifest['database_table_counts'], {'cashback_events': 1, 'push_subscriptions': 1})
                M.verify(destination)
                with sqlite3.connect(destination / 'files' / M.DATABASE) as restored:
                    self.assertEqual(restored.execute('SELECT * FROM cashback_events').fetchall(), [('live-event', 12345)])
                    self.assertEqual(restored.execute('SELECT secret FROM push_subscriptions').fetchone()[0], 'private-push-key')
                self.assertEqual(live.execute('SELECT COUNT(*) FROM cashback_events').fetchone()[0], 1)
                for path in destination.rglob('*'):
                    self.assertEqual(path.stat().st_mode & 0o077, 0)
                for relative in M.CONFIGS:
                    self.assertEqual((root / relative).read_text(), 'private-config-value')

    def test_corrupted_backup_or_missing_manifest_entry_fails_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'stacks'
            with self.make_source(root):
                destination = Path(temporary) / 'backup'
                M.snapshot(root, destination, 'registry/cashback@sha256:' + 'a' * 64, {})
                path = destination / 'files' / M.CONFIGS[0]
                path.write_text('corrupt')
                with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                    M.verify(destination)
                path.write_text('private-config-value')
                manifest = json.loads((destination / 'manifest.json').read_text())
                del manifest['files'][M.CONFIGS[0]]
                (destination / 'manifest.json').write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, 'Incomplete'):
                    M.verify(destination)

    def test_missing_source_db_or_symlink_fails_without_config_mutation(self):
        for failure in ('database', 'symlink'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / 'stacks'
                connection = self.make_source(root)
                connection.close()
                if failure == 'database':
                    (root / M.DATABASE).unlink()
                else:
                    path = root / M.CONFIGS[2]
                    path.unlink()
                    path.symlink_to(root / M.CONFIGS[1])
                destination = Path(temporary) / 'backup'
                with self.assertRaises(ValueError):
                    M.snapshot(root, destination, 'registry/cashback@sha256:' + 'a' * 64, {})
                self.assertFalse((destination / 'verification.json').exists())
                self.assertEqual((root / M.CONFIGS[0]).read_text(), 'private-config-value')
