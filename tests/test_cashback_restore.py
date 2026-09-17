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

from finance_tracker.web_push import WebPushStore

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
    cashback_database = source / "cashback-data/cashback-events.sqlite3"
    sqlite_database(cashback_database)
    WebPushStore(cashback_database)
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


def fake_runtime(root: Path, *, inspect_fault: bool) -> Path:
    runtime = root / ("fake-runtime-fault" if inspect_fault else "fake-runtime")
    mode = "fault" if inspect_fault else "success"
    runtime.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib\n"
        "import sqlite3\n"
        "import sys\n"
        f"root = pathlib.Path({str(root)!r})\n"
        f"mode = {mode!r}\n"
        "command = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "marker = root / 'retained-marker'\n"
        "db_marker = root / 'runtime-db-path'\n"
        "def database_path():\n"
        "    if '-v' in sys.argv:\n"
        "        path = pathlib.Path(sys.argv[sys.argv.index('-v') + 1].split(':', 1)[0]) / 'cashback-events.sqlite3'\n"
        "        db_marker.write_text(str(path), encoding='utf-8')\n"
        "        return path\n"
        "    return pathlib.Path(db_marker.read_text(encoding='utf-8')) if db_marker.exists() else None\n"
        "def emulate_startup():\n"
        "    path = database_path()\n"
        "    if path is not None:\n"
        "        with sqlite3.connect(path) as connection:\n"
        "            connection.execute(\"INSERT OR REPLACE INTO push_state (state_key, state_value, updated_at) VALUES ('routing-map', '{}', CURRENT_TIMESTAMP)\")\n"
        "        writes = root / 'push-state-writes'\n"
        "        writes.write_text(str(int(writes.read_text()) + 1) if writes.exists() else '1', encoding='utf-8')\n"
        "if command == 'version':\n"
        "    raise SystemExit(0)\n"
        "if command == 'image' and len(sys.argv) > 2 and sys.argv[2] == 'inspect':\n"
        "    print('sha256:' + 'a' * 64)\n"
        "    raise SystemExit(0)\n"
        "if command == 'run':\n"
        "    marker.write_text('container-retained', encoding='utf-8')\n"
        "    emulate_startup()\n"
        "    raise SystemExit(0)\n"
        "if command == 'restart':\n"
        "    emulate_startup()\n"
        "    raise SystemExit(0)\n"
        "if command == 'exec':\n"
        "    raise SystemExit(0)\n"
        "if command == 'inspect':\n"
        "    if mode == 'fault':\n"
        "        print('runtime transport failure', file=sys.stderr)\n"
        "        raise SystemExit(1)\n"
        "    if marker.exists():\n"
        "        raise SystemExit(0)\n"
        "    print('Error: no such container', file=sys.stderr)\n"
        "    raise SystemExit(1)\n"
        "if command == 'rm':\n"
        "    (root / 'rm-called').write_text('called', encoding='utf-8')\n"
        "    marker.unlink(missing_ok=True)\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    runtime.chmod(0o700)
    return runtime


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
            'table in {"push_subscriptions", "push_deliveries"}',
            'str(row[0]) != "routing-map"',
            "image inspect --format '{{.Id}}'",
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

    def test_runtime_inspect_fault_fails_closed_and_never_claims_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            runtime = fake_runtime(root, inspect_fault=True)
            receipt = root / "fault.json"
            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--backup-root",
                    str(root),
                    "--receipt",
                    str(receipt),
                    "--runtime",
                    str(runtime),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertTrue((root / "retained-marker").is_file())
            self.assertFalse((root / "rm-called").exists())
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["error"]["code"], "cleanup_not_verified")
            self.assertFalse(payload["cleanup_verified"])
            self.assertFalse(payload["runs"][0]["cleanup_verified"])

    def test_receipt_write_failure_is_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            runtime = fake_runtime(root, inspect_fault=False)
            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--backup-root",
                    str(root),
                    "--receipt",
                    "/dev/null/cb5-receipt.json",
                    "--runtime",
                    str(runtime),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("restore_receipt_write_failed", result.stderr)
            self.assertTrue((root / "rm-called").is_file())
            self.assertFalse((root / "retained-marker").exists())

    def test_happy_path_runs_twice_and_cleans_each_exact_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            runtime = fake_runtime(root, inspect_fault=False)
            receipt = root / "passed.json"
            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--backup-root",
                    str(root),
                    "--receipt",
                    str(receipt),
                    "--runtime",
                    str(runtime),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
            self.assertEqual(list(Draft202012Validator(schema).iter_errors(payload)), [])
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["requested_runs"], 2)
            self.assertEqual(len(payload["runs"]), 2)
            self.assertEqual({run["status"] for run in payload["runs"]}, {"passed"})
            self.assertEqual(len({run["sidecar_id"] for run in payload["runs"]}), 2)
            for run in payload["runs"]:
                self.assertEqual(run["pre_state_sha256"], run["post_state_sha256"])
                self.assertTrue(run["exact_state_match"])
                self.assertTrue(run["health_authorized"])
                self.assertTrue(run["restart_verified"])
                self.assertTrue(run["cleanup_verified"])
            self.assertTrue((root / "rm-called").is_file())
            self.assertFalse((root / "retained-marker").exists())
            self.assertEqual((root / "push-state-writes").read_text(encoding="utf-8"), "4")
            self.assertTrue(payload["cleanup_verified"])

    def test_repeat_requires_two_and_passed_schema_requires_all_invariants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = root / "repeat-one.json"
            result = subprocess.run(
                [str(SCRIPT), "--repeat", "1", "--receipt", str(receipt)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 64)
            self.assertFalse(receipt.exists())

        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        run = {
            "run_index": 1,
            "sidecar_id": "sidecar-1",
            "pre_state_sha256": "a" * 64,
            "post_state_sha256": "a" * 64,
            "exact_state_match": True,
            "health_authorized": True,
            "restart_verified": True,
            "cleanup_verified": True,
            "status": "passed",
        }
        receipt = {
            "schema_version": 1,
            "status": "passed",
            "mode": "disposable",
            "redacted": True,
            "started_at": "2026-08-20T12:00:00Z",
            "completed_at": "2026-08-20T12:00:01Z",
            "backup": {
                "name": "20260820T120000Z",
                "archive_sha256": "b" * 64,
                "archive_bytes": 1,
                "verified": True,
            },
            "runtime": {
                "engine": "podman",
                "image": "cashback:test",
                "image_digest": "sha256:" + "a" * 64,
                "available": True,
                "verified": True,
            },
            "source_provenance": {
                "commit": "c" * 40,
                "script_sha256": "d" * 64,
            },
            "requested_runs": 2,
            "runs": [run, {**run, "run_index": 2, "sidecar_id": "sidecar-2"}],
            "cleanup_verified": True,
            "production_mutated": False,
            "retained_mutated": False,
            "secret_values_recorded": False,
            "error": None,
        }
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(receipt)), [])
        receipt["requested_runs"] = 1
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(receipt)))
        receipt["requested_runs"] = 2
        receipt["runs"][0]["restart_verified"] = False
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(receipt)))


if __name__ == "__main__":
    unittest.main()
