from __future__ import annotations

import importlib.util
import json
import os
import shutil
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
        source["finance_source_cursors"] = [{"source_code": "disposable-test-source"}]
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
            rows = migration.target_tables[name]
            row_strings = sorted(
                json.dumps(self.runner._canonical(row), ensure_ascii=False, separators=(",", ":"))
                for row in rows
            )
            table = {
                "name": name,
                "table_id_sha256": self.runner._digest_json_without_newline(name),
                "schema": schema,
                "schema_sha256": self.runner._digest_json_without_newline(schema),
                "row_count": len(rows),
                "rows_sha256": self.runner._digest_json_without_newline(row_strings),
            }
            table["digest_sha256"] = self.runner._digest_json_without_newline(table)
            tables.append(table)
        readback = {
            "schema_version": 1,
            "receipt_contract": "finance-data-table-readback-receipt-v1",
            "status": "VERIFIED",
            "phase": "FORWARD_POST",
            "scope": "READ_ONLY_IN_MEMORY_FINANCE_DATA_TABLE_DIGEST",
            "finance_tables": 4,
            "tables": tables,
            "total_rows": sum(table["row_count"] for table in tables),
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
        rollback_readback = dict(readback)
        rollback_readback["phase"] = "ROLLBACK"
        raw_rollback = temp / "readback-rollback.raw"
        raw_rollback.write_text(
            "finance data table digest verified:" + json.dumps(rollback_readback) + "\n",
            encoding="utf-8",
        )
        raw_pre = temp / "readback-pre.raw"
        raw_pre.write_text(
            "finance data table digest verified:"
            + json.dumps(
                {
                    "status": "FORWARD_PRE_READBACK",
                    "phase": "FORWARD_PRE",
                    "finance_tables": 0,
                    "tables": [],
                    "total_rows": 0,
                    "digest_sha256": self.runner._digest_json_without_newline([]),
                    "migration_receipt": {"bound": True, "sha256": receipt_sha},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        workflow_root = temp / "workflows"
        workflow_root.mkdir()
        (workflow_root / "cutover.json").write_text(
            json.dumps({"tables": list(self.runner.TARGETS)}) + "\n", encoding="utf-8"
        )
        # The unit fixture is a disposable checkout; production code still
        # binds the workflow root to the real checkout path.
        self.runner.WORKFLOW_ROOT = workflow_root
        identity = {
            "schema_version": "finance-four-table-accepted-identity-v1",
            "repository_root": str(ROOT),
            "workflow_root": str(workflow_root),
            "source_head": self.source_head,
            "generator_head": self.generator_head,
            "clean_checkout": True,
            "legacy_references": [],
        }
        identity["identity_sha256"] = self.runner.hashlib.sha256(
            self.runner._canonical_bytes(identity)
        ).hexdigest()
        identity_path = migration_path.with_name("finance-four-table-accepted-identity.json")
        identity_path.write_bytes(self.runner._canonical_bytes(identity))
        os.chmod(identity_path, 0o600)
        return source_path, migration_path, receipt_sha, workflow_root, raw_readback, raw_pre, raw_rollback

    def test_forward_binds_receipt_heads_and_proves_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root, raw_readback, raw_pre, _raw_rollback = self._fixture(temp)
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
                str(raw_pre),
                "--post-readback-raw",
                str(raw_readback),
                "--second-post-readback-raw",
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
            source, migration, receipt_sha, workflow_root, raw_readback, raw_pre, _raw_rollback = self._fixture(temp)
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
                str(raw_pre),
                "--post-readback-raw",
                str(raw_readback),
                "--second-post-readback-raw",
                str(raw_readback),
                "--output",
                str(temp / "forward.json"),
            ]
            self.assertEqual(self.runner.main(args), 1)

    def test_rollback_requires_forward_receipt_and_restores_exact_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root, raw_readback, raw_pre, raw_rollback = self._fixture(temp)
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
                str(raw_pre),
                "--post-readback-raw",
                str(raw_readback),
                "--second-post-readback-raw",
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
            second_index = rollback_common.index("--second-post-readback-raw")
            del rollback_common[second_index : second_index + 2]
            rollback_common[rollback_common.index(str(raw_pre))] = str(raw_rollback)
            rollback_common[rollback_common.index(str(raw_readback))] = str(raw_rollback)
            rollback_common[-1] = str(rollback)
            runtime_proof = temp / "runtime-proof.json"
            rollback_args = [
                "rollback",
                *rollback_common,
                "--forward-receipt",
                str(forward),
                "--runtime-proof",
                str(runtime_proof),
            ]
            rehearsal_args = [
                "rollback-rehearsal",
                "--source-backup", str(source),
                "--migration-receipt", str(migration),
                "--migration-receipt-sha256", receipt_sha,
                "--repository-root", str(ROOT),
                "--operator-ack", self.runner.REQUIRED_ROLLBACK_ACK,
                "--runtime-action", self.runner.ROLLBACK_RUNTIME_ACTION,
                "--workflow-root", str(workflow_root),
                "--output", str(runtime_proof),
            ]
            self.assertEqual(self.runner.main(rehearsal_args), 0)
            self.assertEqual(self.runner.main(rollback_args), 0)
            result = json.loads(rollback.read_text(encoding="utf-8"))
            Draft202012Validator(self.schema).validate(result)
            self.assertTrue(result["restore_roundtrip"])
            self.assertEqual(result["source_digest"], result["restored_source_digest"])
            self.assertTrue(result["pre_delete"])

    def test_receipt_must_be_mode_six_hundred(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root, raw_readback, raw_pre, _raw_rollback = self._fixture(temp)
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
                str(raw_pre),
                "--post-readback-raw",
                str(raw_readback),
                "--second-post-readback-raw",
                str(raw_readback),
                "--output",
                str(temp / "forward.json"),
            ]
            self.assertEqual(self.runner.main(args), 1)

    def test_rollback_rejects_tampered_forward_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root, raw_readback, raw_pre, raw_rollback = self._fixture(temp)
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
                str(raw_pre),
                "--post-readback-raw",
                str(raw_readback),
                "--second-post-readback-raw",
                str(raw_readback),
                "--output",
                str(forward),
            ]
            self.assertEqual(self.runner.main(["forward", *common]), 0)
            tampered = json.loads(forward.read_text(encoding="utf-8"))
            tampered["target_digest"] = "f" * 64
            forward.write_bytes(self.runner._canonical_bytes(tampered))
            os.chmod(forward, 0o600)
            rollback_common = common.copy()
            second_index = rollback_common.index("--second-post-readback-raw")
            del rollback_common[second_index : second_index + 2]
            rollback_common[rollback_common.index(str(raw_pre))] = str(raw_rollback)
            rollback_common[rollback_common.index(str(raw_readback))] = str(raw_rollback)
            rollback_common[-1] = str(temp / "rollback.json")
            rollback_args = [
                "rollback",
                *rollback_common,
                "--forward-receipt",
                str(forward),
                "--runtime-proof",
                str(temp / "missing-proof.json"),
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
            "--second-post-readback-raw",
            "rollback-rehearsal",
            "--runtime-proof",
            "finance-four-table-accepted-identity.json",
            "FINANCE_DATA_TABLE_READBACK_PHASE",
            "FINANCE_N8N_RUNTIME_MODE",
        ):
            self.assertIn(command, script)

    def test_shell_disposable_forward_call_order(self):
        """The dual CLI reaches runtime twice, then rolls back successfully."""
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, _receipt_sha, _workflow_root, raw_readback, raw_pre, raw_rollback = self._fixture(temp)
            checkout = temp / "checkout"
            for relative in (
                "integrations/n8n/generate_data_table_migration.py",
                "integrations/n8n/data-table-migration-matrix.json",
                "integrations/n8n/data-tables.json",
                "integrations/n8n/setup-workflows/runner/four_table_cutover.py",
                "integrations/n8n/setup-workflows/runner/n8n-cli-finance-data-table-digest.cjs",
                "integrations/n8n/setup-workflows/runner/parse_n8n_redacted_wrapper_output.py",
                "integrations/n8n/setup-workflows/runner/run-four-table-cutover.sh",
            ):
                destination = checkout / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, destination)
            (checkout / "integrations/n8n/workflows").mkdir(parents=True)
            subprocess.run(["git", "-C", str(checkout), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(checkout), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(checkout), "config", "user.name", "Disposable Test"], check=True)
            subprocess.run(["git", "-C", str(checkout), "add", "integrations"], check=True)
            subprocess.run(["git", "-C", str(checkout), "commit", "-qm", "disposable checkout"], check=True)
            checkout_head = subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
            ).stdout.strip()
            receipt_dir = temp / "receipts"
            receipt_dir.mkdir()
            shutil.copy2(source, receipt_dir / "finance-data-table-backup-v1.json")
            shutil.copy2(migration, receipt_dir / "data-table-migration-receipt.json")
            os.chmod(receipt_dir / "data-table-migration-receipt.json", 0o600)
            identity = {
                "schema_version": "finance-four-table-accepted-identity-v1",
                "repository_root": str(checkout),
                "workflow_root": str(checkout / "integrations/n8n/workflows"),
                "source_head": checkout_head,
                "generator_head": self.generator_head,
                "clean_checkout": True,
                "legacy_references": [],
            }
            identity["identity_sha256"] = self.runner.hashlib.sha256(
                self.runner._canonical_bytes(identity)
            ).hexdigest()
            identity_path = receipt_dir / "finance-four-table-accepted-identity.json"
            identity_path.write_bytes(self.runner._canonical_bytes(identity))
            os.chmod(identity_path, 0o600)
            (receipt_dir / "forward.raw").write_text(raw_readback.read_text(encoding="utf-8"), encoding="utf-8")
            (receipt_dir / "pre.raw").write_text(raw_pre.read_text(encoding="utf-8"), encoding="utf-8")
            (receipt_dir / "rollback.raw").write_text(raw_rollback.read_text(encoding="utf-8"), encoding="utf-8")
            log = temp / "docker.log"
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                f"log={log}\n"
                "echo CALL:$* >> \"$log\"\n"
                "if [[ \"$*\" == *' execute --id '* ]]; then echo EXECUTE >> \"$log\"; exit 0; fi\n"
                "count=$(grep -c '^READ' \"$log\" 2>/dev/null || true)\n"
                "echo READ >> \"$log\"\n"
                "if [[ \"$*\" == *'FINANCE_DATA_TABLE_READBACK_PHASE=ROLLBACK'* ]]; then cat \"$FINANCE_TEST_ROLLBACK\"; "
                "elif [[ \"$count\" = 0 ]]; then cat \"$FINANCE_TEST_PRE\"; else cat \"$FINANCE_TEST_POST\"; fi\n",
                encoding="utf-8",
            )
            os.chmod(fake_docker, 0o700)
            environment = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "FINANCE_REPOSITORY_DIR": str(checkout),
                "FINANCE_N8N_RECEIPT_DIR": str(receipt_dir),
                "FINANCE_N8N_CONTAINER": "disposable-finance",
                "N8N_FINANCE_PROJECT_ID": "finance-test-project",
                "FINANCE_N8N_RUNTIME_MODE": "DISPOSABLE_ONLY",
                "FOUR_TABLE_FORWARD_ACK": self.runner.REQUIRED_FORWARD_ACK,
                "FINANCE_TEST_PRE": str(receipt_dir / "pre.raw"),
                "FINANCE_TEST_POST": str(receipt_dir / "forward.raw"),
                "FINANCE_TEST_ROLLBACK": str(receipt_dir / "rollback.raw"),
            }
            completed = subprocess.run(
                ["bash", str(checkout / "integrations/n8n/setup-workflows/runner/run-four-table-cutover.sh"), "forward"],
                cwd=checkout,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + " log=" + log.read_text(encoding="utf-8"))
            self.assertEqual(
                log.read_text(encoding="utf-8").splitlines(),
                ["READ", "EXECUTE", "READ", "EXECUTE", "READ"],
                log.read_text(encoding="utf-8"),
            )
            environment["FOUR_TABLE_FORWARD_ACK"] = ""
            environment["FOUR_TABLE_ROLLBACK_ACK"] = self.runner.REQUIRED_ROLLBACK_ACK
            rollback_completed = subprocess.run(
                ["bash", str(checkout / "integrations/n8n/setup-workflows/runner/run-four-table-cutover.sh"), "rollback"],
                cwd=checkout,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rollback_completed.returncode, 0, rollback_completed.stderr)
            self.assertTrue((receipt_dir / "finance-data-table-rollback-runtime-proof.json").exists())
            self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["READ", "EXECUTE", "READ", "EXECUTE", "READ", "READ", "READ"])


if __name__ == "__main__":
    unittest.main()
