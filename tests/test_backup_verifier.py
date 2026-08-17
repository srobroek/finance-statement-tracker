import hashlib
import importlib.util
import io
import json
import sqlite3
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

    def _backup(self, root: Path, *, unsafe_member: str | None = None) -> Path:
        source = root / "source"
        self._sqlite(source / "actual-data/server-files/account.sqlite")
        self._sqlite(source / "actual-data/user-files/budget.sqlite")
        self._sqlite(source / "cashback-data/cashback-events.sqlite3")
        (source / "configuration").mkdir(parents=True)
        for name in ("actual-compose.yaml", "cashback-compose.yaml", "ingestion-compose.yaml"):
            (source / "configuration" / name).write_text("services: {}\n", encoding="utf-8")
        (source / "configuration/profile.json").write_text('{"schema_version": 1}\n', encoding="utf-8")
        (source / "ingestion-data/jobs/job-1").mkdir(parents=True)
        (source / "ingestion-data/jobs/job-1/request.json").write_text('{"type": "test"}\n', encoding="utf-8")

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
                    "schema_version": 2,
                    "created_at": backup.name,
                    "includes": ["actual-data", "cashback-data", "ingestion-data", "configuration"],
                    "secrets_included": False,
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
            self.assertEqual(result["json_documents"], 2)
            self.assertEqual(len(result["sqlite_databases"]), 3)

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
