from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
RUNNER_DIR = ROOT / "integrations" / "n8n" / "setup-workflows" / "runner"
PYTHON_RUNNER = RUNNER_DIR / "four_table_cutover.py"
CJS_RUNNER = RUNNER_DIR / "n8n-cli-four-table-cutover.cjs"
SHELL_RUNNER = RUNNER_DIR / "run-four-table-cutover.sh"
DIGEST_ADAPTER = RUNNER_DIR / "n8n-cli-finance-data-table-digest.cjs"
READBACK_PARSER = RUNNER_DIR / "parse_n8n_redacted_wrapper_output.py"
READBACK_FIXTURE = (
    ROOT / "tests" / "fixtures" / "n8n-2.36.2-data-table-digest-output.json"
)
INVENTORY = RUNNER_DIR / "finance-four-table-legacy-reference-inventory-v1.json"
APPROVED_INVENTORY_SHA256 = (
    "e414e2ee0e2a31aa9f7aec8bce03498b9f9e1d2c8598a9c193150f339248a6a3"
)
TARGETS = {
    "finance_ingestion_state",
    "finance_documents",
    "finance_actual_batches",
    "finance_ai_reviews",
}


def load_runner():
    spec = importlib.util.spec_from_file_location("four_table_cutover", PYTHON_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("four-table runner unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cjs_validation_harness() -> str:
    source = CJS_RUNNER.read_text(encoding="utf-8")
    declarations = source[
        source.index("const TARGET_NAMES") : source.index("function clone")
    ]
    helpers = source[
        source.index("function selectorId") : source.index(
            "function transactionTimeouts"
        )
    ]
    validator = source[
        source.index("function validateCanonicalGraph") : source.index(
            "function workflowReadback"
        )
    ]
    return f"""
{declarations}
{helpers}
{validator}
const fs = require('node:fs');
const workflows = JSON.parse(fs.readFileSync(0, 'utf8'));
const targetIds = new Map([
  ['finance_ingestion_state', 'target-ingestion'],
  ['finance_documents', 'target-documents'],
  ['finance_actual_batches', 'target-batches'],
  ['finance_ai_reviews', 'target-reviews'],
]);
for (const workflow of workflows) validateCanonicalGraph(workflow, targetIds);
process.stdout.write('validated');
"""


def write_runtime_receipt(runner, path: Path, schema: str) -> dict[str, object]:
    unsigned: dict[str, object] = {
        "schema_version": schema,
        "operation": "FORWARD",
        "durable_journal": True,
        "commit_protocol": "postgresql_synchronous_wal",
        "readback_verified": True,
        "action_count": len(runner.EXPECTED_REFERENCE_ACTIONS),
        "actions": [{} for _ in runner.EXPECTED_REFERENCE_ACTIONS],
    }
    unsigned["runtime_plan_receipt_sha256"] = hashlib.sha256(
        runner._canonical_bytes(unsigned)
    ).hexdigest()
    path.write_text(json.dumps(unsigned) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return unsigned


def run_cjs_validation(
    workflows: list[dict[str, object]],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", "-e", cjs_validation_harness()],
        input=json.dumps(workflows),
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )


class FourTableCutoverRunnerTests(unittest.TestCase):
    def test_python_runner_preserves_four_targets_and_source_contract(self) -> None:
        runner = load_runner()
        self.assertEqual(set(runner.TARGETS), TARGETS)
        self.assertEqual(
            runner.PRESERVED_SOURCE_TABLES, frozenset({"finance_source_contracts"})
        )
        self.assertEqual(
            runner.PRESERVED_LEGACY_AUDIT_TABLES,
            frozenset({"finance_pipeline_runs", "finance_mcp_requests"}),
        )
        self.assertEqual(
            set(runner.LEGACY_TABLE_IDS) - {"finance_source_contracts"},
            {
                "finance_source_cursors",
                "finance_archive_receipts",
                "finance_document_operations",
                "finance_pipeline_runs",
                "finance_reconciliations",
                "finance_mcp_requests",
            },
        )
        inventory = runner._load_legacy_reference_inventory()
        self.assertEqual(
            inventory["source_contract"],
            "integrations/n8n/data-tables.json@schema_version=4",
        )
        self.assertEqual(
            inventory["source_matrix"]["path"],
            "integrations/n8n/data-table-migration-matrix.json",
        )

    def test_canonical_source_preserves_legacy_audit_and_history_nodes(self) -> None:
        runner = load_runner()
        bundle = runner._canonical_source_bundle(
            SimpleNamespace(workflow_root=ROOT / "integrations" / "n8n" / "workflows"),
            "0" * 40,
            "1" * 40,
            "2" * 64,
        )
        workflows = {
            entry["path"]: json.loads(entry["content"]) for entry in bundle["files"]
        }
        expected_nodes = {
            "integrations/n8n/workflows/03-shared-statement-pipeline.json": (
                {
                    "Mark Terminal Readback Verified": "upsert",
                    "Read Back Terminal Pipeline Receipt": "get",
                    "Read Back Verified Terminal Receipt": "get",
                    "Upsert Terminal Pipeline Receipt": "upsert",
                },
                "finance_pipeline_runs",
            ),
            "integrations/n8n/workflows/10-finance-operations-status.json": (
                {
                    "Mark MCP Receipt Verified": "update",
                    "Read Back ACCEPTED MCP Request": "get",
                    "Read Back Terminal MCP Request": "get",
                    "Read Verified MCP Receipt": "get",
                    "Upsert ACCEPTED MCP Request": "upsert",
                    "Upsert Terminal MCP Request": "update",
                },
                "finance_mcp_requests",
            ),
        }
        for path, (operations, table) in expected_nodes.items():
            nodes = {node["name"]: node for node in workflows[path]["nodes"]}
            for name, operation in operations.items():
                with self.subTest(path=path, node=name):
                    node = nodes[name]
                    self.assertEqual(node["type"], "n8n-nodes-base.dataTable")
                    self.assertEqual(node["parameters"]["resource"], "row")
                    self.assertEqual(node["parameters"]["operation"], operation)
                    self.assertTrue(node["parameters"]["filters"]["conditions"])
                    selector = node["parameters"]["dataTableId"]
                    selected = (
                        selector["value"]
                        if isinstance(selector, dict) and selector.get("__rl") is True
                        else selector
                    )
                    self.assertEqual(selected, table)

    def test_shell_preflight_passes_repository_root_to_runner(self) -> None:
        source = SHELL_RUNNER.read_text(encoding="utf-8")
        preflight = source.split("preflight() {", 1)[1].split("\nrun_readback()", 1)[0]
        self.assertIn('--repository-root "$repo_dir"', preflight)

    def test_inventory_pin_and_references_are_coherent(self) -> None:
        raw = INVENTORY.read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(hashlib.sha256(raw).hexdigest(), APPROVED_INVENTORY_SHA256)
        inventory = json.loads(raw)
        self.assertEqual(
            inventory["schema_version"],
            "finance-four-table-legacy-reference-inventory-v1",
        )
        self.assertEqual(
            inventory["source_snapshot"]["finance_commit"],
            inventory["source_matrix"]["revision"],
        )
        for table in inventory["tables"]:
            self.assertIsInstance(table["source_table"], str)
            self.assertIn("target_table", table)
            for reference in table["node_references"]:
                self.assertEqual(reference["table"], table["source_table"])
                self.assertTrue((ROOT / reference["file"]).is_file(), reference["file"])
                self.assertIn(
                    reference["operation"],
                    {"get", "insert", "upsert", "update", "delete"},
                )

    def test_direct_pg_lifecycle_has_shutdown_safe_commit_boundary(self) -> None:
        source = CJS_RUNNER.read_text(encoding="utf-8")
        for marker in (
            "const pg = createRequire",
            "SET LOCAL lock_timeout",
            "SET LOCAL statement_timeout",
            "SET LOCAL synchronous_commit = 'on'",
            "await persistRecoveryJournal",
            "await lock.client.query('COMMIT')",
            "await lock.client.end()",
            "ROLLBACK_RECOVERY_REASON",
        ):
            self.assertIn(marker, source)
        self.assertNotRegex(
            source, r"require\(['\"]n8n-cli['\"]\)|executeWorkflow|Container\."
        )

    def test_folded_recovery_guards_are_failure_only_and_bound(self) -> None:
        python_source = PYTHON_RUNNER.read_text(encoding="utf-8")
        cjs_source = CJS_RUNNER.read_text(encoding="utf-8")
        shell_source = SHELL_RUNNER.read_text(encoding="utf-8")
        self.assertIn("limit=4 * 1024 * 1024", python_source)
        self.assertIn('len(artifacts["artifacts"]) > 4096', python_source)
        self.assertIn("CANONICAL_SOURCE_WORKFLOW_INVALID", python_source)
        self.assertIn("source_corpus_sha256", python_source)
        self.assertIn("FORWARD_RUNTIME_FAILURE", cjs_source)
        self.assertIn("LEGACY_FORWARD_JOURNAL_RECEIPT_MISMATCH", cjs_source)
        self.assertIn(
            "if (process.env.FINANCE_FOUR_TABLE_RECOVER_JOURNAL === '1')", cjs_source
        )
        self.assertIn("recover_runtime_receipt", shell_source)

    def test_shell_operator_and_runtime_gates_are_pinned(self) -> None:
        source = SHELL_RUNNER.read_text(encoding="utf-8")
        for marker in (
            "FINANCE_REPOSITORY_DIR:?",
            "FINANCE_N8N_RECEIPT_DIR:?",
            "FINANCE_N8N_RUNTIME_MODE:?",
            "FOUR_TABLE_FORWARD_REQUIRES_NAMED_OPERATOR_GATE",
            "FOUR_TABLE_ROLLBACK_REQUIRES_NAMED_OPERATOR_GATE",
            "DISPOSABLE_ONLY | PRODUCTION_ONLY",
            "flock -n 9",
            "node -e",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("source-binding", source)

    def test_python_runner_compiles_and_help_is_available(self) -> None:
        compile_result = subprocess.run(
            ["python3", "-m", "py_compile", str(PYTHON_RUNNER)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
        help_result = subprocess.run(
            ["python3", str(PYTHON_RUNNER), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("preflight", help_result.stdout)

    def test_cjs_consumer_allows_only_preserved_operational_selectors(self) -> None:
        source = CJS_RUNNER.read_text(encoding="utf-8")
        for marker in (
            "PRESERVED_OPERATIONAL_SELECTOR_NAMES",
            "'finance_pipeline_runs'",
            "'finance_mcp_requests'",
            "'finance_execution_failures'",
            "PRESERVED_OPERATIONAL_SELECTOR_IDS",
        ):
            self.assertIn(marker, source)

        def workflow(selector: object) -> dict[str, object]:
            return {
                "id": "preserved-selector",
                "active": False,
                "connections": {},
                "nodes": [
                    {
                        "id": "node",
                        "name": "node",
                        "type": "n8n-nodes-base.dataTable",
                        "parameters": {"resource": "row", "dataTableId": selector},
                    }
                ],
            }

        preserved = [
            "finance_source_contracts",
            "sha256:73b62207",
            "finance_pipeline_runs",
            "sha256:48eb19e5",
            "finance_mcp_requests",
            "sha256:3b9034f0",
            "finance_execution_failures",
            {
                "__rl": True,
                "mode": "name",
                "value": "finance_mcp_requests",
            },
        ]
        result = run_cjs_validation([workflow(selector) for selector in preserved])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        rejected = run_cjs_validation([workflow("finance_source_cursors")])
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("CANONICAL_SOURCE_TABLE_SELECTOR_INVALID", rejected.stderr)

    def test_python_generated_source_composes_with_cjs_consumer(self) -> None:
        runner = load_runner()
        bundle = runner._canonical_source_bundle(
            SimpleNamespace(workflow_root=ROOT / "integrations" / "n8n" / "workflows"),
            "0" * 40,
            "1" * 40,
            "2" * 64,
        )
        workflows = [json.loads(entry["content"]) for entry in bundle["files"]]
        result = run_cjs_validation(workflows)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "validated")

    def test_python_readback_uses_supported_transport_parser_call(self) -> None:
        runner = load_runner()
        raw = json.loads(READBACK_FIXTURE.read_text(encoding="utf-8"))["raw_stdout"]
        raw = raw.replace(
            '"bound":false,"sha256":null',
            '"bound":true,"sha256":"' + "a" * 64 + '"',
            1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "readback.raw"
            path.write_text(raw, encoding="utf-8")
            result = runner._parse_readback(path, "a" * 64, "FORWARD_POST")
        self.assertTrue(result["verified"])
        self.assertEqual(result["phase"], "FORWARD_POST")
        self.assertEqual(result["finance_tables"], 4)

    def test_forward_runtime_receipt_parser_accepts_bound_rollback_input(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "forward-runtime.json"
            expected = write_runtime_receipt(
                runner, path, "finance-four-table-runtime-plan-v1"
            )
            receipt, observed_sha = runner._read_forward_runtime_receipt(
                SimpleNamespace(
                    operation_kind="ROLLBACK",
                    forward_runtime_receipt=path,
                )
            )
        self.assertEqual(receipt, expected)
        self.assertEqual(len(observed_sha), 64)

    def test_disposable_rollback_precondition_forwards_runtime_schema(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_path = root / "forward-runtime.json"
            expected = write_runtime_receipt(
                runner, runtime_path, "finance-four-table-runtime-plan-v1"
            )
            args = SimpleNamespace(
                operation_kind="ROLLBACK",
                live_export=root / "live-export.json",
                source_backup=root / "source-backup.json",
                migration_receipt=root / "migration-receipt.json",
                forward_runtime_receipt=runtime_path,
                output=root / "precondition.json",
            )
            export = {
                "project_id": "synthetic-project",
                "export_sha256": "c" * 64,
                "reference_count": 33,
                "unresolved": [],
            }
            binding = {"required_live_export_digest": "b" * 64}
            migration_receipt = {"source_digest": "d" * 64}
            lock = {"lock_receipt_sha256": "e" * 64}
            with (
                mock.patch.object(
                    runner,
                    "_heads",
                    return_value=(
                        "0" * 40,
                        "1" * 40,
                        "a" * 64,
                        "b" * 64,
                        "2" * 64,
                    ),
                ),
                mock.patch.object(
                    runner,
                    "_source_and_receipt",
                    return_value=({}, migration_receipt, "a" * 64, "b" * 64),
                ),
                mock.patch.object(
                    runner,
                    "_binding_inputs",
                    return_value=binding,
                ),
                mock.patch.object(
                    runner,
                    "_validate_live_export",
                    return_value=export,
                ),
                mock.patch.object(runner, "_validate_forward_runtime_binding"),
                mock.patch.object(runner, "_lock_receipt", return_value=lock),
                mock.patch.object(runner, "_assert_currentness"),
                mock.patch.object(runner, "_validate_output_path"),
                mock.patch.object(runner, "_write_json"),
            ):
                result = runner.validate_preconditions(args)
        self.assertEqual(
            expected["schema_version"],
            "finance-four-table-runtime-plan-v1",
        )
        self.assertEqual(
            result["forward_runtime_receipt_schema"],
            "finance-four-table-runtime-plan-v1",
        )

    def test_shell_forwards_runtime_receipt_for_disposable_rollback(self) -> None:
        source = SHELL_RUNNER.read_text(encoding="utf-8")
        rollback_args = source.split("rollback_receipt_args=()", 1)[1].split(
            "resolver_args=()", 1
        )[0]
        self.assertIn(
            "rollback_receipt_args+=(--forward-runtime-receipt "
            '"$forward_runtime_receipt")',
            rollback_args,
        )
        self.assertNotIn("PRODUCTION_ONLY", rollback_args)
        self.assertGreaterEqual(source.count('"${rollback_receipt_args[@]}"'), 2)

    def test_digest_adapter_composes_phase_contract_with_closed_table_set(self) -> None:
        source = DIGEST_ADAPTER.read_text(encoding="utf-8")
        contract = source[
            source.index("const CANONICAL_TABLE_NAMES") : source.index(
                "const originalInit"
            )
        ]
        harness = f"""
const crypto = require('node:crypto');
{contract}
const tables = [...CANONICAL_TABLE_NAMES, ...PRESERVED_TABLE_NAMES]
  .map((name) => ({{ name, id: `id-${{name}}` }}));
assertAllowedProjectTables({{ count: tables.length, data: tables }});
const observedTargets = CANONICAL_TABLE_NAMES.map((name) => ({{ name, row_count: 0 }}));
const forwardPre = readbackReceipt('FORWARD_PRE', observedTargets, 0);
if (forwardPre.status !== 'FORWARD_PRE_READBACK' ||
    forwardPre.finance_tables !== 0 || forwardPre.tables.length !== 0) process.exit(2);
const rollbackPre = readbackReceipt('ROLLBACK_PRE', observedTargets, 0);
if (rollbackPre.status !== 'VERIFIED' || rollbackPre.finance_tables !== 4) process.exit(3);
try {{
  readbackReceipt('FORWARD_PRE', [{{ ...observedTargets[0], row_count: 1 }}, ...observedTargets.slice(1)], 1);
  process.exit(5);
}} catch (error) {{
  if (error.message !== 'FORWARD_PRE_READBACK_MUST_BE_OBSERVED_EMPTY') throw error;
}}
try {{
  assertTargetSchemaDigest('finance_documents', [{{ name: 'wrong', type: 'string' }}]);
  process.exit(6);
}} catch (error) {{
  if (error.message !== 'TARGET_SCHEMA_DIGEST_MISMATCH:finance_documents') throw error;
}}
try {{
  assertAllowedProjectTables({{ count: tables.length + 1, data: [...tables, {{ name: 'unexpected' }}] }});
  process.exit(4);
}} catch (error) {{
  if (error.message !== 'CLOSED_FINANCE_DATA_TABLE_SET_REQUIRED') throw error;
}}
"""
        result = subprocess.run(
            ["node", "-e", harness],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_readback_parser_requires_explicit_forward_pre_phase(self) -> None:
        runner = load_runner()
        raw = json.loads(READBACK_FIXTURE.read_text(encoding="utf-8"))["raw_stdout"]
        prefix = "finance data table digest verified:"
        payload = json.loads(
            next(
                line.removeprefix(prefix)
                for line in raw.splitlines()
                if line.startswith(prefix)
            )
        )
        payload["migration_receipt"] = {
            "schema_version": "data-table-migration-receipt-v1",
            "required": True,
            "bound": True,
            "sha256": "a" * 64,
        }
        payload.update(
            {
                "status": "FORWARD_PRE_READBACK",
                "phase": "FORWARD_PRE",
                "finance_tables": 0,
                "tables": [],
                "total_rows": 0,
                "digest_sha256": hashlib.sha256(b"[]").hexdigest(),
            }
        )
        schema = json.loads(
            (
                ROOT
                / "integrations"
                / "n8n"
                / "schemas"
                / "finance-data-table-readback-receipt-v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(payload)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "pre.raw"
            path.write_text(prefix + json.dumps(payload, separators=(",", ":")) + "\n")
            observed = runner._parse_readback(path, "a" * 64, "FORWARD_PRE")
            self.assertEqual(observed["finance_tables"], 0)
            with self.assertRaisesRegex(runner.CutoverError, "READBACK_PHASE_MISMATCH"):
                runner._parse_readback(path, "a" * 64, "ROLLBACK_PRE")

    def test_all_rebound_node_fields_conform_to_migration_matrix(self) -> None:
        runner = load_runner()
        matrix = json.loads(
            (
                ROOT / "integrations" / "n8n" / "data-table-migration-matrix.json"
            ).read_text(encoding="utf-8")
        )
        bundle = runner._canonical_source_bundle(
            SimpleNamespace(workflow_root=ROOT / "integrations" / "n8n" / "workflows"),
            "0" * 40,
            "1" * 40,
            "2" * 64,
        )
        workflows = {
            entry["path"]: json.loads(entry["content"]) for entry in bundle["files"]
        }
        for reference in runner._reference_inventory():
            target = reference["canonical_table_name"]
            if target is None:
                continue
            node = next(
                item
                for item in workflows[reference["workflow_path"]]["nodes"]
                if item["name"] == reference["node_name"]
            )
            parameters = node["parameters"]
            target_fields = set(matrix["target_schemas"][target]["columns"])
            written = set(parameters.get("columns", {}).get("value", {}))
            filtered = {
                condition["keyName"]
                for condition in parameters.get("filters", {}).get("conditions", [])
            }
            self.assertLessEqual(
                written | filtered, target_fields, reference["reference_id"]
            )
        reconciliation = workflows[
            "integrations/n8n/workflows/03-shared-statement-pipeline.json"
        ]
        nodes = {node["name"]: node for node in reconciliation["nodes"]}
        self.assertEqual(
            set(
                nodes["Upsert Reconciliation Receipt"]["parameters"]["columns"]["value"]
            ),
            {
                "source_code",
                "period_key",
                "reconciliation_version",
                "statement_sha256",
                "verification_artifact_sha256",
                "reconciliation_state",
                "reconciliation_difference_minor",
                "idempotency_key",
                "reconciliation_verified_at",
                "updated_at",
            },
        )
        upsert_parameters = nodes["Upsert Reconciliation Receipt"]["parameters"]
        self.assertEqual(
            upsert_parameters["filters"]["conditions"],
            [
                {
                    "keyName": "idempotency_key",
                    "condition": "eq",
                    "keyValue": "={{ $('Prepare Outbox Intent').first().json.idempotency_key }}",
                }
            ],
        )
        self.assertEqual(
            upsert_parameters["columns"]["value"]["idempotency_key"],
            "={{ $('Prepare Outbox Intent').first().json.idempotency_key }}",
        )
        consumer_code = nodes["Validate Reconciliation Readback"]["parameters"][
            "jsCode"
        ]
        self.assertIn("row.reconciliation_state", consumer_code)
        self.assertIn("row.verification_artifact_sha256", consumer_code)
        self.assertNotRegex(consumer_code, r"\brow\.state\b")
        self.assertNotIn("row.actual_verification_sha256", consumer_code)
        self.assertNotIn("row.cashback_close_id", consumer_code)

    def test_forward_replay_reuses_first_legacy_journal_without_persisting(
        self,
    ) -> None:
        source = CJS_RUNNER.read_text(encoding="utf-8")
        replay_helpers = source[
            source.index("function replayValidationExport") : source.index(
                "async function recoverRuntimeJournal"
            )
        ]
        execute = source[
            source.index("async function execute()") : source.index(
                "async function writeRuntimeReceipt"
            )
        ]
        harness = f"""
const crypto = require('node:crypto');
const projectId = 'project-1';
const JOURNAL_TABLE = 'journal';
const RUNTIME_SCHEMA = 'finance-four-table-runtime-plan-v2';
const LEGACY_RUNTIME_SCHEMA = 'finance-four-table-runtime-plan-v1';
const WORKFLOW_BODY_FIELDS = ['marker'];
const operation = 'FORWARD';
const digest = (value) => crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');
const originalReadback = [{{ workflow_id: 'wf', workflow_body_sha256: 'canonical' }}];
const originalRollback = [{{ id: 'wf', marker: 'legacy' }}];
const original = {{
  schema_version: RUNTIME_SCHEMA,
  operation: 'FORWARD',
  export_sha256: 'original-export',
  readback_digest_sha256: digest(originalReadback),
  rollback_workflows_sha256: digest(originalRollback),
  credential_state_digest_after: 'credential-state',
  workflow_credential_objects_digest_after: 'workflow-credentials',
  workflow_revision_digest_after: 'workflow-revisions',
  actions: [{{ reference_id: 'ref', revision_id: 'legacy-revision' }}],
}};
const replacement = {{ ...original, export_sha256: 'replay-export', runtime_plan_receipt_sha256: 'replacement' }};
const graph = {{
  workflows: new Map([['wf', 'revision']]),
  references: new Map(),
}};
const exported = {{
  export_sha256: 'replay-export',
  references: [{{ reference_id: 'ref', revision_id: 'replay-revision' }}],
}};
const lock = {{
  resource: 'resource',
  binding: {{
    operation_nonce: 'nonce',
    protected_quiescence_receipt_digest: 'q',
    required_live_export_digest: 'e',
    contract_bijection_digest: 'b',
  }},
  client: {{
    queries: [],
    async query(sql) {{
      this.queries.push(sql);
      if (String(sql).startsWith('SELECT receipt')) return {{
        rows: [
          {{ receipt: original, rollback_workflows: originalRollback }},
          {{ receipt: replacement, rollback_workflows: [{{ id: 'wf', marker: 'canonical' }}] }},
        ],
      }};
      return {{ rows: [] }};
    }},
    async end() {{}},
  }},
}};
const canonical = new Map([['wf', {{ id: 'wf', marker: 'canonical' }}]]);
const current = new Map([['wf', {{ id: 'wf', marker: 'canonical' }}]]);
let persisted = 0;
let emitted = null;
function decode() {{ return exported; }}
function validateExport() {{ return graph; }}
function canonicalSourceFromInput() {{ return {{ workflows: canonical, sha256: 'canonical-source' }}; }}
async function acquireProjectLock() {{ return lock; }}
async function verifyInFlight() {{}}
async function verifyTargets() {{}}
async function credentialState() {{ return {{ values: [], digest: 'credential-state' }}; }}
function validateCredentialBindings() {{ return new Map(); }}
function credentialOriginBitset() {{ return ''; }}
function workflowCredentialObjectsDigest() {{ return 'workflow-credentials'; }}
function workflowRevisionDigest() {{ return 'workflow-revisions'; }}
function workflowOpaqueCredentialObjectsDigest() {{ return 'opaque'; }}
async function loadWorkflows() {{ return current; }}
function findReferences(_graph, workflows) {{
  return [{{
    reference: {{ canonical_table_id: 'target' }},
    oldMatches: workflows.get('wf').marker === 'legacy',
    targetMatches: workflows.get('wf').marker === 'canonical',
  }}];
}}
function applyForward() {{ return {{ alreadyApplied: true, expected: canonical, changed: new Map() }}; }}
function workflowReadback() {{ return originalReadback; }}
function sameJson(left, right) {{ return JSON.stringify(left) === JSON.stringify(right); }}
function allCredentialOrigins() {{ return true; }}
async function persistRecoveryJournal() {{ persisted += 1; }}
function validateBinding() {{}}
function validateForwardReceipt(receipt, historicalExport) {{
  if (historicalExport.export_sha256 !== receipt.export_sha256) throw new Error('historical export not rebound');
}}
async function writeRuntimeReceipt(receipt) {{ emitted = receipt; }}
{replay_helpers}
{execute}
(async () => {{
  const receipt = await execute();
  if (receipt !== original || emitted !== original || persisted !== 0) process.exit(2);
  if (lock.client.queries.filter((query) => String(query).startsWith('SELECT receipt')).length !== 1) process.exit(3);
  if (!lock.client.queries.includes('COMMIT')) process.exit(4);
}})().catch((error) => {{ console.error(error); process.exit(1); }});
"""
        result = subprocess.run(
            ["node", "-e", harness],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shell_rollback_restore_runs_between_runtime_and_post_readback(
        self,
    ) -> None:
        source = SHELL_RUNNER.read_text(encoding="utf-8")
        main_case = source.rsplit('case "$operation" in', 1)[1]
        rollback_flow = main_case.split("\nrollback)\n", 1)[1].split("\n  ;;\nesac", 1)[
            0
        ]
        self.assertLess(
            rollback_flow.index("run_runtime"),
            rollback_flow.index("run_rollback_restore"),
        )
        self.assertLess(
            rollback_flow.index("run_rollback_restore"),
            rollback_flow.index('run_readback "$post_readback" ROLLBACK_POST'),
        )
        function_body = source[
            source.index("run_rollback_restore() {") : source.index(
                "\n\nrun_readback()"
            )
        ]
        harness = f"""
set -euo pipefail
{function_body}
resolver_args=()
runner_dir=/runner
source_backup=/receipts/source.json
migration_receipt=/receipts/migration.json
migration_sha={"a" * 64}
source_backup_sha={"b" * 64}
FINANCE_FOUR_TABLE_OPERATION_NONCE=nonce
FINANCE_FOUR_TABLE_PROTECTED_QUIESCENCE_RECEIPT_DIGEST={"c" * 64}
FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST={"d" * 64}
FINANCE_FOUR_TABLE_CONTRACT_BIJECTION_DIGEST={"e" * 64}
repo_dir=/repo
N8N_FINANCE_PROJECT_ID=project-1
accepted_identity=/receipts/identity.json
operator_ack=rollback-ack
runtime_action=rollback-action
workflow_root=/repo/workflows
live_export=/receipts/export.json
lock_receipt=/receipts/lock.json
runtime_state=/receipts/state.json
runtime_proof=/receipts/proof.json
log="$PWD/rollback-runtime.log"
python3() {{ printf '%s\\n' "$*" >"$log"; }}
chmod() {{ :; }}
run_rollback_restore
grep -F 'rollback-runtime' "$log" >/dev/null
grep -F -- '--runtime-state /receipts/state.json' "$log" >/dev/null
grep -F -- '--output /receipts/proof.json' "$log" >/dev/null
"""
        result = subprocess.run(
            ["bash", "-c", harness],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
