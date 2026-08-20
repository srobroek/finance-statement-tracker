import hashlib
import importlib.util
import io
import json
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

verifier_path = Path(__file__).parents[1] / "deploy" / "actual-poc" / "verify-backup.py"
verifier_spec = importlib.util.spec_from_file_location("finance_backup_verifier", verifier_path)
if verifier_spec is None or verifier_spec.loader is None:
    raise RuntimeError("cannot load backup verifier")
verifier = importlib.util.module_from_spec(verifier_spec)
verifier_spec.loader.exec_module(verifier)


class BackupVerifierTests(unittest.TestCase):
    def _sqlite(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE proof (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO proof(value) VALUES ('restorable')")
        connection.commit()
        connection.close()

    def _push_state(self, path: Path) -> None:
        with sqlite3.connect(path) as connection:
            connection.executescript(
                """
                CREATE TABLE push_subscriptions (
                    endpoint TEXT PRIMARY KEY,
                    p256dh TEXT NOT NULL,
                    auth TEXT NOT NULL
                );
                CREATE TABLE push_deliveries (
                    notification_key TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    payload_hash TEXT NOT NULL
                );
                CREATE TABLE push_state (
                    state_key TEXT PRIMARY KEY,
                    state_value TEXT NOT NULL
                );
                INSERT INTO push_subscriptions VALUES (
                    'https://push.example/private-endpoint',
                    'secret-p256dh-value',
                    'secret-auth-value'
                );
                INSERT INTO push_deliveries VALUES (
                    'alert-1', 'https://push.example/private-endpoint', 'hash'
                );
                INSERT INTO push_state VALUES ('routing-map', '{}');
                """
            )

    def _backup(
        self,
        root: Path,
        *,
        unsafe_member: str | None = None,
        push_data: bool = False,
        sanitize_push: bool = False,
        extra_push_database: bool = False,
        historical_push_snapshot: bool = False,
    ) -> Path:
        source = root / "source"
        cashback_database = source / "cashback-data/cashback-events.sqlite3"
        self._sqlite(source / "actual-data/server-files/account.sqlite")
        self._sqlite(source / "actual-data/user-files/budget.sqlite")
        self._sqlite(cashback_database)
        if push_data:
            self._push_state(cashback_database)
        if extra_push_database:
            auxiliary_database = source / "cashback-data/auxiliary.sqlite3"
            self._sqlite(auxiliary_database)
            self._push_state(auxiliary_database)
        if historical_push_snapshot:
            historical_database = source / "cashback-data/pre-deploy-20260818T010203Z.sqlite3"
            self._sqlite(historical_database)
            self._push_state(historical_database)
        if sanitize_push:
            subprocess.run(
                [
                    sys.executable,
                    str(Path("deploy/actual-poc/sanitize-cashback-backup.py")),
                    str(cashback_database),
                ],
                check=True,
            )
        (source / "configuration").mkdir(parents=True)
        for name in ("actual-compose.yaml", "cashback-compose.yaml"):
            (source / "configuration" / name).write_text("services: {}\n", encoding="utf-8")
        (source / "configuration/profile.json").write_text('{"schema_version": 1}\n', encoding="utf-8")

        backup = root / "20260818T010203Z"
        backup.mkdir()
        archive = backup / "finance-data.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            for path in sorted(source.rglob("*")):
                bundle.add(path, arcname=path.relative_to(source).as_posix(), recursive=False)
            if unsafe_member:
                info = tarfile.TarInfo(unsafe_member)
                payload = b"unsafe"
                info.size = len(payload)
                bundle.addfile(info, io.BytesIO(payload))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (backup / "SHA256SUMS").write_text(f"{digest}  finance-data.tar.gz\n", encoding="ascii")
        (backup / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 4,
                    "created_at": backup.name,
                    "includes": ["actual-data", "cashback-data", "configuration"],
                    "secrets_included": False,
                    "excluded_data": sorted(verifier.EXCLUDED_PUSH_DATA),
                    "excluded_paths": sorted(verifier.EXCLUDED_CASHBACK_PATHS),
                }
            ),
            encoding="utf-8",
        )
        return backup

    def test_verifies_extracted_sqlite_and_json_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = self._backup(root)

            result = verifier.verify_backup(root, backup, None)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["backup"], backup.name)
            self.assertEqual(result["json_documents"], 1)
            self.assertEqual(len(result["sqlite_databases"]), 3)
            self.assertEqual(result["excluded_data"], sorted(verifier.EXCLUDED_PUSH_DATA))
            self.assertEqual(result["excluded_paths"], sorted(verifier.EXCLUDED_CASHBACK_PATHS))

    def test_sanitized_backup_excludes_push_credentials_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = self._backup(root, push_data=True, sanitize_push=True)

            result = verifier.verify_backup(root, backup, None)

            self.assertEqual(result["status"], "ok")
            with tarfile.open(backup / "finance-data.tar.gz", "r:gz") as archive:
                database = archive.extractfile("cashback-data/cashback-events.sqlite3")
                self.assertIsNotNone(database)
                contents = database.read() if database is not None else b""
            self.assertNotIn(b"secret-p256dh-value", contents)
            self.assertNotIn(b"secret-auth-value", contents)
            self.assertNotIn(b"https://push.example/private-endpoint", contents)

    def test_rejects_push_sentinels_in_any_sqlite_archive_member(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = self._backup(root, extra_push_database=True)

            with tarfile.open(backup / "finance-data.tar.gz", "r:gz") as archive:
                database = archive.extractfile("cashback-data/auxiliary.sqlite3")
                self.assertIsNotNone(database)
                contents = database.read() if database is not None else b""
            for sentinel in (
                b"https://push.example/private-endpoint",
                b"secret-p256dh-value",
                b"secret-auth-value",
            ):
                self.assertIn(sentinel, contents)

            with self.assertRaisesRegex(verifier.VerificationError, "excluded push state"):
                verifier.verify_backup(root, backup, None)

    def test_rejects_historical_snapshot_with_push_sentinels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = self._backup(root, historical_push_snapshot=True)

            with tarfile.open(backup / "finance-data.tar.gz", "r:gz") as archive:
                database = archive.extractfile(
                    "cashback-data/pre-deploy-20260818T010203Z.sqlite3"
                )
                self.assertIsNotNone(database)
                contents = database.read() if database is not None else b""
            for sentinel in (
                b"https://push.example/private-endpoint",
                b"secret-p256dh-value",
                b"secret-auth-value",
            ):
                self.assertIn(sentinel, contents)

            with self.assertRaisesRegex(verifier.VerificationError, "historical cashback snapshot"):
                verifier.verify_backup(root, backup, None)

    def test_rejects_backup_that_claims_push_exclusion_without_scrubbing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = self._backup(root, push_data=True)

            with self.assertRaisesRegex(verifier.VerificationError, "excluded push state"):
                verifier.verify_backup(root, backup, None)

    def test_rejects_unclassified_legacy_v3_backup_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = self._backup(root)
            manifest_path = backup / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["schema_version"] = 3
            manifest.pop("excluded_data")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(verifier.VerificationError, "legacy v3"):
                verifier.verify_backup(root, backup, None)

    def test_rejects_checksum_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = self._backup(root)
            (backup / "finance-data.tar.gz").write_bytes(b"damaged")

            with self.assertRaisesRegex(verifier.VerificationError, "checksum mismatch"):
                verifier.verify_backup(root, backup, None)

    def test_rejects_archive_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = self._backup(root, unsafe_member="../escape.txt")

            with self.assertRaisesRegex(verifier.VerificationError, "unsafe archive member"):
                verifier.verify_backup(root, backup, None)


if __name__ == "__main__":
    unittest.main()
