from __future__ import annotations

import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "integrations/n8n/setup-workflows/runner/four_table_cutover.py"
MIGRATION_PATH = ROOT / "integrations/n8n/generate_data_table_migration.py"
SCHEMA_PATH = ROOT / "integrations/n8n/schemas/finance-four-table-cutover-receipt-v1.schema.json"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FourTableCutoverRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = _load("finance_four_table_cutover", RUNNER_PATH)
        cls.migration = _load("finance_four_table_migration", MIGRATION_PATH)
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def _fixture(self, temp: Path):
        source = {name: [] for name in self.runner._legacy_names()}
        migration = self.migration.MigrationRunner(source)
        source_path = temp / "finance-data-table-backup-v1.json"
        migration_path = temp / "data-table-migration-receipt.json"
        source_path.write_bytes(self.runner._canonical_bytes(migration.backup_snapshot()))
        first = migration.run()
        migration_path.write_bytes(self.runner._canonical_bytes(first))
        os.chmod(source_path, 0o600)
        os.chmod(migration_path, 0o600)
        receipt_sha = self.runner.hashlib.sha256(migration_path.read_bytes()).hexdigest()
        workflow_root = temp / "workflows"
        workflow_root.mkdir()
        (workflow_root / "cutover.json").write_text(
            json.dumps({"tables": list(self.runner.TARGETS)}) + "\n", encoding="utf-8"
        )
        return source_path, migration_path, receipt_sha, workflow_root

    def test_forward_binds_receipt_heads_and_proves_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root = self._fixture(temp)
            output = temp / "forward.json"
            args = [
                "forward",
                "--source-backup",
                str(source),
                "--migration-receipt",
                str(migration),
                "--migration-receipt-sha256",
                receipt_sha,
                "--source-head",
                "a" * 40,
                "--generator-head",
                "b" * 64,
                "--operator-ack",
                self.runner.REQUIRED_FORWARD_ACK,
                "--workflow-root",
                str(workflow_root),
                "--output",
                str(output),
            ]
            self.assertEqual(self.runner.main(args), 0)
            result = json.loads(output.read_text(encoding="utf-8"))
            Draft202012Validator(self.schema).validate(result)
            self.assertEqual(result["operation"], "FORWARD")
            self.assertEqual(result["migration_receipt_sha256"], receipt_sha)
            self.assertTrue(result["reference_rewrite"]["verified"])
            self.assertTrue(result["second_run_noop"])
            self.assertEqual([row["name"] for row in result["target_tables"]], sorted(self.runner.TARGETS))
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_forward_rejects_legacy_references(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root = self._fixture(temp)
            (workflow_root / "old.json").write_text(
                '{"table":"finance_source_cursors"}\n', encoding="utf-8"
            )
            args = [
                "forward",
                "--source-backup",
                str(source),
                "--migration-receipt",
                str(migration),
                "--migration-receipt-sha256",
                receipt_sha,
                "--source-head",
                "a" * 40,
                "--generator-head",
                "b" * 64,
                "--operator-ack",
                self.runner.REQUIRED_FORWARD_ACK,
                "--workflow-root",
                str(workflow_root),
                "--output",
                str(temp / "forward.json"),
            ]
            self.assertEqual(self.runner.main(args), 1)

    def test_rollback_requires_forward_receipt_and_restores_exact_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root = self._fixture(temp)
            forward = temp / "forward.json"
            common = [
                "--source-backup",
                str(source),
                "--migration-receipt",
                str(migration),
                "--migration-receipt-sha256",
                receipt_sha,
                "--source-head",
                "a" * 40,
                "--generator-head",
                "b" * 64,
                "--operator-ack",
                self.runner.REQUIRED_FORWARD_ACK,
                "--workflow-root",
                str(workflow_root),
                "--output",
                str(forward),
            ]
            self.assertEqual(self.runner.main(["forward", *common]), 0)
            rollback = temp / "rollback.json"
            rollback_common = common.copy()
            rollback_common[
                rollback_common.index(self.runner.REQUIRED_FORWARD_ACK)
            ] = self.runner.REQUIRED_ROLLBACK_ACK
            rollback_args = [
                "rollback",
                *rollback_common[:-1],
                str(rollback),
                "--forward-receipt",
                str(forward),
            ]
            self.assertEqual(self.runner.main(rollback_args), 0)
            result = json.loads(rollback.read_text(encoding="utf-8"))
            Draft202012Validator(self.schema).validate(result)
            self.assertTrue(result["restore_roundtrip"])
            self.assertEqual(result["source_digest"], result["restored_source_digest"])
            self.assertTrue(result["pre_delete"])

    def test_receipt_must_be_mode_six_hundred(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root = self._fixture(temp)
            os.chmod(migration, 0o644)
            args = [
                "forward",
                "--source-backup",
                str(source),
                "--migration-receipt",
                str(migration),
                "--migration-receipt-sha256",
                receipt_sha,
                "--source-head",
                "a" * 40,
                "--generator-head",
                "b" * 64,
                "--operator-ack",
                self.runner.REQUIRED_FORWARD_ACK,
                "--workflow-root",
                str(workflow_root),
                "--output",
                str(temp / "forward.json"),
            ]
            self.assertEqual(self.runner.main(args), 1)

    def test_rollback_rejects_tampered_forward_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root = self._fixture(temp)
            forward = temp / "forward.json"
            common = [
                "--source-backup",
                str(source),
                "--migration-receipt",
                str(migration),
                "--migration-receipt-sha256",
                receipt_sha,
                "--source-head",
                "a" * 40,
                "--generator-head",
                "b" * 64,
                "--operator-ack",
                self.runner.REQUIRED_FORWARD_ACK,
                "--workflow-root",
                str(workflow_root),
                "--output",
                str(forward),
            ]
            self.assertEqual(self.runner.main(["forward", *common]), 0)
            tampered = json.loads(forward.read_text(encoding="utf-8"))
            tampered["target_digest"] = "f" * 64
            forward.write_bytes(self.runner._canonical_bytes(tampered))
            os.chmod(forward, 0o600)
            rollback_args = [
                "rollback",
                *common[:-1],
                str(temp / "rollback.json"),
                "--forward-receipt",
                str(forward),
            ]
            rollback_args[rollback_args.index(self.runner.REQUIRED_FORWARD_ACK)] = (
                self.runner.REQUIRED_ROLLBACK_ACK
            )
            self.assertEqual(self.runner.main(rollback_args), 1)


if __name__ == "__main__":
    unittest.main()
