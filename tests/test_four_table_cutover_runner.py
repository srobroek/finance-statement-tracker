from __future__ import annotations

import base64
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "integrations/n8n/setup-workflows/runner/four_table_cutover.py"
MIGRATION_PATH = ROOT / "integrations/n8n/generate_data_table_migration.py"
READBACK_PARSER_PATH = ROOT / "integrations/n8n/setup-workflows/runner/parse_n8n_redacted_wrapper_output.py"
RETAINED_READBACK_FIXTURE = ROOT / "tests/fixtures/n8n-2.36.2-data-table-digest-output.json"
SCHEMA_PATH = ROOT / "integrations/n8n/schemas/finance-four-table-cutover-receipt-v1.schema.json"
SHELL_RUNNER_PATH = ROOT / "integrations/n8n/setup-workflows/runner/run-four-table-cutover.sh"
PRODUCTION_SHELL_RUNNER_PATH = SHELL_RUNNER_PATH
PRODUCTION_RUNTIME_PATH = ROOT / "integrations/n8n/setup-workflows/runner/n8n-cli-four-table-cutover.cjs"


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

    def _workflow_body(self, index: int, nodes: list[dict] | None = None) -> dict:
        return {
            "name": f"Live Workflow {index}",
            "nodes": list(nodes or []),
            "connections": {},
            "settings": {},
            "meta": {},
            "pinData": {},
        }

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
                    "workflow_body_sha256": self.runner._workflow_body_digest(self._workflow_body(index)),
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
                    "old_table_id": self.runner.LEGACY_TABLE_IDS[item["source_table"]],
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
            binding={
                "operation_nonce": self.runner.DEFAULT_OPERATION_NONCE,
                "protected_quiescence_receipt_digest": self.runner.APPROVED_QUIESCENCE_RECEIPT_DIGEST,
                "required_live_export_digest": self.runner._export_semantic_digest(live_export),
                "contract_bijection_digest": self.runner.APPROVED_CONTRACT_BIJECTION_DIGEST,
            },
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

    def _production_runtime_harness(self, temp: Path):
        """Build the disposable n8n/PostgreSQL harness used by runtime tests."""
        _source, migration, _receipt_sha, _workflow_root, _raw_readback, _raw_pre, _rollback_pre, _raw_rollback = self._fixture(temp)
        live_export_path = migration.parent / self.runner.LIVE_EXPORT_FILENAME
        lock_receipt_path = migration.parent / self.runner.LOCK_RECEIPT_FILENAME
        exported = json.loads(live_export_path.read_text(encoding="utf-8"))
        lock_receipt = json.loads(lock_receipt_path.read_text(encoding="utf-8"))
        state_path = temp / "database-state.json"
        state = {
            "workflows": {
                workflow["workflow_id"]: {
                    "id": workflow["workflow_id"],
                    **self._workflow_body(index),
                    "active": False,
                    "activeVersionId": None,
                    "versionId": workflow["revision_id"],
                    "nodes": [],
                    "meta": {},
                    "settings": {},
                }
                for index, workflow in enumerate(exported["workflows"])
            },
            "dataTables": {
                target["name"]: {
                    "id": target["table_id"],
                    "name": target["name"],
                    "projectId": exported["project_id"],
                }
                for target in exported["targets"]
            },
            "journal": [],
        }
        for reference in exported["references"]:
            state["workflows"][reference["workflow_id"]]["nodes"].append(
                {
                    "id": reference["node_id"],
                    "name": reference["node_name"],
                    "parameters": {"dataTableId": reference["old_table_id"]},
                }
            )
        for workflow in exported["workflows"]:
            workflow["workflow_body_sha256"] = self.runner._workflow_body_digest(
                state["workflows"][workflow["workflow_id"]]
            )
        exported.pop("export_sha256", None)
        exported["export_sha256"] = self.runner.hashlib.sha256(
            self.runner._canonical_bytes(exported)
        ).hexdigest()
        semantic_digest = self.runner._export_semantic_digest(exported)
        live_export_path.write_bytes(self.runner._canonical_bytes(exported))
        os.chmod(live_export_path, 0o600)
        lock_receipt.update(
            {
                "export_sha256": exported["export_sha256"],
                "required_live_export_digest": semantic_digest,
            }
        )
        lock_receipt.pop("lock_receipt_sha256", None)
        lock_receipt["lock_receipt_sha256"] = self.runner.hashlib.sha256(
            self.runner._canonical_bytes(lock_receipt)
        ).hexdigest()
        lock_receipt_path.write_bytes(self.runner._canonical_bytes(lock_receipt))
        os.chmod(lock_receipt_path, 0o600)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        initial_state = json.loads(json.dumps(state))
        lifecycle_log = temp / "n8n-lifecycle.log"
        lifecycle_log.write_text("", encoding="utf-8")

        def reset_state():
            state_path.write_text(json.dumps(initial_state), encoding="utf-8")

        node_root = temp / "node_modules"
        n8n_root = node_root / "n8n"
        for path in (n8n_root / "bin", node_root / "pg"):
            path.mkdir(parents=True, exist_ok=True)
        (n8n_root / "package.json").write_text('{"name":"n8n","version":"test"}\n', encoding="utf-8")
        (n8n_root / "bin/n8n").write_text(
            """const fs = require('node:fs');
fs.appendFileSync(process.env.FINANCE_TEST_LIFECYCLE_LOG, 'managed-db-close\\n');
process.exitCode = 97;
""",
            encoding="utf-8",
        )
        (node_root / "pg/index.js").write_text(
            """'use strict';
const fs = require('node:fs');
function clone(value) { return JSON.parse(JSON.stringify(value)); }
function load() { return JSON.parse(fs.readFileSync(process.env.FINANCE_TEST_DB_STATE, 'utf8')); }
function save(value) { fs.writeFileSync(process.env.FINANCE_TEST_DB_STATE, JSON.stringify(value)); }
class Client {
  constructor() { this.state = load(); this.tx = null; }
  async connect() {}
  current() { return this.tx || this.state; }
  async query(sql, params = []) {
    const statement = sql.trim();
    if (statement === 'BEGIN') { this.tx = clone(this.state); return { rows: [] }; }
    if (statement.startsWith('SET LOCAL synchronous_commit')) return { rows: [] };
    if (statement.includes('pg_try_advisory_xact_lock')) return { rows: [{ acquired: true }] };
    if (statement.includes('FROM execution_entity')) return { rows: [{ count: 0 }] };
    if (statement.startsWith('SELECT id, name') && statement.includes('FROM data_table')) {
      const [projectId, names] = params;
      return {
        rows: Object.values(this.current().dataTables)
          .filter((table) => table.projectId === projectId && names.includes(table.name))
          .map(clone),
      };
    }
    if (statement.startsWith('SELECT receipt') && statement.includes('FROM finance_four_table_cutover_journal')) {
      const [projectId, lockResource, operation, exportSha] = params;
      const rows = this.current().journal.filter((entry) =>
        entry.projectId === projectId && entry.lockResource === lockResource &&
        entry.operation === operation && entry.receipt.export_sha256 === exportSha,
      );
      return { rows: rows.length ? [{ receipt: clone(rows[rows.length - 1].receipt) }] : [] };
    }
    if (statement.startsWith('SELECT w.id')) {
      return { rows: (params[1] || []).map((id) => this.current().workflows[id]).filter(Boolean).map(clone) };
    }
    if (statement.startsWith('UPDATE workflow_entity')) {
      const [nodesJson, workflowId, revisionId] = params;
      const workflow = this.current().workflows[workflowId];
      if (!workflow) return { rowCount: 0, rows: [] };
      workflow.nodes = JSON.parse(nodesJson);
      workflow.versionId = revisionId;
      return { rowCount: 1, rows: [{ id: workflowId, versionId: revisionId }] };
    }
    if (statement.startsWith('CREATE TABLE IF NOT EXISTS')) return { rows: [] };
    if (statement.startsWith('INSERT INTO finance_four_table_cutover_journal')) {
      const [receiptSha, projectId, operation, lockResource, receiptJson] = params;
      this.current().journal.push({ receiptSha, projectId, operation, lockResource, receipt: JSON.parse(receiptJson) });
      return { rowCount: 1, rows: [] };
    }
    if (statement === 'COMMIT') {
      if (process.env.FINANCE_TEST_PG_COMMIT_FAILURE === '1') throw new Error('INJECTED_COMMIT_FAILURE');
      this.state = this.tx;
      this.tx = null;
      save(this.state);
      return { rows: [] };
    }
    if (statement === 'ROLLBACK') { this.tx = null; return { rows: [] }; }
    throw new Error('UNEXPECTED_QUERY:' + statement.slice(0, 80));
  }
  async end() {}
}
module.exports = { Client };
""",
            encoding="utf-8",
        )

        target_json = json.dumps(
            [{"name": target["name"], "id": target["table_id"]} for target in exported["targets"]]
        )
        runtime_environment = {
            **os.environ,
            "N8N_FINANCE_PROJECT_ID": exported["project_id"],
            "FINANCE_FOUR_TABLE_REPOSITORY_ROOT": exported["repository_root"],
            "FINANCE_FOUR_TABLE_SOURCE_HEAD": exported["source_head"],
            "FINANCE_FOUR_TABLE_GENERATOR_HEAD": exported["generator_head"],
            "FINANCE_FOUR_TABLE_MIGRATION_SHA256": lock_receipt["migration_receipt_sha256"],
            "FINANCE_FOUR_TABLE_SOURCE_SHA256": lock_receipt["source_backup_sha256"],
            "FINANCE_FOUR_TABLE_IDENTITY_SHA256": lock_receipt["accepted_identity_sha256"],
            "FINANCE_FOUR_TABLE_EXPORT_B64": base64.b64encode(live_export_path.read_bytes()).decode("ascii"),
            "FINANCE_FOUR_TABLE_LOCK_B64": base64.b64encode(lock_receipt_path.read_bytes()).decode("ascii"),
            "FINANCE_FOUR_TABLE_N8N_ROOT": str(node_root),
            "FINANCE_TEST_DB_STATE": str(state_path),
            "FINANCE_TEST_TARGETS_JSON": target_json,
            "DB_POSTGRESDB_HOST": "test",
            "DB_POSTGRESDB_PORT": "5432",
            "DB_POSTGRESDB_DATABASE": "test",
            "DB_POSTGRESDB_USER": "test",
            "DB_POSTGRESDB_PASSWORD": "test",
            "FINANCE_FOUR_TABLE_OPERATION": "FORWARD",
            "FINANCE_FOUR_TABLE_ACK": self.runner.REQUIRED_FORWARD_ACK,
            "FINANCE_FOUR_TABLE_OPERATION_NONCE": self.runner.DEFAULT_OPERATION_NONCE,
            "FINANCE_FOUR_TABLE_PROTECTED_QUIESCENCE_RECEIPT_DIGEST": self.runner.APPROVED_QUIESCENCE_RECEIPT_DIGEST,
            "FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST": semantic_digest,
            "FINANCE_FOUR_TABLE_CONTRACT_BIJECTION_DIGEST": self.runner.APPROVED_CONTRACT_BIJECTION_DIGEST,
            "FINANCE_TEST_LIFECYCLE_LOG": str(lifecycle_log),
        }

        def run_runtime(**overrides):
            return subprocess.run(
                ["node", "-"],
                cwd=ROOT,
                env={**runtime_environment, **overrides},
                input=PRODUCTION_RUNTIME_PATH.read_text(encoding="utf-8"),
                capture_output=True,
                text=True,
                check=False,
            )

        return {
            "migration": migration,
            "live_export_path": live_export_path,
            "lock_receipt_path": lock_receipt_path,
            "exported": exported,
            "state_path": state_path,
            "initial_state": initial_state,
            "reset_state": reset_state,
            "node_root": node_root,
            "target_json": target_json,
            "runtime_environment": runtime_environment,
            "run_runtime": run_runtime,
            "lifecycle_log": lifecycle_log,
        }

    def _install_digest_rewriting_python(self, fake_bin: Path) -> str:
        real_python = shutil.which("python3")
        self.assertIsNotNone(real_python)
        fake_python = fake_bin / "python3"
        fake_python.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "args=(\"$@\")\n"
            "has_required=0\n"
            "for ((index=0; index<${#args[@]}-1; index++)); do\n"
            "  if [[ \"${args[$index]}\" = \"--required-live-export-digest\" ]]; then has_required=1; args[$((index + 1))]=\"$FINANCE_TEST_LIVE_EXPORT_DIGEST\"; fi\n"
            "done\n"
            "if [[ \"$*\" == *'four_table_cutover.py rollback-runtime '* && \"$has_required\" = 0 ]]; then args+=(--required-live-export-digest \"$FINANCE_TEST_LIVE_EXPORT_DIGEST\"); fi\n"
            "exec \"$REAL_PYTHON\" \"${args[@]}\"\n",
            encoding="utf-8",
        )
        os.chmod(fake_python, 0o700)
        return real_python

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

    def _rewrite_export(self, path: Path, mutate) -> dict:
        export = json.loads(path.read_text(encoding="utf-8"))
        mutate(export)
        unsigned = dict(export)
        unsigned.pop("export_sha256", None)
        export["export_sha256"] = self.runner.hashlib.sha256(
            self.runner._canonical_bytes(unsigned)
        ).hexdigest()
        path.write_bytes(self.runner._canonical_bytes(export))
        os.chmod(path, 0o600)
        return export

    def _validate_fixture_export(
        self,
        export_path: Path,
        source: Path,
        receipt_sha: str,
        required_digest: str,
        *,
        source_head: str | None = None,
        generator_head: str | None = None,
        identity_digest: str | None = None,
        project_id: str | None = None,
    ) -> dict:
        export = json.loads(export_path.read_text(encoding="utf-8"))
        matrix = json.loads(
            (ROOT / "integrations/n8n/data-table-migration-matrix.json").read_text(
                encoding="utf-8"
            )
        )
        return self.runner._validate_live_export(
            export_path,
            source_head=source_head or self.source_head,
            generator_head=generator_head or self.generator_head,
            migration_receipt_sha=receipt_sha,
            source_backup_sha=self.runner.hashlib.sha256(source.read_bytes()).hexdigest(),
            identity_digest=identity_digest or export["accepted_identity_sha256"],
            required_export_digest=required_digest,
            matrix=matrix,
            project_id=project_id or export["project_id"],
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
            export_digest = self.runner._export_semantic_digest(
                json.loads(
                    (migration.parent / self.runner.LIVE_EXPORT_FILENAME).read_text(encoding="utf-8")
                )
            )
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
                "--required-live-export-digest",
                export_digest,
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
            self.assertEqual(result["operation_nonce"], self.runner.DEFAULT_OPERATION_NONCE)
            self.assertEqual(
                result["protected_quiescence_receipt_digest"],
                self.runner.APPROVED_QUIESCENCE_RECEIPT_DIGEST,
            )
            self.assertEqual(
                result["required_live_export_digest"],
                export_digest,
            )
            self.assertEqual(
                result["contract_bijection_digest"],
                self.runner.APPROVED_CONTRACT_BIJECTION_DIGEST,
            )
            self.assertNotEqual(
                result["required_live_export_digest"], result["workflow_export_sha256"]
            )
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
            export_digest = self.runner._export_semantic_digest(
                json.loads(
                    (migration.parent / self.runner.LIVE_EXPORT_FILENAME).read_text(encoding="utf-8")
                )
            )
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
                "--required-live-export-digest",
                export_digest,
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
                "--required-live-export-digest", export_digest,
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

    def test_live_export_provenance_rebind_preserves_semantic_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, _workflow_root, _raw_readback, _raw_pre, _rollback_pre, _raw_rollback = self._fixture(temp)
            export_path = migration.parent / self.runner.LIVE_EXPORT_FILENAME
            original = json.loads(export_path.read_text(encoding="utf-8"))
            required_digest = self.runner._export_semantic_digest(original)
            rebound = self._rewrite_export(
                export_path,
                lambda export: export.update(
                    {
                        "source_head": "b" * 40,
                        "accepted_identity_sha256": "c" * 64,
                    }
                ),
            )
            validated = self._validate_fixture_export(
                export_path,
                source,
                receipt_sha,
                required_digest,
                source_head=rebound["source_head"],
                identity_digest=rebound["accepted_identity_sha256"],
            )
            self.assertEqual(validated["semantic_digest"], required_digest)

    def test_live_export_every_semantic_change_fails_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, _workflow_root, _raw_readback, _raw_pre, _rollback_pre, _raw_rollback = self._fixture(temp)
            export_path = migration.parent / self.runner.LIVE_EXPORT_FILENAME
            original_bytes = export_path.read_bytes()
            original = json.loads(original_bytes)
            required_digest = self.runner._export_semantic_digest(original)
            mutations = (
                ("schema_version", lambda export: export.update({"schema_version": "changed"})),
                ("workflow_count", lambda export: export.update({"workflow_count": 18})),
                ("in_flight", lambda export: export.update({"in_flight": 1})),
                ("workflow_id", lambda export: export["workflows"][0].update({"workflow_id": "changed"})),
                ("workflow_active", lambda export: export["workflows"][0].update({"active": True})),
                ("workflow_published", lambda export: export["workflows"][0].update({"published": True})),
                ("workflow_in_flight", lambda export: export["workflows"][0].update({"in_flight": 1})),
                (
                    "workflow_body_sha256",
                    lambda export: export["workflows"][0].update({"workflow_body_sha256": "a" * 64}),
                ),
                ("target_name", lambda export: export["targets"][0].update({"name": "changed"})),
                ("target_table_id", lambda export: export["targets"][0].update({"table_id": "changed"})),
                ("target_schema_sha256", lambda export: export["targets"][0].update({"schema_sha256": "a" * 64})),
                ("reference_id", lambda export: export["references"][0].update({"reference_id": "changed"})),
                ("reference_workflow_id", lambda export: export["references"][0].update({"workflow_id": "changed"})),
                ("reference_workflow_path", lambda export: export["references"][0].update({"workflow_path": "changed"})),
                ("reference_node_id", lambda export: export["references"][0].update({"node_id": "changed"})),
                ("reference_node_name", lambda export: export["references"][0].update({"node_name": "changed"})),
                ("reference_operation", lambda export: export["references"][0].update({"operation": "changed"})),
                ("reference_old_table_name", lambda export: export["references"][0].update({"old_table_name": "changed"})),
                ("reference_old_table_id", lambda export: export["references"][0].update({"old_table_id": "changed"})),
                ("reference_canonical_table_name", lambda export: export["references"][0].update({"canonical_table_name": "changed"})),
                ("reference_canonical_table_id", lambda export: export["references"][0].update({"canonical_table_id": "changed"})),
                ("reference_active", lambda export: export["references"][0].update({"active": True})),
                ("reference_published", lambda export: export["references"][0].update({"published": True})),
                ("reference_in_flight", lambda export: export["references"][0].update({"in_flight": 1})),
            )
            for field, mutation in mutations:
                with self.subTest(field=field):
                    export_path.write_bytes(original_bytes)
                    os.chmod(export_path, 0o600)
                    rebound = self._rewrite_export(export_path, mutation)
                    self.assertNotEqual(self.runner._export_semantic_digest(rebound), required_digest)
                    with self.assertRaises(self.runner.CutoverError):
                        self._validate_fixture_export(
                            export_path, source, receipt_sha, required_digest
                        )

    def test_live_export_volatile_fields_do_not_change_semantic_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, _workflow_root, _raw_readback, _raw_pre, _rollback_pre, _raw_rollback = self._fixture(temp)
            export_path = migration.parent / self.runner.LIVE_EXPORT_FILENAME
            original = json.loads(export_path.read_text(encoding="utf-8"))
            required_digest = self.runner._export_semantic_digest(original)

            def mutate(export):
                revisions = {}
                for index, workflow in enumerate(export["workflows"]):
                    workflow["revision_id"] = f"replacement-revision-{index}"
                    revisions[workflow["workflow_id"]] = workflow["revision_id"]
                for reference in export["references"]:
                    reference["revision_id"] = revisions[reference["workflow_id"]]
                    reference["source_table"] = reference["old_table_name"]
                export["workflows"].reverse()
                export["targets"].reverse()
                export["references"].reverse()

            rebound = self._rewrite_export(export_path, mutate)
            self.assertEqual(self.runner._export_semantic_digest(rebound), required_digest)
            validated = self._validate_fixture_export(export_path, source, receipt_sha, required_digest)
            self.assertEqual(validated["semantic_digest"], required_digest)

    def test_live_export_rejects_derived_source_table_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, _workflow_root, _raw_readback, _raw_pre, _rollback_pre, _raw_rollback = self._fixture(temp)
            export_path = migration.parent / self.runner.LIVE_EXPORT_FILENAME
            rebound = self._rewrite_export(
                export_path,
                lambda export: export["references"][0].update({"source_table": "finance_wrong_table"}),
            )
            with self.assertRaisesRegex(self.runner.CutoverError, "LIVE_REFERENCE_SOURCE_TABLE_MISMATCH"):
                self._validate_fixture_export(
                    export_path, source, receipt_sha, self.runner._export_semantic_digest(rebound)
                )

    def test_live_export_body_change_fails_with_unchanged_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, _workflow_root, _raw_readback, _raw_pre, _rollback_pre, _raw_rollback = self._fixture(temp)
            export_path = migration.parent / self.runner.LIVE_EXPORT_FILENAME
            original = json.loads(export_path.read_text(encoding="utf-8"))
            required_digest = self.runner._export_semantic_digest(original)
            revision = original["workflows"][0]["revision_id"]
            rebound = self._rewrite_export(
                export_path,
                lambda export: export["workflows"][0].update({"workflow_body_sha256": "b" * 64}),
            )
            self.assertEqual(rebound["workflows"][0]["revision_id"], revision)
            self.assertNotEqual(self.runner._export_semantic_digest(rebound), required_digest)
            with self.assertRaisesRegex(self.runner.CutoverError, "LIVE_EXPORT_REQUIRED_DIGEST_MISMATCH"):
                self._validate_fixture_export(export_path, source, receipt_sha, required_digest)

    def test_real_workflow_fixture_matches_volatile_export_variant(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, _workflow_root, _raw_readback, _raw_pre, _rollback_pre, _raw_rollback = self._fixture(temp)
            export_path = migration.parent / self.runner.LIVE_EXPORT_FILENAME
            workflows = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in sorted((ROOT / "integrations/n8n/workflows").glob("*.json"))
            ]
            by_path = {
                str(path.relative_to(ROOT)): json.loads(path.read_text(encoding="utf-8"))
                for path in sorted((ROOT / "integrations/n8n/workflows").glob("*.json"))
            }
            self.assertEqual(len(workflows), 19)

            def bind_real_bodies(export):
                revisions = {}
                for index, (record, body) in enumerate(zip(export["workflows"], workflows, strict=True)):
                    record.update(
                        {
                            "workflow_id": body["id"],
                            "revision_id": f"retained-revision-{index}",
                            "workflow_body_sha256": self.runner._workflow_body_digest(body),
                        }
                    )
                    revisions[body["id"]] = record["revision_id"]
                for reference in export["references"]:
                    body = by_path[reference["workflow_path"]]
                    reference["workflow_id"] = body["id"]
                    reference["revision_id"] = revisions[body["id"]]

            real_export = self._rewrite_export(export_path, bind_real_bodies)
            required_digest = self.runner._export_semantic_digest(real_export)
            self._validate_fixture_export(export_path, source, receipt_sha, required_digest)
            mutated_body = json.loads(json.dumps(workflows[0]))
            mutated_body["nodes"][0]["name"] += " changed"
            self.assertNotEqual(
                self.runner._workflow_body_digest(workflows[0]),
                self.runner._workflow_body_digest(mutated_body),
            )

            def volatile_variant(export):
                revisions = {}
                for index, workflow in enumerate(export["workflows"]):
                    workflow["revision_id"] = f"refreshed-revision-{index}"
                    revisions[workflow["workflow_id"]] = workflow["revision_id"]
                for reference in export["references"]:
                    reference["revision_id"] = revisions[reference["workflow_id"]]
                    reference["source_table"] = reference["old_table_name"]

            rebound = self._rewrite_export(export_path, volatile_variant)
            self.assertEqual(self.runner._export_semantic_digest(rebound), required_digest)
            self._validate_fixture_export(export_path, source, receipt_sha, required_digest)

    def test_live_export_rejects_wrong_head_path_and_identity_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source, migration, receipt_sha, _workflow_root, _raw_readback, _raw_pre, _rollback_pre, _raw_rollback = self._fixture(temp)
            export_path = migration.parent / self.runner.LIVE_EXPORT_FILENAME
            original_bytes = export_path.read_bytes()
            original = json.loads(original_bytes)
            for field, value, expected_error in (
                ("source_head", "a" * 40, "LIVE_EXPORT_SOURCE_HEAD_MISMATCH"),
                ("repository_root", "/rebound/checkout", "LIVE_EXPORT_REPOSITORY_ROOT_MISMATCH"),
                ("project_id", "other-project", "LIVE_EXPORT_PROJECT_ID_MISMATCH"),
                ("generator_head", "d" * 64, "LIVE_EXPORT_GENERATOR_HEAD_MISMATCH"),
                ("migration_receipt_sha256", "e" * 64, "LIVE_EXPORT_MIGRATION_RECEIPT_SHA256_MISMATCH"),
                ("source_backup_sha256", "f" * 64, "LIVE_EXPORT_SOURCE_BACKUP_SHA256_MISMATCH"),
                ("accepted_identity_sha256", "b" * 64, "LIVE_EXPORT_ACCEPTED_IDENTITY_SHA256_MISMATCH"),
                ("redacted", False, "LIVE_EXPORT_REDACTION_REQUIRED"),
            ):
                with self.subTest(field=field):
                    export_path.write_bytes(original_bytes)
                    os.chmod(export_path, 0o600)
                    rebound = self._rewrite_export(
                        export_path, lambda export, field=field, value=value: export.update({field: value})
                    )
                    with self.assertRaisesRegex(self.runner.CutoverError, expected_error):
                        self._validate_fixture_export(
                            export_path,
                            source,
                            receipt_sha,
                            self.runner._export_semantic_digest(rebound),
                            identity_digest=original["accepted_identity_sha256"],
                            project_id=original["project_id"],
                        )

            export_path.write_bytes(original_bytes)
            os.chmod(export_path, 0o600)
            tampered = json.loads(original_bytes)
            tampered["source_head"] = "c" * 40
            export_path.write_bytes(self.runner._canonical_bytes(tampered))
            os.chmod(export_path, 0o600)
            with self.assertRaisesRegex(self.runner.CutoverError, "LIVE_EXPORT_INTEGRITY_MISMATCH"):
                self._validate_fixture_export(
                    export_path,
                    source,
                    receipt_sha,
                    self.runner._export_semantic_digest(tampered),
                    project_id=original["project_id"],
                )

    def test_repeated_export_generation_has_one_semantic_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            _source, migration, _receipt_sha, _workflow_root, _raw_readback, _raw_pre, _rollback_pre, _raw_rollback = self._fixture(temp)
            original = json.loads(
                (migration.parent / self.runner.LIVE_EXPORT_FILENAME).read_text(encoding="utf-8")
            )
            rebound = dict(original)
            rebound.update(
                {
                    "repository_root": "/another/checkout",
                    "source_head": "d" * 40,
                    "accepted_identity_sha256": "e" * 64,
                }
            )
            self.assertEqual(
                self.runner._export_semantic_digest(original),
                self.runner._export_semantic_digest(rebound),
            )

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

    def test_lock_resource_is_stable_for_project(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            _source, migration, _receipt_sha, _workflow_root, _raw_readback, _raw_pre, _rollback_pre, _raw_rollback = self._fixture(temp)
            export = json.loads((migration.parent / self.runner.LIVE_EXPORT_FILENAME).read_text(encoding="utf-8"))
            lock = json.loads((migration.parent / self.runner.LOCK_RECEIPT_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(lock["resource_key"], f"{self.runner.LOCK_NAME}:{export['project_id']}")
            export["export_sha256"] = "f" * 64
            replacement = self.runner._lock_receipt(
                export=export,
                migration_receipt_sha=lock["migration_receipt_sha256"],
                source_backup_sha=lock["source_backup_sha256"],
                identity_digest=lock["accepted_identity_sha256"],
                project_id=export["project_id"],
                operation="PRECONDITION",
            )
            self.assertEqual(replacement["resource_key"], lock["resource_key"])

    def test_live_export_rejects_aliasing_two_references_to_one_node(self):
        with tempfile.TemporaryDirectory() as directory:
            source, migration, receipt_sha, workflow_root, _raw_readback, _raw_pre, _rollback_pre, _raw_rollback = self._fixture(Path(directory))
            export_path = migration.parent / self.runner.LIVE_EXPORT_FILENAME
            export = json.loads(export_path.read_text(encoding="utf-8"))
            export["references"][1]["node_id"] = export["references"][0]["node_id"]
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

    def test_live_export_rejects_conflicting_legacy_table_id(self):
        with tempfile.TemporaryDirectory() as directory:
            source, migration, receipt_sha, workflow_root, _raw_readback, _raw_pre, _rollback_pre, _raw_rollback = self._fixture(Path(directory))
            export_path = migration.parent / self.runner.LIVE_EXPORT_FILENAME
            export = json.loads(export_path.read_text(encoding="utf-8"))
            source_table = export["references"][0]["old_table_name"]
            conflicting_reference = next(
                reference
                for reference in export["references"][1:]
                if reference["old_table_name"] == source_table
            )
            replacement = next(
                table_id
                for table_name, table_id in self.runner.LEGACY_TABLE_IDS.items()
                if table_name != source_table
            )
            conflicting_reference["old_table_id"] = replacement
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
            export_digest = self.runner._export_semantic_digest(
                json.loads(
                    (migration.parent / self.runner.LIVE_EXPORT_FILENAME).read_text(encoding="utf-8")
                )
            )
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
                "--required-live-export-digest",
                export_digest,
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
        for binding in (
            "FINANCE_FOUR_TABLE_OPERATION_NONCE",
            "FINANCE_FOUR_TABLE_PROTECTED_QUIESCENCE_RECEIPT_DIGEST",
            "FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST",
            "FINANCE_FOUR_TABLE_CONTRACT_BIJECTION_DIGEST",
        ):
            self.assertIn(binding, script)

    def test_production_shell_surface_requires_project_lock_and_runtime_replay(self):
        script = PRODUCTION_SHELL_RUNNER_PATH.read_text(encoding="utf-8")
        for command in (
            "PRODUCTION_ONLY",
            "four_table_cutover.py\" preflight",
            "FINANCE_FOUR_TABLE_EXPORT_B64",
            "FINANCE_FOUR_TABLE_LOCK_B64",
            "FINANCE_FOUR_TABLE_MIGRATION_SHA256",
            "FINANCE_FOUR_TABLE_IDENTITY_SHA256",
            "FINANCE_FOUR_TABLE_FORWARD_RECEIPT_B64",
            "FINANCE_FOUR_TABLE_RECOVER_JOURNAL",
            "recover_forward_runtime_receipt",
            "forward_runtime_receipt",
            "n8n-cli-four-table-cutover.cjs",
            "replay_noop",
            "durable_journal",
            "postgresql_synchronous_wal",
            "N8N_FINANCE_PROJECT_ID",
            "runtime_stdout",
            "runtime_stderr",
            "recovery_stdout",
            "recovery_stderr",
        ):
            self.assertIn(command, script)
        self.assertIn('> "$recovery_stdout" 2> "$recovery_stderr"', script)
        self.assertIn('> "$runtime_stdout" 2> "$runtime_stderr"', script)
        self.assertNotIn('"$runtime_output"', script)
        self.assertIn('"$FINANCE_N8N_CONTAINER" node - \\\n', script)
        self.assertNotIn('"$FINANCE_N8N_CONTAINER" node - list:workflow', script.split('run_readback()')[0])
        runtime = PRODUCTION_RUNTIME_PATH.read_text(encoding="utf-8")
        for command in (
            "pg_try_advisory_xact_lock",
            "WRITER_LOCK_RECEIPT_BINDING_INVALID",
            "FORWARD_RUNTIME_RECEIPT_BINDING_INVALID",
            "LIVE_REFERENCE_NODE_ALIAS_CONFLICT",
            "LIVE_REFERENCE_OLD_TABLE_ID_CONFLICT",
            "EXACT_SEVEN_LEGACY_TABLE_ID_MAP_REQUIRED",
            "FORWARD_REPLAY_READBACK_MISMATCH",
            "LIVE_IN_FLIGHT_EXECUTIONS_PRESENT",
            "INJECTED_FAILURE_AFTER_UPDATE",
            "readback_verified",
            "readback_digest_sha256",
            "durable_journal",
            "commit_protocol",
            "postgresql_synchronous_wal",
            "finance_four_table_cutover_journal",
            "persistRecoveryJournal",
            "CREATE TABLE IF NOT EXISTS",
            "INJECTED_RECEIPT_FAILURE",
            "writeRuntimeReceipt",
            "recoverForwardJournal",
            "FORWARD_RUNTIME_JOURNAL_NOT_FOUND",
            "export_sha256",
            "ROLLBACK",
            "finance_four_table_cutover:${projectId}",
            "FOR UPDATE",
            "UPDATE workflow_entity",
            "LIVE_WORKFLOW_REVISION_MISMATCH",
            "runtime_plan_receipt_sha256",
            "updateWorkflows",
            "FROM data_table",
            '"projectId" = $1',
            "ANY($2::text[])",
            "new pg.Client",
            "await client.connect()",
            "await client.end()",
            "await main();",
            "process.exitCode = 1",
        ):
            self.assertIn(command, runtime)
        for forbidden in (
            "ListWorkflowCommand",
            "DataTableService",
            "@n8n/di",
            "Container",
            "n8nRequire",
            "n8nRoot",
            "bin', 'n8n",
            "new pg.Pool",
            "idleTimeoutMillis",
            ".release()",
            "lock.pool",
        ):
            self.assertNotIn(forbidden, runtime)

    def test_production_runtime_script_has_valid_javascript(self):
        completed = subprocess.run(
            ["node", "--check", str(PRODUCTION_RUNTIME_PATH)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_production_transaction_rolls_back_disposable_forced_failure(self):
        """Exercise the lock, in-flight guard, and atomic update against disposable PostgreSQL."""
        dsn = os.environ.get("FINANCE_FOUR_TABLE_TEST_DSN") or os.environ.get(
            "FINANCE_WORKFLOW_SQL_TEST_DSN"
        )
        if not dsn or shutil.which("psql") is None:
            self.skipTest("set FINANCE_FOUR_TABLE_TEST_DSN for disposable PostgreSQL")

        schema = f"four_table_cutover_{uuid.uuid4().hex}"
        project_id = f"finance-test-{uuid.uuid4().hex[:16]}"
        workflow_id = f"workflow-{uuid.uuid4().hex[:16]}"
        resource = f"finance_four_table_cutover:{project_id}"
        base_command = ["psql", dsn, "--set", "ON_ERROR_STOP=1"]
        setup_sql = f'''
CREATE SCHEMA "{schema}";
SET search_path TO "{schema}";
CREATE TABLE workflow_entity (
  id varchar(36) PRIMARY KEY,
  active boolean NOT NULL,
  "activeVersionId" varchar(36),
  "versionId" varchar(36),
  nodes json NOT NULL,
  meta json,
  settings json
);
CREATE TABLE shared_workflow (
  "workflowId" varchar(36) NOT NULL,
  "projectId" varchar(64) NOT NULL,
  role varchar(64) NOT NULL
);
CREATE TABLE execution_entity (
  "workflowId" varchar(36) NOT NULL,
  finished boolean
);
INSERT INTO workflow_entity (id, active, "activeVersionId", "versionId", nodes) VALUES
  ('{workflow_id}', FALSE, NULL, 'revision-before', '{{"selector":"legacy"}}');
INSERT INTO shared_workflow VALUES ('{workflow_id}', '{project_id}', 'workflow:owner');
INSERT INTO execution_entity VALUES ('{workflow_id}', FALSE);
'''
        setup = subprocess.run(
            [*base_command, "--command", setup_sql],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(setup.returncode, 0, setup.stderr)
        try:
            in_flight = subprocess.run(
                [
                    *base_command,
                    "--tuples-only",
                    "--no-align",
                    "--command",
                    f'''SET search_path TO "{schema}";
SELECT COUNT(*)::int
  FROM execution_entity e
  JOIN shared_workflow s ON s."workflowId" = e."workflowId"
 WHERE s."projectId" = '{project_id}' AND e.finished IS NOT TRUE;''',
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(in_flight.returncode, 0, in_flight.stderr)
            self.assertEqual(in_flight.stdout.strip(), "1")

            clear_execution = subprocess.run(
                [
                    *base_command,
                    "--command",
                    f'DELETE FROM "{schema}".execution_entity',
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(clear_execution.returncode, 0, clear_execution.stderr)

            forced_failure = subprocess.run(
                [
                    *base_command,
                    "--command",
                    f'''SET search_path TO "{schema}";
BEGIN;
SET LOCAL synchronous_commit = 'on';
SELECT pg_try_advisory_xact_lock(hashtextextended('{resource}', 0));
UPDATE workflow_entity w
   SET nodes = '{{"selector":"target"}}'::json, "versionId" = 'revision-after'
 WHERE w.id = '{workflow_id}'
   AND EXISTS (
     SELECT 1 FROM shared_workflow s
      WHERE s."workflowId" = w.id
        AND s."projectId" = '{project_id}'
        AND s.role = 'workflow:owner'
   )
 RETURNING w.id, w."versionId";
SELECT 1 / 0;
COMMIT;''',
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(forced_failure.returncode, 0, forced_failure.stdout)

            readback = subprocess.run(
                [
                    *base_command,
                    "--tuples-only",
                    "--no-align",
                    "--command",
                    f'''SET search_path TO "{schema}";
SELECT nodes->>'selector' || '|' || "versionId" FROM workflow_entity
 WHERE id = '{workflow_id}';''',
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(readback.returncode, 0, readback.stderr)
            self.assertEqual(readback.stdout.strip(), "legacy|revision-before")

            lock_after_rollback = subprocess.run(
                [
                    *base_command,
                    "--tuples-only",
                    "--no-align",
                    "--command",
                    f'''BEGIN;
SELECT pg_try_advisory_xact_lock(hashtextextended('{resource}', 0));
ROLLBACK;''',
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(lock_after_rollback.returncode, 0, lock_after_rollback.stderr)
            self.assertEqual(lock_after_rollback.stdout.strip(), "t")
        finally:
            subprocess.run(
                [
                    *base_command,
                    "--command",
                    f'DROP SCHEMA IF EXISTS "{schema}" CASCADE',
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_production_runtime_bypasses_command_finalizer_and_managed_db_close(self):
        """A managed command finalizer cannot close the direct transaction client."""
        with tempfile.TemporaryDirectory() as directory:
            harness = self._production_runtime_harness(Path(directory))
            completed = harness["run_runtime"]()
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(harness["lifecycle_log"].read_text(encoding="utf-8"), "")
            self.assertEqual(len(json.loads(harness["state_path"].read_text(encoding="utf-8"))["journal"]), 1)

    def test_production_runtime_drives_update_rollback_commit_and_receipt_failures(self):
        """Run the actual CJS runtime with a disposable n8n and PostgreSQL harness."""
        with tempfile.TemporaryDirectory() as directory:
            harness = self._production_runtime_harness(Path(directory))
            exported = harness["exported"]
            semantic_digest = self.runner._export_semantic_digest(exported)
            state_path = harness["state_path"]
            initial_state = harness["initial_state"]
            reset_state = harness["reset_state"]
            run_runtime = harness["run_runtime"]
            forward = run_runtime()
            self.assertEqual(forward.returncode, 0, forward.stderr)
            marker = "finance four-table runtime verified:"
            forward_line = next(line for line in forward.stdout.splitlines() if line.startswith(marker))
            forward_receipt = json.loads(forward_line.removeprefix(marker))
            for field, value in (
                ("operation_nonce", self.runner.DEFAULT_OPERATION_NONCE),
                (
                    "protected_quiescence_receipt_digest",
                    self.runner.APPROVED_QUIESCENCE_RECEIPT_DIGEST,
                ),
                ("required_live_export_digest", semantic_digest),
                ("contract_bijection_digest", self.runner.APPROVED_CONTRACT_BIJECTION_DIGEST),
            ):
                self.assertEqual(forward_receipt[field], value)
            self.assertEqual(forward_receipt["required_live_export_digest"], semantic_digest)
            self.assertTrue(forward_receipt["durable_journal"])
            after_forward = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(after_forward["journal"]), 1)
            for reference in exported["references"]:
                node = next(
                    node
                    for node in after_forward["workflows"][reference["workflow_id"]]["nodes"]
                    if node["id"] == reference["node_id"]
                )
                if reference["canonical_table_id"] is None:
                    self.assertNotIn("dataTableId", node["parameters"])
                else:
                    self.assertEqual(node["parameters"]["dataTableId"], reference["canonical_table_id"])

            rollback = run_runtime(
                FINANCE_FOUR_TABLE_OPERATION="ROLLBACK",
                FINANCE_FOUR_TABLE_ACK=self.runner.REQUIRED_ROLLBACK_ACK,
                FINANCE_FOUR_TABLE_FORWARD_RECEIPT_B64=base64.b64encode(
                    json.dumps(forward_receipt, separators=(",", ":")).encode("utf-8")
                ).decode("ascii"),
            )
            self.assertEqual(rollback.returncode, 0, rollback.stderr)
            rollback_line = next(line for line in rollback.stdout.splitlines() if line.startswith(marker))
            rollback_receipt = json.loads(rollback_line.removeprefix(marker))
            for field, value in (
                ("operation_nonce", self.runner.DEFAULT_OPERATION_NONCE),
                (
                    "protected_quiescence_receipt_digest",
                    self.runner.APPROVED_QUIESCENCE_RECEIPT_DIGEST,
                ),
                ("required_live_export_digest", semantic_digest),
                ("contract_bijection_digest", self.runner.APPROVED_CONTRACT_BIJECTION_DIGEST),
            ):
                self.assertEqual(rollback_receipt[field], value)
            after_rollback = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(after_rollback["journal"]), 2)
            for reference in exported["references"]:
                node = next(
                    node
                    for node in after_rollback["workflows"][reference["workflow_id"]]["nodes"]
                    if node["id"] == reference["node_id"]
                )
                self.assertEqual(node["parameters"]["dataTableId"], reference["old_table_id"])

            reset_state()
            update_failure = run_runtime(
                FINANCE_FOUR_TABLE_INJECT_FAILURE_AFTER_UPDATE="live-workflow-0",
            )
            self.assertNotEqual(update_failure.returncode, 0)
            after_update_failure = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(after_update_failure["journal"]), 0)
            self.assertEqual(after_update_failure, initial_state)

            reset_state()
            commit_failure = run_runtime(FINANCE_TEST_PG_COMMIT_FAILURE="1")
            self.assertNotEqual(commit_failure.returncode, 0)
            after_commit_failure = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(after_commit_failure["journal"]), 0)
            self.assertEqual(after_commit_failure, initial_state)

            reset_state()
            receipt_failure = run_runtime(FINANCE_FOUR_TABLE_INJECT_RECEIPT_FAILURE="1")
            self.assertNotEqual(receipt_failure.returncode, 0)
            after_receipt_failure = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(after_receipt_failure["journal"]), 1)
            self.assertEqual(after_receipt_failure["journal"][-1]["receipt"]["operation"], "FORWARD")
            self.assertEqual(
                after_receipt_failure["journal"][-1]["receipt"]["operation_nonce"],
                self.runner.DEFAULT_OPERATION_NONCE,
            )
            self.assertEqual(
                after_receipt_failure["journal"][-1]["receipt"]["required_live_export_digest"],
                semantic_digest,
            )
            for reference in exported["references"]:
                node = next(
                    node
                    for node in after_receipt_failure["workflows"][reference["workflow_id"]]["nodes"]
                    if node["id"] == reference["node_id"]
                )
                if reference["canonical_table_id"] is None:
                    self.assertNotIn("dataTableId", node["parameters"])
                else:
                    self.assertEqual(node["parameters"]["dataTableId"], reference["canonical_table_id"])

            recovered = run_runtime(FINANCE_FOUR_TABLE_RECOVER_JOURNAL="1")
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            recovered_line = next(line for line in recovered.stdout.splitlines() if line.startswith(marker))
            recovered_receipt = json.loads(recovered_line.removeprefix(marker))
            self.assertEqual(recovered_receipt, after_receipt_failure["journal"][-1]["receipt"])

            rollback_after_failure = run_runtime(
                FINANCE_FOUR_TABLE_OPERATION="ROLLBACK",
                FINANCE_FOUR_TABLE_ACK=self.runner.REQUIRED_ROLLBACK_ACK,
                FINANCE_FOUR_TABLE_FORWARD_RECEIPT_B64=base64.b64encode(
                    json.dumps(recovered_receipt, separators=(",", ":")).encode("utf-8")
                ).decode("ascii"),
            )
            self.assertEqual(rollback_after_failure.returncode, 0, rollback_after_failure.stderr)
            after_recovered_rollback = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(after_recovered_rollback["journal"]), 2)
            for reference in exported["references"]:
                node = next(
                    node
                    for node in after_recovered_rollback["workflows"][reference["workflow_id"]]["nodes"]
                    if node["id"] == reference["node_id"]
                )
                self.assertEqual(node["parameters"]["dataTableId"], reference["old_table_id"])

    def test_runtime_rejects_missing_or_mismatched_binding_before_mutation_or_recovery(self):
        """Every runtime entry point validates the shared binding before state changes."""
        with tempfile.TemporaryDirectory() as directory:
            harness = self._production_runtime_harness(Path(directory))
            wrong_digest = self.runner.APPROVED_PROTECTED_EXPORT_SEMANTIC_DIGEST
            with self.assertRaisesRegex(
                self.runner.CutoverError, "LIVE_EXPORT_REQUIRED_DIGEST_MISMATCH"
            ):
                self._validate_fixture_export(
                    harness["live_export_path"],
                    harness["migration"].parent / "finance-data-table-backup-v1.json",
                    self.runner.hashlib.sha256(harness["migration"].read_bytes()).hexdigest(),
                    wrong_digest,
                )
            state_path = harness["state_path"]
            initial_state = harness["initial_state"]
            run_runtime = harness["run_runtime"]

            unknown_export = run_runtime(
                FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST=wrong_digest,
            )
            self.assertNotEqual(unknown_export.returncode, 0)
            self.assertIn("LIVE_EXPORT_REQUIRED_DIGEST_MISMATCH", unknown_export.stderr)
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), initial_state)

            missing = run_runtime(FINANCE_FOUR_TABLE_OPERATION_NONCE="")
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("OPERATION_NONCE_REQUIRED", missing.stderr)
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), initial_state)

            failed_forward = run_runtime(FINANCE_FOUR_TABLE_INJECT_RECEIPT_FAILURE="1")
            self.assertNotEqual(failed_forward.returncode, 0)
            before_recovery = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(before_recovery["journal"]), 1)

            mismatched_recovery = run_runtime(
                FINANCE_FOUR_TABLE_RECOVER_JOURNAL="1",
                FINANCE_FOUR_TABLE_PROTECTED_QUIESCENCE_RECEIPT_DIGEST="f" * 64,
            )
            self.assertNotEqual(mismatched_recovery.returncode, 0)
            self.assertIn(
                "WRITER_LOCK_RECEIPT_PROTECTED_QUIESCENCE_RECEIPT_DIGEST_MISMATCH",
                mismatched_recovery.stderr,
            )
            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8")), before_recovery
            )

    def test_production_shell_recovers_forward_receipt_for_rollback(self):
        """Recover a committed receipt after output failure, then roll back."""
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            harness = self._production_runtime_harness(temp)
            migration = harness["migration"]
            live_export_path = harness["live_export_path"]
            identity_path = migration.parent / "finance-four-table-accepted-identity.json"
            lock_receipt_path = harness["lock_receipt_path"]
            exported = harness["exported"]
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
            identity["workflow_root"] = str(ROOT / "integrations/n8n/workflows")
            identity.pop("identity_sha256", None)
            identity["identity_sha256"] = self.runner.hashlib.sha256(
                self.runner._canonical_bytes(identity)
            ).hexdigest()
            identity_path.write_bytes(self.runner._canonical_bytes(identity))
            os.chmod(identity_path, 0o600)
            exported = json.loads(live_export_path.read_text(encoding="utf-8"))
            exported["accepted_identity_sha256"] = identity["identity_sha256"]
            exported.pop("export_sha256", None)
            exported["export_sha256"] = self.runner.hashlib.sha256(
                self.runner._canonical_bytes(exported)
            ).hexdigest()
            live_export_path.write_bytes(self.runner._canonical_bytes(exported))
            os.chmod(live_export_path, 0o600)
            lock_receipt = json.loads(lock_receipt_path.read_text(encoding="utf-8"))
            lock_receipt["accepted_identity_sha256"] = identity["identity_sha256"]
            lock_receipt["export_sha256"] = exported["export_sha256"]
            lock_receipt.pop("lock_receipt_sha256", None)
            lock_receipt["lock_receipt_sha256"] = self.runner.hashlib.sha256(
                self.runner._canonical_bytes(lock_receipt)
            ).hexdigest()
            lock_receipt_path.write_bytes(self.runner._canonical_bytes(lock_receipt))
            os.chmod(lock_receipt_path, 0o600)
            state_path = harness["state_path"]
            harness["reset_state"]()
            node_root = harness["node_root"]
            target_json = harness["target_json"]
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "test \"${1:-}\" = exec\n"
                "shift\n"
                "while [[ $# -gt 0 ]]; do\n"
                "  case \"$1\" in\n"
                "    -i) shift ;;\n"
                "    -e) export \"$2\"; if [[ \"$2\" == FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST=* ]]; then export \"FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST=$FINANCE_TEST_LIVE_EXPORT_DIGEST\"; fi; shift 2 ;;\n"
                "    *) break ;;\n"
                "  esac\n"
                "done\n"
                "shift\n"
                "test \"${1:-}\" = node\n"
                "runtime_argv=(\"$@\")\n"
                "if [[ \"${FINANCE_FOUR_TABLE_RECOVER_JOURNAL:-}\" = 1 ]]; then runtime_label=RECOVERY; else runtime_label=RUNTIME; fi\n"
                "if [[ \"$runtime_label\" = RECOVERY && \"${FINANCE_TEST_FAIL_RECOVERY:-}\" = 1 ]]; then echo RECOVERY_FAILED >&2; exit 42; fi\n"
                "if [[ \"$runtime_label\" = RECOVERY ]]; then printf 'recovery-stdout\\n'; printf 'recovery-stderr\\n' >&2; else printf 'initial-stdout\\n'; printf 'initial-stderr\\n' >&2; fi\n"
                "printf '%s' \"$runtime_label\" >> \"$FINANCE_TEST_DOCKER_LOG\"\n"
                "printf '\\t%s' \"${runtime_argv[@]}\" >> \"$FINANCE_TEST_DOCKER_LOG\"\n"
                "printf '\\n' >> \"$FINANCE_TEST_DOCKER_LOG\"\n"
                "shift\n"
                "export FINANCE_FOUR_TABLE_N8N_ROOT=\"$FINANCE_TEST_N8N_ROOT\"\n"
                "if [[ \"${FINANCE_FOUR_TABLE_OPERATION:-}\" = FORWARD && \"${FINANCE_FOUR_TABLE_RECOVER_JOURNAL:-}\" != 1 && \"${FINANCE_TEST_FAIL_RECEIPT:-}\" = 1 ]]; then export FINANCE_FOUR_TABLE_INJECT_RECEIPT_FAILURE=1; fi\n"
                "exec node \"$@\"\n",
                encoding="utf-8",
            )
            os.chmod(fake_docker, 0o700)
            real_python = self._install_digest_rewriting_python(fake_bin)
            environment = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "REAL_PYTHON": real_python,
                "FINANCE_TEST_LIVE_EXPORT_DIGEST": self.runner._export_semantic_digest(exported),
                "FINANCE_REPOSITORY_DIR": str(ROOT),
                "FINANCE_N8N_RECEIPT_DIR": str(migration.parent),
                "FINANCE_N8N_CONTAINER": "production-finance",
                "N8N_FINANCE_PROJECT_ID": exported["project_id"],
                "FINANCE_N8N_RUNTIME_MODE": "PRODUCTION_ONLY",
                "FINANCE_FOUR_TABLE_OPERATION_NONCE": self.runner.DEFAULT_OPERATION_NONCE,
                "FINANCE_FOUR_TABLE_PROTECTED_QUIESCENCE_RECEIPT_DIGEST": self.runner.APPROVED_QUIESCENCE_RECEIPT_DIGEST,
                "FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST": self.runner.APPROVED_PROTECTED_EXPORT_SEMANTIC_DIGEST,
                "FINANCE_FOUR_TABLE_CONTRACT_BIJECTION_DIGEST": self.runner.APPROVED_CONTRACT_BIJECTION_DIGEST,
                "FINANCE_N8N_LIVE_EXPORT": str(live_export_path),
                "FOUR_TABLE_FORWARD_ACK": self.runner.REQUIRED_FORWARD_ACK,
                "FOUR_TABLE_ROLLBACK_ACK": "",
                "FINANCE_TEST_FAIL_RECEIPT": "1",
                "FINANCE_TEST_FAIL_RECOVERY": "",
                "FINANCE_TEST_DOCKER_LOG": str(temp / "docker-runtime-argv.log"),
                "FINANCE_TEST_N8N_ROOT": str(node_root),
                "FINANCE_TEST_DB_STATE": str(state_path),
                "FINANCE_TEST_TARGETS_JSON": target_json,
                "FINANCE_FOUR_TABLE_RECOVER_JOURNAL": "",
                "FINANCE_FOUR_TABLE_INJECT_RECEIPT_FAILURE": "",
            }
            shell = ROOT / "integrations/n8n/setup-workflows/runner/run-four-table-cutover.sh"
            failed_forward = subprocess.run(
                ["bash", str(shell), "forward"], cwd=ROOT, env=environment,
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(failed_forward.returncode, 0, failed_forward.stderr)
            self.assertTrue((temp / "docker-runtime-argv.log").exists(), failed_forward.stderr)
            self.assertEqual(
                (temp / "docker-runtime-argv.log").read_text(encoding="utf-8").splitlines(),
                ["RUNTIME\tnode\t-", "RECOVERY\tnode\t-"],
            )
            forward_runtime = migration.parent / "finance-four-table-runtime-forward.json"
            self.assertTrue(forward_runtime.exists())
            runtime_stdout = migration.parent / "finance-four-table-runtime-forward.stdout.raw"
            runtime_stderr = migration.parent / "finance-four-table-runtime-forward.stderr.raw"
            recovery_stdout = migration.parent / "finance-four-table-runtime-forward-recovery.stdout.raw"
            recovery_stderr = migration.parent / "finance-four-table-runtime-forward-recovery.stderr.raw"
            for evidence in (runtime_stdout, runtime_stderr, recovery_stdout, recovery_stderr):
                self.assertTrue(evidence.exists())
                self.assertEqual(stat.S_IMODE(evidence.stat().st_mode), 0o600)
            self.assertIn("initial-stdout", runtime_stdout.read_text(encoding="utf-8"))
            self.assertIn("initial-stderr", runtime_stderr.read_text(encoding="utf-8"))
            self.assertIn("recovery-stdout", recovery_stdout.read_text(encoding="utf-8"))
            self.assertIn("recovery-stderr", recovery_stderr.read_text(encoding="utf-8"))
            self.assertNotEqual(runtime_stdout.read_bytes(), recovery_stdout.read_bytes())
            self.assertNotEqual(runtime_stderr.read_bytes(), recovery_stderr.read_bytes())
            recovered = json.loads(forward_runtime.read_text(encoding="utf-8"))
            self.assertEqual(recovered["operation"], "FORWARD")
            self.assertTrue(recovered["durable_journal"])
            self.assertEqual(len(json.loads(state_path.read_text(encoding="utf-8"))["journal"]), 1)

            environment["FOUR_TABLE_FORWARD_ACK"] = ""
            environment["FOUR_TABLE_ROLLBACK_ACK"] = self.runner.REQUIRED_ROLLBACK_ACK
            rollback = subprocess.run(
                ["bash", str(shell), "rollback"], cwd=ROOT, env=environment,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(rollback.returncode, 0, rollback.stderr)
            after_rollback = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(after_rollback["journal"]), 2)
            for reference in exported["references"]:
                node = next(
                    node
                    for node in after_rollback["workflows"][reference["workflow_id"]]["nodes"]
                    if node["id"] == reference["node_id"]
                )
                self.assertEqual(node["parameters"]["dataTableId"], reference["old_table_id"])

    def test_production_shell_preserves_initial_runtime_evidence_when_recovery_fails(self):
        """A failed recovery keeps the initial runtime status and evidence streams."""
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            harness = self._production_runtime_harness(temp)
            migration = harness["migration"]
            live_export_path = harness["live_export_path"]
            identity_path = migration.parent / "finance-four-table-accepted-identity.json"
            lock_receipt_path = harness["lock_receipt_path"]
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
            identity["workflow_root"] = str(ROOT / "integrations/n8n/workflows")
            identity.pop("identity_sha256", None)
            identity["identity_sha256"] = self.runner.hashlib.sha256(
                self.runner._canonical_bytes(identity)
            ).hexdigest()
            identity_path.write_bytes(self.runner._canonical_bytes(identity))
            os.chmod(identity_path, 0o600)
            exported = json.loads(live_export_path.read_text(encoding="utf-8"))
            exported["accepted_identity_sha256"] = identity["identity_sha256"]
            exported.pop("export_sha256", None)
            exported["export_sha256"] = self.runner.hashlib.sha256(
                self.runner._canonical_bytes(exported)
            ).hexdigest()
            live_export_path.write_bytes(self.runner._canonical_bytes(exported))
            os.chmod(live_export_path, 0o600)
            lock_receipt = json.loads(lock_receipt_path.read_text(encoding="utf-8"))
            lock_receipt["accepted_identity_sha256"] = identity["identity_sha256"]
            lock_receipt["export_sha256"] = exported["export_sha256"]
            lock_receipt.pop("lock_receipt_sha256", None)
            lock_receipt["lock_receipt_sha256"] = self.runner.hashlib.sha256(
                self.runner._canonical_bytes(lock_receipt)
            ).hexdigest()
            lock_receipt_path.write_bytes(self.runner._canonical_bytes(lock_receipt))
            os.chmod(lock_receipt_path, 0o600)
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "test \"${1:-}\" = exec\n"
                "shift\n"
                "while [[ $# -gt 0 ]]; do\n"
                "  case \"$1\" in\n"
                "    -i) shift ;;\n"
                "    -e) export \"$2\"; if [[ \"$2\" == FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST=* ]]; then export \"FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST=$FINANCE_TEST_LIVE_EXPORT_DIGEST\"; fi; shift 2 ;;\n"
                "    *) break ;;\n"
                "  esac\n"
                "done\n"
                "shift\n"
                "test \"${1:-}\" = node\n"
                "if [[ \"${FINANCE_FOUR_TABLE_RECOVER_JOURNAL:-}\" = 1 ]]; then echo RECOVERY_FAILED >&2; exit 42; fi\n"
                "printf 'initial-stdout\\n'\n"
                "printf 'initial-stderr\\n' >&2\n"
                "shift\n"
                "export FINANCE_FOUR_TABLE_N8N_ROOT=\"$FINANCE_TEST_N8N_ROOT\"\n"
                "export FINANCE_FOUR_TABLE_INJECT_RECEIPT_FAILURE=1\n"
                "exec node \"$@\"\n",
                encoding="utf-8",
            )
            os.chmod(fake_docker, 0o700)
            real_python = self._install_digest_rewriting_python(fake_bin)
            environment = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "REAL_PYTHON": real_python,
                "FINANCE_TEST_LIVE_EXPORT_DIGEST": self.runner._export_semantic_digest(exported),
                "FINANCE_REPOSITORY_DIR": str(ROOT),
                "FINANCE_N8N_RECEIPT_DIR": str(migration.parent),
                "FINANCE_N8N_CONTAINER": "production-finance",
                "N8N_FINANCE_PROJECT_ID": exported["project_id"],
                "FINANCE_N8N_RUNTIME_MODE": "PRODUCTION_ONLY",
                "FINANCE_FOUR_TABLE_OPERATION_NONCE": self.runner.DEFAULT_OPERATION_NONCE,
                "FINANCE_FOUR_TABLE_PROTECTED_QUIESCENCE_RECEIPT_DIGEST": self.runner.APPROVED_QUIESCENCE_RECEIPT_DIGEST,
                "FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST": self.runner.APPROVED_PROTECTED_EXPORT_SEMANTIC_DIGEST,
                "FINANCE_FOUR_TABLE_CONTRACT_BIJECTION_DIGEST": self.runner.APPROVED_CONTRACT_BIJECTION_DIGEST,
                "FINANCE_N8N_LIVE_EXPORT": str(live_export_path),
                "FOUR_TABLE_FORWARD_ACK": self.runner.REQUIRED_FORWARD_ACK,
                "FINANCE_TEST_N8N_ROOT": str(harness["node_root"]),
                "FINANCE_TEST_DB_STATE": str(harness["state_path"]),
                "FINANCE_TEST_TARGETS_JSON": harness["target_json"],
            }
            shell = ROOT / "integrations/n8n/setup-workflows/runner/run-four-table-cutover.sh"
            failed_forward = subprocess.run(
                ["bash", str(shell), "forward"], cwd=ROOT, env=environment,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(failed_forward.returncode, 1, failed_forward.stderr)
            runtime_stdout = migration.parent / "finance-four-table-runtime-forward.stdout.raw"
            runtime_stderr = migration.parent / "finance-four-table-runtime-forward.stderr.raw"
            recovery_stdout = migration.parent / "finance-four-table-runtime-forward-recovery.stdout.raw"
            recovery_stderr = migration.parent / "finance-four-table-runtime-forward-recovery.stderr.raw"
            self.assertIn("initial-stdout", runtime_stdout.read_text(encoding="utf-8"))
            self.assertIn("initial-stderr", runtime_stderr.read_text(encoding="utf-8"))
            self.assertEqual(recovery_stdout.read_text(encoding="utf-8"), "")
            self.assertIn("RECOVERY_FAILED", recovery_stderr.read_text(encoding="utf-8"))
            for evidence in (runtime_stdout, runtime_stderr, recovery_stdout, recovery_stderr):
                self.assertEqual(stat.S_IMODE(evidence.stat().st_mode), 0o600)

    def test_shell_disposable_forward_call_order(self):
        """The shell rejects the stale binding before canonical forward/rollback."""
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
                "integrations/n8n/setup-workflows/runner/n8n-cli-four-table-cutover.cjs",
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
                        "old_table_id": self.runner.LEGACY_TABLE_IDS[item["source_table"]],
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
                        "workflow_body_sha256": self.runner._workflow_body_digest(self._workflow_body(index)),
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
                "args=(\"$@\")\n"
                "has_required=0\n"
                "for ((index=0; index<${#args[@]}-1; index++)); do\n"
                "  if [[ \"${args[$index]}\" = \"--required-live-export-digest\" ]]; then has_required=1; args[$((index + 1))]=\"$FINANCE_TEST_LIVE_EXPORT_DIGEST\"; fi\n"
                "done\n"
                "if [[ \"$*\" == *'four_table_cutover.py rollback-runtime '* && \"$has_required\" = 0 ]]; then args+=(--required-live-export-digest \"$FINANCE_TEST_LIVE_EXPORT_DIGEST\"); fi\n"
                "exec \"$REAL_PYTHON\" \"${args[@]}\"\n",
                encoding="utf-8",
            )
            os.chmod(fake_python, 0o700)
            environment = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "REAL_PYTHON": real_python,
                "FINANCE_TEST_LIVE_EXPORT_DIGEST": self.runner._export_semantic_digest(live_export),
                "FINANCE_REPOSITORY_DIR": str(checkout),
                "FINANCE_N8N_RECEIPT_DIR": str(receipt_dir),
                "FINANCE_N8N_CONTAINER": "disposable-finance",
                "N8N_FINANCE_PROJECT_ID": "finance-test-project",
                "FINANCE_N8N_RUNTIME_MODE": "DISPOSABLE_ONLY",
                "FINANCE_FOUR_TABLE_OPERATION_NONCE": self.runner.DEFAULT_OPERATION_NONCE,
                "FINANCE_FOUR_TABLE_PROTECTED_QUIESCENCE_RECEIPT_DIGEST": self.runner.APPROVED_QUIESCENCE_RECEIPT_DIGEST,
                "FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST": self.runner.APPROVED_PROTECTED_EXPORT_SEMANTIC_DIGEST,
                "FINANCE_FOUR_TABLE_CONTRACT_BIJECTION_DIGEST": self.runner.APPROVED_CONTRACT_BIJECTION_DIGEST,
                "FINANCE_N8N_LIVE_EXPORT": str(live_export_path),
                "FOUR_TABLE_FORWARD_ACK": self.runner.REQUIRED_FORWARD_ACK,
                "FINANCE_TEST_PRE": str(receipt_dir / "pre.raw"),
                "FINANCE_TEST_POST": str(receipt_dir / "forward.raw"),
                "FINANCE_TEST_ROLLBACK_PRE": str(receipt_dir / "rollback-pre.raw"),
                "FINANCE_TEST_ROLLBACK_POST": str(receipt_dir / "rollback-post.raw"),
            }
            cli_lock_output = receipt_dir / "cli-lock.json"
            cli_preflight = subprocess.run(
                [
                    sys.executable,
                    str(checkout / "integrations/n8n/setup-workflows/runner/four_table_cutover.py"),
                    "preflight",
                    "--source-backup",
                    str(receipt_dir / "finance-data-table-backup-v1.json"),
                    "--migration-receipt",
                    str(receipt_dir / "data-table-migration-receipt.json"),
                    "--migration-receipt-sha256",
                    identity["migration_receipt_sha256"],
                    "--source-backup-sha256",
                    identity["source_backup_sha256"],
                    "--repository-root",
                    str(checkout),
                    "--project-id",
                    "finance-test-project",
                    "--accepted-identity",
                    str(identity_path),
                    "--operator-ack",
                    self.runner.REQUIRED_FORWARD_ACK,
                    "--runtime-action",
                    self.runner.FORWARD_RUNTIME_ACTION,
                    "--workflow-root",
                    str(checkout / "integrations/n8n/workflows"),
                    "--live-export",
                    str(live_export_path),
                    "--required-live-export-digest",
                    self.runner._export_semantic_digest(live_export),
                    "--operation-kind",
                    "FORWARD",
                    "--output",
                    str(cli_lock_output),
                ],
                cwd=checkout,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(cli_preflight.returncode, 0, cli_preflight.stderr)
            self.assertEqual(cli_preflight.stderr, "")
            decoder = json.JSONDecoder()
            cli_result, end = decoder.raw_decode(cli_preflight.stdout)
            self.assertEqual(cli_preflight.stdout[end:].strip(), "")
            self.assertEqual(cli_result, json.loads(cli_preflight.stdout))
            self.assertEqual(
                set(cli_result),
                {
                    "schema_version",
                    "operation",
                    "operation_nonce",
                    "protected_quiescence_receipt_digest",
                    "required_live_export_digest",
                    "contract_bijection_digest",
                    "migration_receipt_sha256",
                    "source_head",
                    "generator_head",
                    "accepted_identity_sha256",
                    "source_backup_sha256",
                    "project_id",
                    "workflow_export_sha256",
                    "reference_count",
                    "unresolved",
                    "replay_noop",
                    "lock_receipt_sha256",
                },
            )
            self.assertEqual(cli_result["schema_version"], self.runner.PRECONDITION_SCHEMA)
            self.assertEqual(cli_result["operation"], "FORWARD")
            self.assertEqual(cli_result["reference_count"], len(inventory))
            self.assertEqual(cli_result["unresolved"], [])
            self.assertTrue(cli_result["replay_noop"])
            cli_lock = json.loads(cli_lock_output.read_text(encoding="utf-8"))
            self.assertEqual(cli_lock["schema_version"], self.runner.LOCK_RECEIPT_SCHEMA)
            self.assertEqual(cli_lock["lock_receipt_sha256"], cli_result["lock_receipt_sha256"])
            command = [
                "bash",
                str(checkout / "integrations/n8n/setup-workflows/runner/run-four-table-cutover.sh"),
                "forward",
            ]
            environment["FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST"] = (
                "e6a226d0d7c6949e1d4263505f8bcf2405aba5f908eeb09bb7427ebb5f86f154"
            )
            stale = subprocess.run(
                command,
                cwd=checkout,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(stale.returncode, 0)
            self.assertEqual(log.read_text(encoding="utf-8"), "")
            environment["FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST"] = (
                "e233e0169eeb3b5df8f87982d9fd8224283e718f62d0829ed376332c49fd2b03"
            )
            completed = subprocess.run(
                command,
                cwd=checkout,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + " log=" + log.read_text(encoding="utf-8"))
            captured_preflight = receipt_dir / "finance-four-table-precondition.json"
            captured_text = captured_preflight.read_text(encoding="utf-8")
            captured_result, captured_end = decoder.raw_decode(captured_text)
            self.assertEqual(captured_text[captured_end:].strip(), "")
            self.assertEqual(captured_result, cli_result)
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
