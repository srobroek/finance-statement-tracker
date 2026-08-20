from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "deploy/actual-poc/restore-cashback-disposable.sh"
SCHEMA = ROOT / "config/cashback-restore-receipt.schema.json"


def sqlite_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES (1, 'cashback')")


def backup_fixture(root: Path) -> Path:
    source = root / "source"
    sqlite_database(source / "actual-data/server-files/account.sqlite")
    sqlite_database(source / "actual-data/user-files/budget.sqlite")
    sqlite_database(source / "cashback-data/cashback-events.sqlite3")
    configuration = source / "configuration"
    configuration.mkdir(parents=True)
    (configuration / "actual-compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (configuration / "cashback-compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (configuration / "profile.json").write_text('{"schema_version": 1}\n', encoding="utf-8")

    backup = root / "20260820T120000Z"
    backup.mkdir()
    archive = backup / "finance-data.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for path in sorted(source.rglob("*")):
            bundle.add(path, arcname=path.relative_to(source).as_posix(), recursive=False)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (backup / "SHA256SUMS").write_text(f"{digest}  finance-data.tar.gz\n", encoding="ascii")
    (backup / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 4,
                "created_at": backup.name,
                "includes": ["actual-data", "cashback-data", "configuration"],
                "secrets_included": False,
                "excluded_data": [
                    "cashback-data/cashback-events.sqlite3:push_deliveries",
                    "cashback-data/cashback-events.sqlite3:push_state",
                    "cashback-data/cashback-events.sqlite3:push_subscriptions",
                ],
                "excluded_paths": ["cashback-data/pre-deploy-*.sqlite3*"],
            }
        ),
        encoding="utf-8",
    )
    return backup


class CashbackRestoreContractTests(unittest.TestCase):
    def test_script_uses_authoritative_contract_and_exact_disposable_cleanup(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        for required in (
            'python3 "${VERIFY_SCRIPT}"',
            "sha256sum -c SHA256SUMS",
            "--pull=never",
            "--network none",
            "apps/cashback-control/probe_health.py",
            '"${RUNTIME}" restart',
            '"${RUNTIME}" rm -f',
            '"${RUNTIME}" inspect',
            "CASHBACK_INGEST_TOKEN",
            '"secret_values_recorded": False',
        ):
            self.assertIn(required, source)
        self.assertNotIn("docker compose", source)
        self.assertNotIn("finance-runtime/.env", source)

    def test_blocked_runtime_writes_truthful_redacted_mode_0600_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            receipt = root / "artifacts" / "cb5-receipt.json"
            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--backup-root",
                    str(root),
                    "--receipt",
                    str(receipt),
                    "--runtime",
                    str(root / "missing-runtime"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            errors = sorted(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(payload), key=str)
            self.assertEqual(errors, [])
            self.assertEqual(payload["status"], "blocked")
            self.assertEqual(payload["error"]["code"], "container_runtime_unavailable")
            self.assertTrue(payload["backup"]["verified"])
            self.assertFalse(payload["runtime"]["verified"])
            self.assertFalse(payload["secret_values_recorded"])
            self.assertFalse(payload["production_mutated"])
            self.assertFalse(payload["retained_mutated"])
            self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("CASHBACK_INGEST_TOKEN", receipt.read_text(encoding="utf-8"))

    def test_checksum_failure_is_not_reported_as_a_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = backup_fixture(root)
            (backup / "finance-data.tar.gz").write_bytes(b"changed")
            receipt = root / "failure.json"
            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--backup-root",
                    str(root),
                    "--receipt",
                    str(receipt),
                    "--runtime",
                    str(root / "missing-runtime"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["error"]["code"], "backup_verification_failed")
            self.assertFalse(payload["backup"]["verified"])
            self.assertFalse(payload["runtime"]["verified"])


if __name__ == "__main__":
    unittest.main()
