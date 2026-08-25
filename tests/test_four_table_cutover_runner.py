from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "integrations/n8n/setup-workflows/runner/four_table_cutover.py"
MIGRATION_PATH = ROOT / "integrations/n8n/generate_data_table_migration.py"
SCHEMA_PATH = ROOT / "integrations/n8n/schemas/finance-four-table-cutover-receipt-v1.schema.json"
SHELL_RUNNER_PATH = ROOT / "integrations/n8n/setup-workflows/runner/run-four-table-cutover.sh"


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
        cls.source_head = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        cls.generator_head = cls.migration.generated_target_schema_digest()

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
        matrix = json.loads(
            (ROOT / "integrations/n8n/data-table-migration-matrix.json").read_text(
                encoding="utf-8"
            )
        )
        tables = []
        for name in sorted(self.runner.TARGETS):
            schema = [
                {"name": field, "type": spec["type"]}
                for field, spec in sorted(matrix["target_schemas"][name]["columns"].items())
            ]
            table = {
                "name": name,
                "table_id_sha256": self.runner._digest_json_without_newline(name),
                "schema": schema,
                "schema_sha256": self.runner._digest_json_without_newline(schema),
                "row_count": 0,
                "rows_sha256": self.runner._digest_json_without_newline([]),
            }
            table["digest_sha256"] = self.runner._digest_json_without_newline(table)
            tables.append(table)
        readback = {
            "schema_version": 1,
            "receipt_contract": "finance-data-table-readback-receipt-v1",
            "status": "VERIFIED",
            "scope": "READ_ONLY_IN_MEMORY_FINANCE_DATA_TABLE_DIGEST",
            "finance_tables": 4,
            "tables": tables,
            "total_rows": 0,
            "digest_sha256": self.runner._digest_json_without_newline(tables),
            "migration_receipt": {
                "schema_version": "data-table-migration-receipt-v1",
                "required": True,
                "bound": True,
                "sha256": receipt_sha,
            },
            "forward_gate": {
                "gate": "FORWARD",
                "status": "BLOCKED",
                "required_ack": self.runner.REQUIRED_FORWARD_ACK,
                "migration_receipt_required": True,
                "command_executed": False,
            },
            "rollback_gate": {
                "gate": "ROLLBACK",
                "status": "BLOCKED",
                "required_ack": self.runner.REQUIRED_ROLLBACK_ACK,
                "migration_receipt_required": True,
                "command_executed": False,
            },
            "writes_performed": False,
            "provider_calls": False,
            "row_values_recorded": False,
            "secret_values_recorded": False,
        }
        raw_readback = temp / "readback.raw"
        raw_readback.write_text(
            "finance data table digest verified:" + json.dumps(readback) + "\n",
            encoding="utf-8",
        )
        workflow_root = temp / "workflows"
        workflow_root.mkdir()
        (workflow_root / "cutover.json").write_text(
            json.dumps({"tables": list(self.runner.TARGETS)}) + "\n", encoding="utf-8"
        )
        return source_path, migration_path, receipt_sha, workflow_root, raw_readback

    def test_forward_binds_receipt_heads_and_proves_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root, raw_readback = self._fixture(temp)
            output = temp / "forward.json"
            args = [
                "forward",
                "--source-backup",
                str(source),
                "--migration-receipt",
                str(migration),
                "--migration-receipt-sha256",
                receipt_sha,
                "--repository-root",
                str(ROOT),
                "--operator-ack",
                self.runner.REQUIRED_FORWARD_ACK,
                "--runtime-action",
                self.runner.FORWARD_RUNTIME_ACTION,
                "--workflow-root",
                str(workflow_root),
                "--pre-readback-raw",
                str(raw_readback),
                "--post-readback-raw",
                str(raw_readback),
                "--output",
                str(output),
            ]
            self.assertEqual(self.runner.main(args), 0)
            result = json.loads(output.read_text(encoding="utf-8"))
            Draft202012Validator(self.schema).validate(result)
            self.assertEqual(result["operation"], "FORWARD")
            self.assertEqual(result["migration_receipt_sha256"], receipt_sha)
            self.assertEqual(result["source_head"], self.source_head)
            self.assertEqual(result["generator_head"], self.generator_head)
            self.assertTrue(result["reference_rewrite"]["verified"])
            self.assertTrue(result["second_run_noop"])
            self.assertEqual([row["name"] for row in result["target_tables"]], sorted(self.runner.TARGETS))
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            missing_evidence = dict(result)
            missing_evidence.pop("post_readback")
            self.assertTrue(list(Draft202012Validator(self.schema).iter_errors(missing_evidence)))
            arbitrary_ack = dict(result)
            arbitrary_ack["operator_ack"] = "operator-approved"
            self.assertTrue(list(Draft202012Validator(self.schema).iter_errors(arbitrary_ack)))

    def test_forward_rejects_legacy_references(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root, raw_readback = self._fixture(temp)
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
                "--repository-root",
                str(ROOT),
                "--operator-ack",
                self.runner.REQUIRED_FORWARD_ACK,
                "--runtime-action",
                self.runner.FORWARD_RUNTIME_ACTION,
                "--workflow-root",
                str(workflow_root),
                "--pre-readback-raw",
                str(raw_readback),
                "--post-readback-raw",
                str(raw_readback),
                "--output",
                str(temp / "forward.json"),
            ]
            self.assertEqual(self.runner.main(args), 1)

    def test_rollback_requires_forward_receipt_and_restores_exact_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root, raw_readback = self._fixture(temp)
            forward = temp / "forward.json"
            common = [
                "--source-backup",
                str(source),
                "--migration-receipt",
                str(migration),
                "--migration-receipt-sha256",
                receipt_sha,
                "--repository-root",
                str(ROOT),
                "--operator-ack",
                self.runner.REQUIRED_FORWARD_ACK,
                "--runtime-action",
                self.runner.FORWARD_RUNTIME_ACTION,
                "--workflow-root",
                str(workflow_root),
                "--pre-readback-raw",
                str(raw_readback),
                "--post-readback-raw",
                str(raw_readback),
                "--output",
                str(forward),
            ]
            self.assertEqual(self.runner.main(["forward", *common]), 0)
            rollback = temp / "rollback.json"
            rollback_common = common.copy()
            rollback_common[
                rollback_common.index(self.runner.REQUIRED_FORWARD_ACK)
            ] = self.runner.REQUIRED_ROLLBACK_ACK
            rollback_common[
                rollback_common.index(self.runner.FORWARD_RUNTIME_ACTION)
            ] = self.runner.ROLLBACK_RUNTIME_ACTION
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
            source, migration, receipt_sha, workflow_root, raw_readback = self._fixture(temp)
            os.chmod(migration, 0o644)
            args = [
                "forward",
                "--source-backup",
                str(source),
                "--migration-receipt",
                str(migration),
                "--migration-receipt-sha256",
                receipt_sha,
                "--repository-root",
                str(ROOT),
                "--operator-ack",
                self.runner.REQUIRED_FORWARD_ACK,
                "--runtime-action",
                self.runner.FORWARD_RUNTIME_ACTION,
                "--workflow-root",
                str(workflow_root),
                "--pre-readback-raw",
                str(raw_readback),
                "--post-readback-raw",
                str(raw_readback),
                "--output",
                str(temp / "forward.json"),
            ]
            self.assertEqual(self.runner.main(args), 1)

    def test_rollback_rejects_tampered_forward_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root, raw_readback = self._fixture(temp)
            forward = temp / "forward.json"
            common = [
                "--source-backup",
                str(source),
                "--migration-receipt",
                str(migration),
                "--migration-receipt-sha256",
                receipt_sha,
                "--repository-root",
                str(ROOT),
                "--operator-ack",
                self.runner.REQUIRED_FORWARD_ACK,
                "--runtime-action",
                self.runner.FORWARD_RUNTIME_ACTION,
                "--workflow-root",
                str(workflow_root),
                "--pre-readback-raw",
                str(raw_readback),
                "--post-readback-raw",
                str(raw_readback),
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
            rollback_args[rollback_args.index(self.runner.FORWARD_RUNTIME_ACTION)] = (
                self.runner.ROLLBACK_RUNTIME_ACTION
            )
            self.assertEqual(self.runner.main(rollback_args), 1)

    def test_shell_surface_locks_forward_and_rollback_commands(self):
        script = SHELL_RUNNER_PATH.read_text(encoding="utf-8")
        for command in (
            "forward|rollback",
            "FOUR_TABLE_FORWARD_REQUIRES_NAMED_OPERATOR_GATE",
            "FOUR_TABLE_ROLLBACK_REQUIRES_NAMED_OPERATOR_GATE",
            "n8n execute --id 10000000-0000-4000-8000-000000000019",
            "--pre-readback-raw",
            "--post-readback-raw",
            "FINANCE_N8N_RUNTIME_MODE",
        ):
            self.assertIn(command, script)


if __name__ == "__main__":
    unittest.main()
