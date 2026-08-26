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
READBACK_PARSER_PATH = ROOT / "integrations/n8n/setup-workflows/runner/parse_n8n_redacted_wrapper_output.py"
RETAINED_READBACK_FIXTURE = ROOT / "tests/fixtures/n8n-2.36.2-data-table-digest-output.json"
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
        cls.readback_parser = _load("finance_readback_parser", READBACK_PARSER_PATH)
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
        rollback_readback["phase"] = "ROLLBACK_POST"
        raw_rollback_post = temp / "readback-rollback-post.raw"
        raw_rollback_post.write_text(
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
                    "migration_receipt": {
                        "schema_version": "data-table-migration-receipt-v1",
                        "required": True,
                        "bound": True,
                        "sha256": receipt_sha,
                    },
                    "schema_version": 1,
                    "receipt_contract": "finance-data-table-readback-receipt-v1",
                    "scope": "READ_ONLY_IN_MEMORY_FINANCE_DATA_TABLE_DIGEST",
                    "forward_gate": readback["forward_gate"],
                    "rollback_gate": readback["rollback_gate"],
                    "writes_performed": False,
                    "provider_calls": False,
                    "row_values_recorded": False,
                    "secret_values_recorded": False,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        rollback_pre = dict(readback)
        rollback_pre["phase"] = "ROLLBACK_PRE"
        rollback_pre_path = temp / "readback-rollback-pre.raw"
        rollback_pre_path.write_text(
            "finance data table digest verified:" + json.dumps(rollback_pre) + "\n",
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
            "migration_receipt_sha256": receipt_sha,
            "source_backup_sha256": self.runner.hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "clean_checkout": True,
            "legacy_references": [],
        }
        identity["identity_sha256"] = self.runner.hashlib.sha256(
            self.runner._canonical_bytes(identity)
        ).hexdigest()
        identity_path = migration_path.with_name("finance-four-table-accepted-identity.json")
        identity_path.write_bytes(self.runner._canonical_bytes(identity))
        os.chmod(identity_path, 0o600)
        matrix = json.loads(
            (ROOT / "integrations/n8n/data-table-migration-matrix.json").read_text(
                encoding="utf-8"
            )
        )
        inventory = self.runner._reference_inventory(matrix)
        schema_digests = self.runner._target_schema_digests(matrix)
        targets = [
            {
                "name": name,
                "table_id": f"live-{index}",
                "schema_sha256": schema_digests[name],
            }
            for index, name in enumerate(sorted(self.runner.TARGETS))
        ]
        target_ids = {target["name"]: target["table_id"] for target in targets}
        live_export = {
            "schema_version": self.runner.LIVE_EXPORT_SCHEMA,
            "project_id": "finance-test-project",
            "repository_root": str(ROOT),
            "source_head": self.source_head,
            "generator_head": self.generator_head,
            "migration_receipt_sha256": identity["migration_receipt_sha256"],
            "source_backup_sha256": identity["source_backup_sha256"],
            "accepted_identity_sha256": identity["identity_sha256"],
            "redacted": True,
            "workflow_count": 19,
            "in_flight": 0,
            "workflows": [
                {
                    "workflow_id": f"live-workflow-{index}",
                    "revision_id": f"live-revision-{index}",
                    "active": False,
                    "published": False,
                    "in_flight": 0,
                }
                for index in range(19)
            ],
            "targets": targets,
            "references": [
                {
                    "reference_id": item["reference_id"],
                    "workflow_id": "live-workflow-0",
                    "revision_id": "live-revision-0",
                    "node_id": f"live-node-{index}",
                    "workflow_path": item["workflow_path"],
                    "node_name": item["node_name"],
                    "operation": item["operation"],
                    "old_table_name": item["source_table"],
                    "old_table_id": f"old-{index}",
                    "canonical_table_name": item["canonical_table_name"],
                    "canonical_table_id": target_ids.get(item["canonical_table_name"]),
                    "active": False,
                    "published": False,
                    "in_flight": 0,
                }
                for index, item in enumerate(inventory)
            ],
        }
        live_export["export_sha256"] = self.runner.hashlib.sha256(
            self.runner._canonical_bytes(live_export)
        ).hexdigest()
        live_export_path = migration_path.with_name(self.runner.LIVE_EXPORT_FILENAME)
        live_export_path.write_bytes(self.runner._canonical_bytes(live_export))
        os.chmod(live_export_path, 0o600)
        lock_receipt = self.runner._lock_receipt(
            export=live_export,
            migration_receipt_sha=identity["migration_receipt_sha256"],
            source_backup_sha=identity["source_backup_sha256"],
            identity_digest=identity["identity_sha256"],
            project_id=live_export["project_id"],
            operation="PRECONDITION",
        )
        lock_receipt_path = migration_path.with_name(self.runner.LOCK_RECEIPT_FILENAME)
        lock_receipt_path.write_bytes(self.runner._canonical_bytes(lock_receipt))
        os.chmod(lock_receipt_path, 0o600)
        return (
            source_path,
            migration_path,
            receipt_sha,
            workflow_root,
            raw_readback,
            raw_pre,
            rollback_pre_path,
            raw_rollback_post,
        )

    def _rewrite_readback(
        self,
        raw: str,
        *,
        phase: str,
        migration_sha: str,
        bound: bool = True,
        remove_sha: bool = False,
    ) -> str:
        prefix = "finance data table digest verified:"
        payload = self.readback_parser.extract_payload(raw, prefix)
        payload["phase"] = phase
        migration_receipt = dict(payload["migration_receipt"])
        migration_receipt["bound"] = bound
        if remove_sha:
            migration_receipt.pop("sha256", None)
        else:
            migration_receipt["sha256"] = migration_sha if bound else None
        payload["migration_receipt"] = migration_receipt
        replacement = prefix + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        return "".join(
            replacement if line.lstrip("\x1b[0123456789; mK").startswith(prefix) else line
            for line in raw.splitlines(keepends=True)
        )

    def test_readback_requires_bound_matching_migration_sha_for_every_phase(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            _source, _migration, receipt_sha, _workflow_root, _raw_readback, raw_pre, _rollback_pre, _raw_rollback = self._fixture(temp)
            retained = json.loads(RETAINED_READBACK_FIXTURE.read_text(encoding="utf-8"))["raw_stdout"]
            phase_inputs = {
                "FORWARD_PRE": raw_pre.read_text(encoding="utf-8"),
                "FORWARD_POST": retained,
                "ROLLBACK_PRE": retained,
                "ROLLBACK_POST": retained,
            }
            for phase, original in phase_inputs.items():
                valid = self._rewrite_readback(
                    original, phase=phase, migration_sha=receipt_sha
                )
                path = temp / f"valid-{phase}.raw"
                path.write_text(valid, encoding="utf-8")
                self.assertTrue(
                    self.runner._parse_readback(path, receipt_sha, phase)["verified"], phase
                )
                for label, mutation in (
                    ("bound-false", {"bound": False}),
                    ("missing-sha", {"remove_sha": True}),
                    ("mismatched-sha", {"migration_sha": "f" * 64}),
                ):
                    mutated = self._rewrite_readback(
                        original,
                        phase=phase,
                        migration_sha=mutation.get("migration_sha", receipt_sha),
                        bound=mutation.get("bound", True),
                        remove_sha=mutation.get("remove_sha", False),
                    )
                    path.write_text(mutated, encoding="utf-8")
                    with self.assertRaisesRegex(
                        self.runner.CutoverError,
                        r"READBACK_(MIGRATION_RECEIPT_MISMATCH|RECEIPT_INVALID)",
                    ):
                        self.runner._parse_readback(path, receipt_sha, phase)

    def test_forward_binds_receipt_heads_and_proves_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root, raw_readback, raw_pre, _rollback_pre, _raw_rollback = self._fixture(temp)
            output = temp / "forward.json"
            args = [
                "forward",
                "--source-backup",
                str(source),
                "--migration-receipt",
                str(migration),
                "--migration-receipt-sha256",
                receipt_sha,
                "--source-backup-sha256",
                self.runner.hashlib.sha256(source.read_bytes()).hexdigest(),
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
                "--runtime-state",
                str(temp / "runtime-state.json"),
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
            source, migration, receipt_sha, workflow_root, raw_readback, raw_pre, _rollback_pre, _raw_rollback = self._fixture(temp)
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
                "--source-backup-sha256",
                self.runner.hashlib.sha256(source.read_bytes()).hexdigest(),
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
                "--runtime-state",
                str(temp / "runtime-state.json"),
                "--output",
                str(temp / "forward.json"),
            ]
            self.assertEqual(self.runner.main(args), 1)

    def test_rollback_requires_forward_receipt_and_restores_exact_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root, raw_readback, raw_pre, raw_rollback_pre, raw_rollback = self._fixture(temp)
            forward = temp / "forward.json"
            common = [
                "--source-backup",
                str(source),
                "--migration-receipt",
                str(migration),
                "--migration-receipt-sha256",
                receipt_sha,
                "--source-backup-sha256",
                self.runner.hashlib.sha256(source.read_bytes()).hexdigest(),
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
                "--runtime-state",
                str(temp / "runtime-state.json"),
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
            rollback_common[rollback_common.index(str(raw_pre))] = str(raw_rollback_pre)
            rollback_common[rollback_common.index(str(raw_readback))] = str(raw_rollback)
            rollback_common[-1] = str(rollback)
            runtime_proof = temp / "runtime-proof.json"
            runtime_state = temp / "runtime-state.json"
            rollback_args = [
                "rollback",
                *rollback_common,
                "--forward-receipt",
                str(forward),
                "--runtime-proof",
                str(runtime_proof),
            ]
            runtime_args = [
                "rollback-runtime",
                "--source-backup", str(source),
                "--migration-receipt", str(migration),
                "--migration-receipt-sha256", receipt_sha,
                "--source-backup-sha256", self.runner.hashlib.sha256(source.read_bytes()).hexdigest(),
                "--repository-root", str(ROOT),
                "--operator-ack", self.runner.REQUIRED_ROLLBACK_ACK,
                "--runtime-action", self.runner.ROLLBACK_RUNTIME_ACTION,
                "--workflow-root", str(workflow_root),
                "--runtime-state", str(runtime_state),
                "--output", str(runtime_proof),
            ]
            self.assertEqual(self.runner.main(runtime_args), 0)
            self.assertEqual(self.runner.main(rollback_args), 0)
            result = json.loads(rollback.read_text(encoding="utf-8"))
            Draft202012Validator(self.schema).validate(result)
            self.assertTrue(result["restore_roundtrip"])
            self.assertEqual(result["source_digest"], result["restored_source_digest"])
            self.assertTrue(result["pre_delete"])

    def test_receipt_must_be_mode_six_hundred(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root, raw_readback, raw_pre, _rollback_pre, _raw_rollback = self._fixture(temp)
            os.chmod(migration, 0o644)
            args = [
                "forward",
                "--source-backup",
                str(source),
                "--migration-receipt",
                str(migration),
                "--migration-receipt-sha256",
                receipt_sha,
                "--source-backup-sha256",
                self.runner.hashlib.sha256(source.read_bytes()).hexdigest(),
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
                "--runtime-state",
                str(temp / "runtime-state.json"),
                "--output",
                str(temp / "forward.json"),
            ]
            self.assertEqual(self.runner.main(args), 1)

    def test_source_backup_must_be_mode_six_hundred(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root, raw_readback, raw_pre, _rollback_pre, _raw_rollback = self._fixture(temp)
            args = [
                "forward",
                "--source-backup", str(source),
                "--migration-receipt", str(migration),
                "--migration-receipt-sha256", receipt_sha,
                "--source-backup-sha256", self.runner.hashlib.sha256(source.read_bytes()).hexdigest(),
                "--repository-root", str(ROOT),
                "--operator-ack", self.runner.REQUIRED_FORWARD_ACK,
                "--runtime-action", self.runner.FORWARD_RUNTIME_ACTION,
                "--workflow-root", str(workflow_root),
                "--pre-readback-raw", str(raw_pre),
                "--post-readback-raw", str(raw_readback),
                "--second-post-readback-raw", str(raw_readback),
                "--runtime-state", str(temp / "runtime-state.json"),
                "--output", str(temp / "forward.json"),
            ]
            for mode in (0o644, 0o400, 0o640):
                os.chmod(source, mode)
                self.assertEqual(self.runner.main(args), 1)

    def test_cutover_rejects_missing_export_or_lock_receipt(self):
        for missing_name in (self.runner.LIVE_EXPORT_FILENAME, self.runner.LOCK_RECEIPT_FILENAME):
            with self.subTest(missing_name=missing_name), tempfile.TemporaryDirectory() as directory:
                temp = Path(directory)
                source, migration, receipt_sha, workflow_root, _raw_readback, _raw_pre, _rollback_pre, _raw_rollback = self._fixture(temp)
                (migration.parent / missing_name).unlink()
                args = [
                    "validate-inputs",
                    "--source-backup", str(source),
                    "--migration-receipt", str(migration),
                    "--migration-receipt-sha256", receipt_sha,
                    "--source-backup-sha256", self.runner.hashlib.sha256(source.read_bytes()).hexdigest(),
                    "--repository-root", str(ROOT),
                    "--operator-ack", self.runner.REQUIRED_FORWARD_ACK,
                    "--runtime-action", self.runner.FORWARD_RUNTIME_ACTION,
                    "--workflow-root", str(workflow_root),
                ]
                self.assertEqual(self.runner.main(args), 1)

    def test_live_export_binds_reference_revision_to_workflow_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root, _raw_readback, _raw_pre, _rollback_pre, _raw_rollback = self._fixture(temp)
            export_path = migration.parent / self.runner.LIVE_EXPORT_FILENAME
            export = json.loads(export_path.read_text(encoding="utf-8"))
            export["references"][0]["revision_id"] = "live-revision-1"
            unsigned = dict(export)
            unsigned.pop("export_sha256", None)
            export["export_sha256"] = self.runner.hashlib.sha256(
                self.runner._canonical_bytes(unsigned)
            ).hexdigest()
            export_path.write_bytes(self.runner._canonical_bytes(export))
            os.chmod(export_path, 0o600)
            args = [
                "validate-inputs",
                "--source-backup", str(source),
                "--migration-receipt", str(migration),
                "--migration-receipt-sha256", receipt_sha,
                "--source-backup-sha256", self.runner.hashlib.sha256(source.read_bytes()).hexdigest(),
                "--repository-root", str(ROOT),
                "--operator-ack", self.runner.REQUIRED_FORWARD_ACK,
                "--runtime-action", self.runner.FORWARD_RUNTIME_ACTION,
                "--workflow-root", str(workflow_root),
            ]
            self.assertEqual(self.runner.main(args), 1)

    def test_lock_receipt_binds_project_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root, _raw_readback, _raw_pre, _rollback_pre, _raw_rollback = self._fixture(temp)
            lock_path = migration.parent / self.runner.LOCK_RECEIPT_FILENAME
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            lock["project_id"] = "other-project"
            unsigned = dict(lock)
            unsigned.pop("lock_receipt_sha256", None)
            lock["lock_receipt_sha256"] = self.runner.hashlib.sha256(
                self.runner._canonical_bytes(unsigned)
            ).hexdigest()
            lock_path.write_bytes(self.runner._canonical_bytes(lock))
            os.chmod(lock_path, 0o600)
            args = [
                "validate-inputs",
                "--source-backup", str(source),
                "--migration-receipt", str(migration),
                "--migration-receipt-sha256", receipt_sha,
                "--source-backup-sha256", self.runner.hashlib.sha256(source.read_bytes()).hexdigest(),
                "--repository-root", str(ROOT),
                "--operator-ack", self.runner.REQUIRED_FORWARD_ACK,
                "--runtime-action", self.runner.FORWARD_RUNTIME_ACTION,
                "--workflow-root", str(workflow_root),
            ]
            self.assertEqual(self.runner.main(args), 1)

    def test_protected_inputs_require_regular_non_symlink_exact_six_hundred_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            protected = temp / "protected.json"
            protected.write_text("{}\n", encoding="utf-8")
            os.chmod(protected, 0o600)
            self.runner._require_protected(protected, "TEST_PROTECTED")

            for mode in (0o400, 0o640):
                os.chmod(protected, mode)
                with self.assertRaisesRegex(
                    self.runner.CutoverError, r"TEST_PROTECTED_MODE_REQUIRED:protected\.json"
                ):
                    self.runner._require_protected(protected, "TEST_PROTECTED")

            replacement = temp / "replacement.json"
            replacement.write_text("{}\n", encoding="utf-8")
            os.chmod(replacement, 0o600)
            protected.unlink()
            protected.symlink_to(replacement)
            with self.assertRaisesRegex(
                self.runner.CutoverError, r"TEST_PROTECTED_MODE_REQUIRED:protected\.json"
            ):
                self.runner._require_protected(protected, "TEST_PROTECTED")

            protected.unlink()
            protected.mkdir()
            os.chmod(protected, 0o600)
            with self.assertRaisesRegex(
                self.runner.CutoverError, r"TEST_PROTECTED_MODE_REQUIRED:protected\.json"
            ):
                self.runner._require_protected(protected, "TEST_PROTECTED")

    def test_forward_rejects_replaced_approved_input(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root, _raw_readback, _raw_pre, _rollback_pre, _raw_rollback = self._fixture(temp)
            source_sha = self.runner.hashlib.sha256(source.read_bytes()).hexdigest()
            source.write_bytes(source.read_bytes() + b" ")
            os.chmod(source, 0o600)
            args = [
                "validate-inputs",
                "--source-backup", str(source),
                "--migration-receipt", str(migration),
                "--migration-receipt-sha256", receipt_sha,
                "--source-backup-sha256", source_sha,
                "--repository-root", str(ROOT),
                "--operator-ack", self.runner.REQUIRED_FORWARD_ACK,
                "--runtime-action", self.runner.FORWARD_RUNTIME_ACTION,
                "--workflow-root", str(workflow_root),
            ]
            self.assertEqual(self.runner.main(args), 1)

            migration.write_bytes(migration.read_bytes() + b" ")
            os.chmod(migration, 0o600)
            args[args.index("--source-backup-sha256") + 1] = self.runner.hashlib.sha256(source.read_bytes()).hexdigest()
            self.assertEqual(self.runner.main(args), 1)

    def test_rollback_rejects_tampered_forward_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, workflow_root, raw_readback, raw_pre, raw_rollback_pre, raw_rollback = self._fixture(temp)
            forward = temp / "forward.json"
            common = [
                "--source-backup",
                str(source),
                "--migration-receipt",
                str(migration),
                "--migration-receipt-sha256",
                receipt_sha,
                "--source-backup-sha256",
                self.runner.hashlib.sha256(source.read_bytes()).hexdigest(),
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
                "--runtime-state",
                str(temp / "runtime-state.json"),
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
            rollback_common[rollback_common.index(str(raw_pre))] = str(raw_rollback_pre)
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
            "rollback-runtime",
            "--runtime-proof",
            "--runtime-state",
            "validate-inputs",
            "--source-backup-sha256",
            "stat -c '%a'",
            "finance-four-table-accepted-identity.json",
            "FINANCE_DATA_TABLE_READBACK_PHASE",
            "FINANCE_N8N_RUNTIME_MODE",
            "parse_n8n_redacted_wrapper_output.py",
            "data-table-receipt",
        ):
            self.assertIn(command, script)

    def test_shell_disposable_forward_call_order(self):
        """The dual CLI reaches runtime twice, then rolls back successfully."""
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, _receipt_sha, _workflow_root, raw_readback, raw_pre, raw_rollback_pre, raw_rollback = self._fixture(temp)
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
                "migration_receipt_sha256": self.runner.hashlib.sha256(
                    (receipt_dir / "data-table-migration-receipt.json").read_bytes()
                ).hexdigest(),
                "source_backup_sha256": self.runner.hashlib.sha256(
                    (receipt_dir / "finance-data-table-backup-v1.json").read_bytes()
                ).hexdigest(),
                "clean_checkout": True,
                "legacy_references": [],
            }
            identity["identity_sha256"] = self.runner.hashlib.sha256(
                self.runner._canonical_bytes(identity)
            ).hexdigest()
            identity_path = receipt_dir / "finance-four-table-accepted-identity.json"
            identity_path.write_bytes(self.runner._canonical_bytes(identity))
            os.chmod(identity_path, 0o600)
            matrix = json.loads(
                (ROOT / "integrations/n8n/data-table-migration-matrix.json").read_text(
                    encoding="utf-8"
                )
            )
            inventory = self.runner._reference_inventory(matrix)
            schema_digests = self.runner._target_schema_digests(matrix)
            targets = [
                {
                    "name": name,
                    "table_id": f"live-{index}",
                    "schema_sha256": schema_digests[name],
                }
                for index, name in enumerate(sorted(self.runner.TARGETS))
            ]
            references = []
            for index, item in enumerate(inventory):
                target = next(
                    (
                        target["table_id"]
                        for target in targets
                        if target["name"] == item["canonical_table_name"]
                    ),
                    None,
                )
                references.append(
                    {
                        "reference_id": item["reference_id"],
                        "workflow_id": "live-workflow-0",
                        "revision_id": "live-revision-0",
                        "node_id": f"live-node-{index}",
                        "workflow_path": item["workflow_path"],
                        "node_name": item["node_name"],
                        "operation": item["operation"],
                        "old_table_name": item["source_table"],
                        "old_table_id": f"old-{index}",
                        "canonical_table_name": item["canonical_table_name"],
                        "canonical_table_id": target,
                        "active": False,
                        "published": False,
                        "in_flight": 0,
                    }
                )
            live_export = {
                "schema_version": self.runner.LIVE_EXPORT_SCHEMA,
                "project_id": "finance-test-project",
                "repository_root": str(checkout),
                "source_head": checkout_head,
                "generator_head": self.generator_head,
                "migration_receipt_sha256": identity["migration_receipt_sha256"],
                "source_backup_sha256": identity["source_backup_sha256"],
                "accepted_identity_sha256": identity["identity_sha256"],
                "redacted": True,
                "workflow_count": 19,
                "in_flight": 0,
                "workflows": [
                    {
                        "workflow_id": f"live-workflow-{index}",
                        "revision_id": f"live-revision-{index}",
                        "active": False,
                        "published": False,
                        "in_flight": 0,
                    }
                    for index in range(19)
                ],
                "targets": targets,
                "references": references,
            }
            live_export["export_sha256"] = self.runner.hashlib.sha256(
                self.runner._canonical_bytes(live_export)
            ).hexdigest()
            live_export_path = receipt_dir / "finance-four-table-live-export.json"
            live_export_path.write_bytes(self.runner._canonical_bytes(live_export))
            os.chmod(live_export_path, 0o600)
            (receipt_dir / "forward.raw").write_text(raw_readback.read_text(encoding="utf-8"), encoding="utf-8")
            (receipt_dir / "pre.raw").write_text(raw_pre.read_text(encoding="utf-8"), encoding="utf-8")
            (receipt_dir / "rollback-pre.raw").write_text(raw_rollback_pre.read_text(encoding="utf-8"), encoding="utf-8")
            (receipt_dir / "rollback-post.raw").write_text(raw_rollback.read_text(encoding="utf-8"), encoding="utf-8")
            log = temp / "docker.log"
            log.touch()
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                f"log={log}\n"
                "if [[ \"$*\" == *' execute --id '* ]]; then echo EXECUTE >> \"$log\"; exit 0; fi\n"
                "count=$(grep -c '^READ' \"$log\" 2>/dev/null || true)\n"
                "echo READ >> \"$log\"\n"
                "printf '\\033[4m>>>> Executing external compose provider \"/usr/local/bin/docker-compose\". Please refer to the documentation for details. <<<<\\n\\n\\033[0mPostgres 16 is outside the supported range and receives compatibility support only. Upgrade to Postgres 17 or newer.\\nAcquiring database migration lock...\\nDeprecation warning: The storage directory \"/home/node/.n8n/binaryData\" will be renamed to \"/home/node/.n8n/storage\" in n8n v3. To migrate now, set N8N_MIGRATE_FS_STORAGE_PATH=true. If you have a volume mounted at the old path, update your mount configuration after migration.\\n'\n"
                "if [[ \"$*\" == *'FINANCE_DATA_TABLE_READBACK_PHASE=ROLLBACK_PRE'* ]]; then cat \"$FINANCE_TEST_ROLLBACK_PRE\"; "
                "elif [[ \"$*\" == *'FINANCE_DATA_TABLE_READBACK_PHASE=ROLLBACK_POST'* ]]; then cat \"$FINANCE_TEST_ROLLBACK_POST\"; "
                "elif [[ \"$count\" = 0 ]]; then cat \"$FINANCE_TEST_PRE\"; else cat \"$FINANCE_TEST_POST\"; fi\n",
                encoding="utf-8",
            )
            os.chmod(fake_docker, 0o700)
            real_python = shutil.which("python3")
            self.assertIsNotNone(real_python)
            fake_python = fake_bin / "python3"
            fake_python.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                f"log={log}\n"
                "if [[ \"$*\" == *'four_table_cutover.py forward '* ]]; then echo \"PY_FORWARD $*\" >> \"$log\"; fi\n"
                "if [[ \"$*\" == *'four_table_cutover.py rollback-runtime '* ]]; then echo \"PY_ROLLBACK_RUNTIME $*\" >> \"$log\"; fi\n"
                "if [[ \"$*\" == *'four_table_cutover.py rollback '* ]]; then echo \"PY_ROLLBACK_FINAL $*\" >> \"$log\"; fi\n"
                "exec \"$REAL_PYTHON\" \"$@\"\n",
                encoding="utf-8",
            )
            os.chmod(fake_python, 0o700)
            environment = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "REAL_PYTHON": real_python,
                "FINANCE_REPOSITORY_DIR": str(checkout),
                "FINANCE_N8N_RECEIPT_DIR": str(receipt_dir),
                "FINANCE_N8N_CONTAINER": "disposable-finance",
                "N8N_FINANCE_PROJECT_ID": "finance-test-project",
                "FINANCE_N8N_RUNTIME_MODE": "DISPOSABLE_ONLY",
                "FINANCE_N8N_LIVE_EXPORT": str(live_export_path),
                "FOUR_TABLE_FORWARD_ACK": self.runner.REQUIRED_FORWARD_ACK,
                "FINANCE_TEST_PRE": str(receipt_dir / "pre.raw"),
                "FINANCE_TEST_POST": str(receipt_dir / "forward.raw"),
                "FINANCE_TEST_ROLLBACK_PRE": str(receipt_dir / "rollback-pre.raw"),
                "FINANCE_TEST_ROLLBACK_POST": str(receipt_dir / "rollback-post.raw"),
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
            call_order = [
                line.split(maxsplit=1)[0]
                for line in log.read_text(encoding="utf-8").splitlines()
                if line in {"READ", "EXECUTE"} or line.startswith("PY_")
            ]
            self.assertEqual(
                call_order,
                ["READ", "EXECUTE", "READ", "EXECUTE", "READ", "PY_FORWARD"],
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
            lines = log.read_text(encoding="utf-8").splitlines()
            call_order = [
                line.split(maxsplit=1)[0]
                for line in lines
                if line in {"READ", "EXECUTE"} or line.startswith("PY_")
            ]
            self.assertEqual(
                call_order,
                [
                    "READ", "EXECUTE", "READ", "EXECUTE", "READ", "PY_FORWARD",
                    "READ", "PY_ROLLBACK_RUNTIME", "READ", "PY_ROLLBACK_FINAL",
                ],
            )
            runtime_line = next(line for line in lines if line.startswith("PY_ROLLBACK_RUNTIME "))
            final_line = next(line for line in lines if line.startswith("PY_ROLLBACK_FINAL "))
            for line in (runtime_line, final_line):
                self.assertIn("--source-backup " + str(receipt_dir / "finance-data-table-backup-v1.json"), line)
                self.assertIn("--migration-receipt " + str(receipt_dir / "data-table-migration-receipt.json"), line)
                self.assertIn("--source-backup-sha256 ", line)
                self.assertIn("--accepted-identity " + str(receipt_dir / "finance-four-table-accepted-identity.json"), line)
                self.assertIn("--runtime-state " + str(receipt_dir / "finance-data-table-disposable-runtime-state.json"), line)
                self.assertIn("--runtime-action FOUR_TABLE_ROLLBACK_RUNTIME_EXECUTED", line)
            runtime_state = json.loads(
                (receipt_dir / "finance-data-table-disposable-runtime-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(runtime_state["operation"], "ROLLBACK")
            self.assertTrue(runtime_state["target_tables_untouched"])
            self.assertEqual(runtime_state["source_digest"], runtime_state["restored_source_digest"])


if __name__ == "__main__":
    unittest.main()
