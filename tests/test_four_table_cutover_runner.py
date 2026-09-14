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


def target_runtime_harness() -> str:
    source = CJS_RUNNER.read_text(encoding="utf-8")
    target_helpers = source[
        source.index("function quoteIdentifier") : source.index(
            "async function verifyInFlight"
        )
    ]
    rollback_helpers = source[
        source.index("function validateRollbackTargets") : source.index(
            "function validateLockReceipt"
        )
    ]
    execute = source[
        source.index("async function execute()") : source.index(
            "async function writeRuntimeReceipt"
        )
    ]
    preamble = r"""
const crypto = require('node:crypto');
let operation = 'FORWARD';
const projectId = 'project-1';
const JOURNAL_TABLE = 'finance_four_table_cutover_journal';
const LEGACY_RUNTIME_SCHEMA = 'finance-four-table-runtime-plan-v1';
const PREVIOUS_RUNTIME_SCHEMA = 'finance-four-table-runtime-plan-v2';
const RUNTIME_SCHEMA = 'finance-four-table-runtime-plan-v3';
const TARGET_NAMES = new Set([
  'finance_ingestion_state',
  'finance_documents',
  'finance_actual_batches',
  'finance_ai_reviews',
]);
const COMPATIBILITY_TABLE_NAMES = new Set(TARGET_NAMES);
const TARGET_SYSTEM_COLUMNS = ['id', 'createdAt', 'updatedAt'];
const WORKFLOW_BODY_FIELDS = ['marker'];
const canonical = (value) => {
  if (value instanceof Date) return value.toISOString();
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  }
  return value;
};
const clone = (value) => JSON.parse(JSON.stringify(value));
const digest = (value) => crypto.createHash('sha256').update(`${JSON.stringify(canonical(value))}\n`).digest('hex');
const digestWithoutNewline = (value) => crypto.createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
const sameJson = (left, right) => JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
function exactKeys(value, expected, code) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).sort().join(',') !== [...expected].sort().join(',')) throw new Error(code);
}
const columnsByName = new Map([
  ['finance_actual_batches', [{ name: 'idempotency_key', type: 'string' }]],
  ['finance_ai_reviews', [{ name: 'idempotency_key', type: 'string' }]],
  ['finance_documents', [{ name: 'document_id', type: 'string' }]],
  ['finance_ingestion_state', [{ name: 'source_code', type: 'string' }]],
]);
const valuesByName = new Map([
  ['finance_actual_batches', [{ idempotency_key: 'batch-1' }]],
  ['finance_ai_reviews', [{ idempotency_key: 'review-1' }]],
  ['finance_documents', [{ document_id: 'document-1' }]],
  ['finance_ingestion_state', [{ source_code: 'outlook' }]],
]);
const targets = new Map([...TARGET_NAMES].sort().map((name) => [name, {
  name,
  tableId: `target-${name}`,
  columns: columnsByName.get(name),
  schema_sha256: digestWithoutNewline(columnsByName.get(name)),
  rows: valuesByName.get(name),
}]));
const graph = {
  targetIds: new Map([...targets].map(([name, target]) => [name, target.tableId])),
  workflows: new Map([['wf', 'legacy-revision']]),
  workflowBodyDigests: new Map([['wf', 'body']]),
};
let workflowState = { id: 'wf', marker: 'legacy', versionId: 'legacy-revision', nodes: [], active: false };
let rowsById = new Map([...targets.values()].map((target) => [target.tableId, []]));
let transactionSnapshot = null;
let persisted = [];
let emitted = null;
let forwardReceipt = null;
let forwardRollbackTargets = null;
const mutationSql = [];
const client = {
  async query(sql, parameters = []) {
    const text = String(sql);
    if (text === 'COMMIT') {
      transactionSnapshot = null;
      return { rows: [] };
    }
    if (text === 'ROLLBACK') {
      if (transactionSnapshot) {
        rowsById = new Map(transactionSnapshot.rows.map(([id, rows]) => [id, clone(rows)]));
        workflowState = clone(transactionSnapshot.workflow);
      }
      transactionSnapshot = null;
      return { rows: [] };
    }
    if (text.includes('FROM data_table\n')) {
      return { rows: [...targets.values()].map((target) => ({ id: target.tableId, name: target.name })) };
    }
    if (text.includes('FROM data_table_column')) {
      return { rows: [...targets.values()].flatMap((target) =>
        target.columns.map((column, index) => ({ table_id: target.tableId, ...column, index }))) };
    }
    if (text.startsWith('SELECT * FROM ')) {
      const tableId = text.match(/data_table_user_(target-[^"]+)/)[1];
      return { rows: clone(rowsById.get(tableId)) };
    }
    if (text.startsWith('DELETE FROM ')) {
      mutationSql.push(text);
      const tableId = text.match(/data_table_user_(target-[^"]+)/)[1];
      rowsById.set(tableId, []);
      return { rows: [] };
    }
    if (text.startsWith('INSERT INTO ')) {
      mutationSql.push(text);
      const tableId = text.match(/data_table_user_(target-[^"]+)/)[1];
      const fieldText = text.match(/\n\s*\(([^)]+)\)\n\s*OVERRIDING SYSTEM VALUE/)[1];
      const fields = fieldText.split(',').map((field) => field.trim().replaceAll('"', ''));
      const rows = rowsById.get(tableId);
      for (let offset = 0; offset < parameters.length; offset += fields.length) {
        const row = Object.fromEntries(fields.map((field, index) => [field, parameters[offset + index]]));
        row.createdAt ??= '2026-01-01T00:00:00.000Z';
        row.updatedAt ??= '2026-01-01T00:00:00.000Z';
        rows.push(row);
      }
      return { rows: [] };
    }
    if (text.startsWith('SELECT rollback_targets')) {
      return { rows: [{ rollback_targets: clone(forwardRollbackTargets) }] };
    }
    return { rows: [] };
  },
  async end() {},
};
function decode(name) {
  if (name === 'FINANCE_FOUR_TABLE_FORWARD_RECEIPT_B64') return forwardReceipt;
  return { export_sha256: 'export', references: [] };
}
function decodedSha256() { return digest(forwardReceipt); }
function validateExport() { return graph; }
function canonicalSourceFromInput() {
  return {
    workflows: new Map([['wf', { id: 'wf', marker: 'canonical' }]]),
    targets,
    sha256: 'canonical-source',
    targetProjectionSha256: 'projection',
    targetDigest: 'target-digest',
  };
}
async function acquireProjectLock() {
  transactionSnapshot = {
    rows: [...rowsById].map(([id, rows]) => [id, clone(rows)]),
    workflow: clone(workflowState),
  };
  return {
    client,
    resource: 'finance_four_table_cutover:project-1',
    binding: {
      operation_nonce: 'nonce',
      protected_quiescence_receipt_digest: 'q'.repeat(64),
      required_live_export_digest: 'e'.repeat(64),
      contract_bijection_digest: 'b'.repeat(64),
    },
  };
}
async function verifyInFlight() {}
async function credentialState() { return { values: [], digest: 'c'.repeat(64) }; }
function validateCredentialBindings() { return new Map(); }
function credentialOriginBitset() { return ''; }
function workflowCredentialObjectsDigest() { return 'd'.repeat(64); }
function workflowRevisionDigest() { return workflowState.versionId; }
function workflowOpaqueCredentialObjectsDigest() { return 'o'.repeat(64); }
function allCredentialOrigins() { return true; }
function credentialOriginsFromBitset() { return new Map(); }
function credentialOriginDigest(value) { return digest(value); }
function credentialBindingForNode() { return null; }
function credentialContractSummary() {
  return { credential_contract_digest: digest([]), credential_binding_count: 0, credential_leaf_count: 0 };
}
function credentialLeavesFromEnvironment() { return []; }
function assertWorkflow() {}
async function loadWorkflows() { return new Map([['wf', clone(workflowState)]]); }
function findReferences() {
  return Array.from({ length: 33 }, (_, index) => ({
    reference: {
      reference_id: `ref-${index}`,
      workflow_id: 'wf',
      revision_id: 'legacy-revision',
      node_id: `node-${index}`,
      canonical_table_id: 'target-finance_documents',
    },
    workflow: clone(workflowState),
    node: {},
    selector: 'legacy',
    oldMatches: workflowState.marker === 'legacy',
    targetMatches: workflowState.marker === 'canonical',
  }));
}
function applyForward() {
  const alreadyApplied = workflowState.marker === 'canonical';
  return {
    alreadyApplied,
    expected: new Map([['wf', { id: 'wf', marker: 'canonical' }]]),
    changed: alreadyApplied ? new Map() : new Map([['wf', { marker: 'canonical' }]]),
  };
}
function workflowReadback(workflows) {
  return [...workflows.values()].map((workflow) => ({ marker: workflow.marker }));
}
async function updateWorkflows(_client, changed) {
  if (changed.size > 0) workflowState = { ...workflowState, marker: changed.get('wf').marker };
  workflowState.versionId = workflowState.marker === 'canonical' ? 'canonical-revision' : 'legacy-revision';
}
async function persistRecoveryJournal(_client, receipt, rollbackWorkflows, rollbackTargets) {
  persisted.push({ receipt, rollbackWorkflows: clone(rollbackWorkflows), rollbackTargets: clone(rollbackTargets) });
  if (receipt.operation === 'FORWARD') {
    forwardReceipt = receipt;
    forwardRollbackTargets = clone(rollbackTargets);
  }
}
async function loadForwardReplayJournal(_client, _graph, _lock, source, _exported, _readback, state) {
  if (state.targetReadbackDigest !== forwardReceipt.target_readback_sha256 ||
      source.targetProjectionSha256 !== forwardReceipt.target_projection_sha256) {
    throw new Error('FORWARD_REPLAY_TARGET_STATE_MISMATCH');
  }
  return forwardReceipt;
}
async function loadRollbackWorkflows() {
  return new Map([['wf', { id: 'wf', marker: 'legacy' }]]);
}
function validateForwardReceipt() {}
function replayValidationExport(value) { return value; }
function validateBinding() {}
async function verifyLegacyForwardJournal() {}
function rollbackSelectorReceipt() { throw new Error('legacy rollback unavailable'); }
async function writeRuntimeReceipt(receipt) { emitted = receipt; }
"""
    scenario = r"""
(async () => {
  const mode = process.argv[1] || 'roundtrip';
  if (mode === 'failure') {
    process.env.FINANCE_FOUR_TABLE_INJECT_FAILURE_AFTER_TARGET = 'finance_documents';
    try {
      await execute();
      process.exit(20);
    } catch (error) {
      if (error.message !== 'INJECTED_FAILURE_AFTER_TARGET:finance_documents') throw error;
    }
    if ([...rowsById.values()].some((rows) => rows.length !== 0) ||
        workflowState.marker !== 'legacy' || persisted.length !== 0) process.exit(21);
    delete process.env.FINANCE_FOUR_TABLE_INJECT_FAILURE_AFTER_TARGET;
    const recovered = await execute();
    const recoveredState = await loadTargetState(client, targets);
    if ([...recoveredState].some(([name, table]) => !sameJson(table.userRows, targets.get(name).rows)) ||
        targetStateDigest(recoveredState) !== recovered.target_readback_sha256) process.exit(22);
    process.stdout.write('failure-atomic');
    return;
  }
  const first = await execute();
  const forwardState = await loadTargetState(client, targets);
  if ([...forwardState].some(([name, table]) =>
    !sameJson(table.userRows, targets.get(name).rows) ||
    table.systemRows.some((row) => row.id >= 0))) process.exit(2);
  if (first.preserved_table_writes !== false ||
      first.target_readback_sha256 !== targetStateDigest(forwardState) ||
      first.rollback_targets_sha256 !== digest(forwardRollbackTargets) ||
      !sameJson(first.target_row_counts, Object.fromEntries(
        [...targets].map(([name, target]) => [name, target.rows.length]),
      )) ||
      persisted.length !== 1) process.exit(3);
  const mutationCount = mutationSql.length;
  const replay = await execute();
  if (replay !== first || mutationSql.length !== mutationCount || persisted.length !== 1) process.exit(4);
  operation = 'ROLLBACK';
  const rollback = await execute();
  if ([...rowsById.values()].some((rows) => rows.length !== 0) ||
      workflowState.marker !== 'legacy' ||
      rollback.target_rows_restored !== true ||
      rollback.preserved_table_writes !== false) process.exit(5);
  if (mutationSql.some((sql) => /finance_(source|archive|pipeline|mcp|reconciliations)/.test(sql))) process.exit(6);
  process.stdout.write('roundtrip');
})().catch((error) => { console.error(error); process.exit(1); });
"""
    return preamble + target_helpers + rollback_helpers + execute + scenario


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
        raw = raw.replace('"scope":', '"phase":"FORWARD_POST","scope":', 1)
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
                mock.patch.object(runner, "_read_forward_cutover_receipt"),
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
            '--forward-runtime-receipt "$forward_runtime_receipt"',
            rollback_args,
        )
        self.assertIn('--forward-receipt "$forward_receipt"', rollback_args)
        self.assertNotIn("PRODUCTION_ONLY", rollback_args)
        self.assertGreaterEqual(source.count('"${rollback_receipt_args[@]}"'), 2)
        self.assertNotIn('cp -- "$runtime_json" "$forward_runtime_receipt"', source)
        self.assertIn(
            'credential_bindings="$repo_dir/integrations/n8n/credential-bindings.json"',
            source,
        )
        self.assertNotIn("FINANCE_FOUR_TABLE_CREDENTIAL_BINDINGS:-", source)
        self.assertIn('--canonical-source-input "$canonical_source"', source)
        self.assertIn("FINANCE_FOUR_TABLE_CANONICAL_SOURCE_FILE_SHA256", source)

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
    forwardPre.finance_tables !== 4 || forwardPre.tables.length !== 4) process.exit(2);
const rollbackPre = readbackReceipt('ROLLBACK_PRE', observedTargets, 0);
if (rollbackPre.status !== 'VERIFIED' || rollbackPre.finance_tables !== 4) process.exit(3);
const replayPre = readbackReceipt(
  'FORWARD_PRE',
  [{{ ...observedTargets[0], row_count: 1 }}, ...observedTargets.slice(1)],
  1,
);
if (replayPre.finance_tables !== 4 || replayPre.total_rows !== 1) process.exit(5);
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
            self.assertEqual(observed["finance_tables"], 4)
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
const RUNTIME_SCHEMA = 'finance-four-table-runtime-plan-v3';
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
  canonical_source_sha256: 'canonical-source',
  readback_digest_sha256: digest(originalReadback),
  rollback_workflows_sha256: digest(originalRollback),
  target_readback_sha256: 'target-state',
  target_projection_sha256: 'projection',
  target_digest: 'target-digest',
  credential_state_digest_after: 'credential-state',
  workflow_credential_objects_digest_after: 'workflow-credentials',
  workflow_revision_digest_after: 'old-workflow-revisions',
  actions: [{{ reference_id: 'ref', revision_id: 'legacy-revision' }}],
}};
const replacement = {{
  ...original,
  export_sha256: 'replay-export',
  workflow_revision_digest_after: 'workflow-revisions',
  runtime_plan_receipt_sha256: 'replacement',
}};
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
          {{ receipt: original, rollback_workflows: originalRollback, rollback_targets: [] }},
          {{ receipt: replacement, rollback_workflows: originalRollback, rollback_targets: [] }},
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
function canonicalSourceFromInput() {{ return {{ workflows: canonical, targets: new Map(), sha256: 'canonical-source', targetProjectionSha256: 'projection', targetDigest: 'target-digest' }}; }}
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
async function applyTargetProjection() {{ return {{ alreadyApplied: true, after: new Map() }}; }}
function targetStateDigest() {{ return 'target-state'; }}
function workflowReadback() {{ return originalReadback; }}
function sameJson(left, right) {{ return JSON.stringify(left) === JSON.stringify(right); }}
function allCredentialOrigins() {{ return true; }}
async function persistRecoveryJournal() {{ persisted += 1; }}
function validateBinding() {{}}
function validateRollbackTargets() {{}}
function validateForwardReceipt(receipt, historicalExport) {{
  if (historicalExport.export_sha256 !== receipt.export_sha256) throw new Error('historical export not rebound');
}}
async function writeRuntimeReceipt(receipt) {{ emitted = receipt; }}
{replay_helpers}
{execute}
(async () => {{
  const receipt = await execute();
  if (receipt !== replacement || emitted !== replacement || persisted !== 0) process.exit(2);
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
log="$(mktemp)"
trap 'rm -f "$log"' EXIT
forward_receipt=/receipts/forward.json
forward_runtime_receipt=/receipts/runtime-forward.json
rollback_runtime_receipt=/receipts/runtime-rollback.json
python3() {{ printf '%s\\n' "$*" >"$log"; }}
chmod() {{ :; }}
run_rollback_restore
grep -F 'rollback-runtime' "$log" >/dev/null
grep -F -- '--runtime-state /receipts/state.json' "$log" >/dev/null
grep -F -- '--output /receipts/proof.json' "$log" >/dev/null
grep -F -- '--forward-receipt /receipts/forward.json' "$log" >/dev/null
grep -F -- '--rollback-runtime-receipt /receipts/runtime-rollback.json' "$log" >/dev/null
"""
        result = subprocess.run(
            ["bash", "-c", harness],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_nonempty_target_rows_replay_and_rollback_through_runtime_execute(
        self,
    ) -> None:
        result = subprocess.run(
            ["node", "-e", target_runtime_harness(), "roundtrip"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "roundtrip")

    def test_injected_target_failure_is_atomic_through_runtime_execute(self) -> None:
        result = subprocess.run(
            ["node", "-e", target_runtime_harness(), "failure"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "failure-atomic")

    def test_nonempty_source_projects_into_bound_runtime_bundle(self) -> None:
        cutover = load_runner()
        migration = cutover._load_migration_module()
        source = {
            "finance_source_cursors": [
                {
                    "source_code": "MAIL",
                    "cursor_value": "2026-08-01",
                    "committed_run_id": "run-1",
                    "cursor_version": 2,
                    "readback_verified": True,
                }
            ],
            "finance_acquisition_receipts": [],
            "finance_archive_receipts": [],
            "finance_document_operations": [],
            "finance_actual_outbox": [],
            "finance_actual_verifications": [],
            "finance_reconciliations": [],
            "finance_agent_jobs": [],
        }
        migration_runner = migration.MigrationRunner(source)
        migration_receipt = migration_runner.run()
        bundle = cutover._canonical_runtime_source_bundle(
            SimpleNamespace(workflow_root=ROOT / "integrations" / "n8n" / "workflows"),
            source_head="0" * 40,
            generator_head="1" * 40,
            identity_digest="2" * 64,
            source_backup_sha256="3" * 64,
            migration_receipt_sha256="4" * 64,
            migration_receipt=migration_receipt,
            runner=migration_runner,
            matrix=cutover._load_matrix(),
        )
        targets = {target["name"]: target for target in bundle["targets"]}
        self.assertEqual(
            bundle["schema_version"], "finance-four-table-canonical-source-v2"
        )
        self.assertEqual(targets["finance_ingestion_state"]["row_count"], 1)
        self.assertEqual(
            targets["finance_ingestion_state"]["rows"][0]["source_code"], "MAIL"
        )
        self.assertEqual(
            targets["finance_ingestion_state"]["rows"][0]["cursor_value"],
            "2026-08-01T00:00:00.000Z",
        )

    def test_digest_adapter_paginates_zero_and_many_rows(self) -> None:
        source = DIGEST_ADAPTER.read_text(encoding="utf-8")
        helpers = source[
            source.index("function canonical") : source.index("const originalInit")
        ]
        harness = f"""
const crypto = require('node:crypto');
const projectId = 'project-1';
const TARGET_SCHEMA_DIGESTS = new Map();
const CANONICAL_TABLES = new Set(['finance_documents']);
{helpers}
const schema = [
  {{ name: 'document_id', type: 'string' }},
  {{ name: 'posted_at', type: 'date' }},
];
const table = {{ id: 'documents', name: 'finance_documents' }};
const values = Array.from({{ length: 1501 }}, (_, index) => ({{
  document_id: `doc-${{String(index).padStart(4, '0')}}`,
  posted_at: new Date(Date.UTC(2026, 0, 1, 0, 0, index)),
}}));
const calls = [];
const service = {{
  async getManyRowsAndCount(_id, _project, page) {{
    calls.push(page.skip);
    return {{ count: values.length, data: values.slice(page.skip, page.skip + page.take) }};
  }},
}};
(async () => {{
  const empty = await readCanonicalRows(
    {{ async getManyRowsAndCount() {{ return {{ count: 0, data: [] }}; }} }},
    table,
    schema,
  );
  if (empty.length !== 0) process.exit(2);
  const rows = await readCanonicalRows(service, table, schema);
  if (rows.length !== values.length || JSON.stringify(calls) !== '[0,1000]') process.exit(3);
  if (!rows[0].includes('T00:00:00.000Z') || !rows.at(-1).includes('document_id')) process.exit(4);
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

    def test_typed_date_canonicalization_is_timezone_stable(self) -> None:
        source = CJS_RUNNER.read_text(encoding="utf-8")
        helper = source[
            source.index("function canonicalTargetValue") : source.index(
                "function digest"
            )
        ]
        harness = f"""
{helper}
const values = [
  canonicalTargetValue('2026-08-01', 'date', 'INVALID'),
  canonicalTargetValue('2026-08-01T04:00:00+04:00', 'date', 'INVALID'),
  canonicalTargetValue('2026-08-01T00:00:00', 'date', 'INVALID'),
];
if (values.some((value) => value !== '2026-08-01T00:00:00.000Z')) process.exit(2);
try {{
  canonicalTargetValue('not-a-date', 'date', 'INVALID');
  process.exit(3);
}} catch (error) {{
  if (error.message !== 'INVALID') throw error;
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

    def test_recovery_state_match_rejects_every_drift_axis(self) -> None:
        source = CJS_RUNNER.read_text(encoding="utf-8")
        helper = source[
            source.index("function receiptMatchesCommittedState") : source.index(
                "function selectForwardReplayJournal"
            )
        ]
        harness = f"""
const crypto = require('node:crypto');
const canonical = (value) => value;
const digest = (value) => crypto.createHash('sha256').update(`${{JSON.stringify(value)}}\\n`).digest('hex');
{helper}
const readback = [{{ workflow_id: 'wf' }}];
const state = {{
  credentialStateDigest: 'credentials',
  workflowCredentialObjectsDigest: 'credential-objects',
  workflowRevisionDigest: 'revisions',
  credentialOriginBitset: '0',
  targetReadbackDigest: 'targets',
}};
const canonicalSource = {{
  sha256: 'source',
  targetProjectionSha256: 'projection',
  targetDigest: 'target-digest',
}};
const receipt = {{
  readback_digest_sha256: digest(readback),
  credential_state_digest_after: state.credentialStateDigest,
  workflow_credential_objects_digest_after: state.workflowCredentialObjectsDigest,
  workflow_revision_digest_after: state.workflowRevisionDigest,
  credential_origin_post_bitset: state.credentialOriginBitset,
  canonical_source_sha256: canonicalSource.sha256,
  target_projection_sha256: canonicalSource.targetProjectionSha256,
  target_digest: canonicalSource.targetDigest,
  target_readback_sha256: state.targetReadbackDigest,
}};
if (!receiptMatchesCommittedState(receipt, readback, state, canonicalSource)) process.exit(2);
for (const field of [
  'credentialStateDigest',
  'workflowCredentialObjectsDigest',
  'workflowRevisionDigest',
  'credentialOriginBitset',
  'targetReadbackDigest',
]) {{
  if (receiptMatchesCommittedState(
    receipt,
    readback,
    {{ ...state, [field]: 'drift' }},
    canonicalSource,
  )) process.exit(3);
}}
if (receiptMatchesCommittedState(receipt, [{{ workflow_id: 'drift' }}], state, canonicalSource)) process.exit(4);
if (receiptMatchesCommittedState(receipt, readback, state, {{ ...canonicalSource, targetDigest: 'drift' }})) process.exit(5);
"""
        result = subprocess.run(
            ["node", "-e", harness],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rollback_runtime_resumes_restored_state_without_rewriting_it(
        self,
    ) -> None:
        runner = load_runner()
        digest = "a" * 64
        binding = {
            "operation_nonce": "nonce",
            "protected_quiescence_receipt_digest": "b" * 64,
            "required_live_export_digest": "c" * 64,
            "contract_bijection_digest": "d" * 64,
        }
        actions = [{} for _ in runner.EXPECTED_REFERENCE_ACTIONS]
        forward_receipt = {
            "schema_version": "finance-four-table-runtime-plan-v3",
            "operation": "FORWARD",
            "export_sha256": "e" * 64,
            "canonical_source_sha256": "f" * 64,
            "target_digest": "1" * 64,
            "target_projection_sha256": "2" * 64,
            "rollback_targets_sha256": "3" * 64,
            "actions": actions,
        }
        rollback_receipt = {
            **binding,
            "project_id": "project-1",
            "lock_resource": "finance_four_table_cutover:project-1",
            "export_sha256": "4" * 64,
            "forward_runtime_receipt_sha256": "5" * 64,
            "canonical_source_sha256": forward_receipt["canonical_source_sha256"],
            "target_digest": forward_receipt["target_digest"],
            "target_projection_sha256": forward_receipt["target_projection_sha256"],
            "rollback_targets_sha256": forward_receipt["rollback_targets_sha256"],
            "actions": actions,
        }
        migration_receipt = {
            "old_tables_preserved": True,
            "deletion_authorized": False,
            "target_digest": "1" * 64,
            "source_digest": "6" * 64,
        }
        runtime_state = {
            **binding,
            "schema_version": runner.RUNTIME_STATE_SCHEMA,
            "operation": "ROLLBACK",
            "status": "RESTORED",
            "migration_receipt_sha256": digest,
            "source_head": "0" * 40,
            "generator_head": "1" * 40,
            "accepted_identity_sha256": "7" * 64,
            "source_backup_sha256": digest,
            "source_digest": "6" * 64,
            "old_tables_preserved": True,
            "runtime_cutover": False,
            "deletion_authorized": False,
            "workflow_export_sha256": "4" * 64,
            "lock_receipt_sha256": "8" * 64,
            "rollback_runtime_receipt_sha256": "9" * 64,
            "target_tables_created": True,
            "target_tables_untouched": False,
            "target_rows_restored": True,
            "forward_runtime_receipt_sha256": "5" * 64,
            "restored_source_digest": "6" * 64,
            "restore_roundtrip": True,
            "runtime_state_before_sha256": "a" * 64,
        }
        fake_runner = mock.Mock()
        fake_runner.run.return_value = migration_receipt
        fake_runner.restore_backup.side_effect = AssertionError(
            "restore must not run on resume"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_backup = root / "source.json"
            source_backup.write_bytes(b"source")
            source_backup.chmod(0o600)
            observed_backup_sha = hashlib.sha256(b"source").hexdigest()
            args = SimpleNamespace(
                source_backup=source_backup,
                migration_receipt=root / "migration.json",
                runtime_state=root / "state.json",
                output=root / "proof.json",
                rollback_runtime_receipt=root / "rollback.json",
                forward_runtime_receipt=root / "forward.json",
                accepted_identity=None,
            )
            runtime_state["source_backup_sha256"] = observed_backup_sha
            with (
                mock.patch.object(
                    runner,
                    "_heads",
                    return_value=(
                        "0" * 40,
                        "1" * 40,
                        digest,
                        observed_backup_sha,
                        "7" * 64,
                    ),
                ),
                mock.patch.object(
                    runner,
                    "_bound_live_inputs",
                    return_value=(
                        {"project_id": "project-1", "export_sha256": "4" * 64},
                        {},
                        "8" * 64,
                        binding,
                    ),
                ),
                mock.patch.object(
                    runner,
                    "_source_and_receipt",
                    return_value=(
                        {},
                        migration_receipt,
                        digest,
                        observed_backup_sha,
                    ),
                ),
                mock.patch.object(
                    runner,
                    "_read_forward_cutover_receipt",
                    return_value={"runtime_state_sha256": "a" * 64},
                ),
                mock.patch.object(runner, "_load_migration_module"),
                mock.patch.object(
                    runner, "_migration_runner", return_value=fake_runner
                ),
                mock.patch.object(
                    runner,
                    "_read_forward_runtime_receipt",
                    return_value=(forward_receipt, "5" * 64),
                ),
                mock.patch.object(
                    runner,
                    "_read_rollback_runtime_receipt",
                    return_value=(rollback_receipt, "9" * 64),
                ),
                mock.patch.object(
                    runner,
                    "_read_runtime_state",
                    return_value=(runtime_state, "b" * 64),
                ),
                mock.patch.object(runner, "_assert_currentness"),
                mock.patch.object(runner, "_validate_output_path"),
                mock.patch.object(runner, "_require_protected"),
                mock.patch.object(runner, "_write_runtime_state") as write_state,
                mock.patch.object(runner, "_write_json") as write_json,
            ):
                result = runner.run_rollback_runtime(args)
        fake_runner.restore_backup.assert_not_called()
        write_state.assert_not_called()
        write_json.assert_called_once()
        self.assertEqual(result["runtime_state_sha256"], "b" * 64)
        self.assertEqual(result["rollback_runtime_receipt_sha256"], "9" * 64)

    def test_incompatible_export_fails_rollback_preflight_before_lock_receipt(
        self,
    ) -> None:
        runner = load_runner()
        binding = {"required_live_export_digest": "b" * 64}
        export = {
            "project_id": "project-1",
            "export_sha256": "c" * 64,
            "actions": [
                {
                    "reference_id": reference_id,
                    "workflow_id": f"workflow-{index}",
                    "node_id": f"node-{index}",
                    "canonical_table_id": f"table-{index}",
                    "revision_id": f"revision-{index}",
                }
                for index, reference_id in enumerate(runner.EXPECTED_REFERENCE_ACTIONS)
            ],
        }
        runtime_receipt = {
            **binding,
            "schema_version": "finance-four-table-runtime-plan-v3",
            "project_id": "project-1",
            "export_sha256": "d" * 64,
            "canonical_source_sha256": "e" * 64,
            "lock_resource": "finance_four_table_cutover:project-1",
            "actions": [dict(action) for action in export["actions"]],
        }
        runtime_receipt["actions"][0]["canonical_table_id"] = "incompatible"
        args = SimpleNamespace(
            operation_kind="ROLLBACK",
            live_export=Path("/live-export.json"),
            source_backup=Path("/source.json"),
            migration_receipt=Path("/migration.json"),
            forward_runtime_receipt=Path("/forward-runtime.json"),
            forward_receipt=Path("/forward.json"),
            output=Path("/precondition.json"),
        )
        with (
            mock.patch.object(
                runner,
                "_heads",
                return_value=("0" * 40, "1" * 40, "a" * 64, "b" * 64, "2" * 64),
            ),
            mock.patch.object(
                runner,
                "_source_and_receipt",
                return_value=({}, {"source_digest": "f" * 64}, "a" * 64, "b" * 64),
            ),
            mock.patch.object(
                runner,
                "_read_forward_runtime_receipt",
                return_value=(runtime_receipt, "3" * 64),
            ),
            mock.patch.object(runner, "_binding_inputs", return_value=binding),
            mock.patch.object(runner, "_validate_live_export", return_value=export),
            mock.patch.object(runner, "_lock_receipt") as lock_receipt,
            self.assertRaisesRegex(
                runner.CutoverError, "FORWARD_RUNTIME_ACTION_MISMATCH"
            ),
        ):
            runner.validate_preconditions(args)
        lock_receipt.assert_not_called()

    def test_cutover_readback_and_rollback_receipt_binding_match_schema(
        self,
    ) -> None:
        runner = load_runner()
        raw = json.loads(READBACK_FIXTURE.read_text(encoding="utf-8"))["raw_stdout"]
        raw = raw.replace(
            '"bound":false,"sha256":null',
            '"bound":true,"sha256":"' + "a" * 64 + '"',
            1,
        )
        raw = raw.replace('"scope":', '"phase":"FORWARD_PRE","scope":', 1)
        raw = raw.replace('"status":"VERIFIED"', '"status":"FORWARD_PRE_READBACK"', 1)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "pre.raw"
            path.write_text(raw, encoding="utf-8")
            readback = runner._parse_readback(path, "a" * 64, "FORWARD_PRE")
        schema = json.loads(
            (
                ROOT
                / "integrations"
                / "n8n"
                / "schemas"
                / "finance-four-table-cutover-receipt-v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        readback_schema = {"$ref": "#/$defs/readback", "$defs": schema["$defs"]}
        Draft202012Validator(readback_schema).validate(readback)
        self.assertTrue(
            {"rollback_runtime_receipt_sha256", "forward_runtime_receipt_sha256"}
            <= set(schema["oneOf"][1]["required"])
        )

    def test_runtime_rejects_extra_project_workflows_and_tables(self) -> None:
        source = CJS_RUNNER.read_text(encoding="utf-8")
        load_workflows = source[
            source.index("async function loadWorkflows") : source.index(
                "function canonicalSourceFromInput"
            )
        ]
        load_targets = source[
            source.index("async function loadTargetState") : source.index(
                "async function insertTargetRows"
            )
        ]
        harness = f"""
const projectId = 'project-1';
const TARGET_NAMES = new Set(['target']);
const COMPATIBILITY_TABLE_NAMES = new Set(['target', 'preserved']);
function assertWorkflow() {{}}
{load_workflows}
{load_targets}
const workflowGraph = {{
  workflows: new Map([['expected', 'revision']]),
  workflowBodyDigests: new Map([['expected', 'digest']]),
}};
const workflowClient = {{
  async query() {{
    return {{ rows: [
      {{ id: 'expected', nodes: [], active: false, activeVersionId: null }},
      {{ id: 'extra', nodes: [], active: false, activeVersionId: null }},
    ] }};
  }},
}};
const tableClient = {{
  async query() {{
    return {{ rows: [
      {{ id: 'target-id', name: 'target' }},
      {{ id: 'extra-id', name: 'unexpected' }},
    ] }};
  }},
}};
(async () => {{
  try {{
    await loadWorkflows(workflowClient, workflowGraph, false);
    process.exit(2);
  }} catch (error) {{
    if (error.message !== 'EXACT_PROJECT_WORKFLOW_SET_REQUIRED') throw error;
  }}
  try {{
    await loadTargetState(tableClient, new Map([['target', {{ tableId: 'target-id' }}]]));
    process.exit(3);
  }} catch (error) {{
    if (error.message !== 'CLOSED_PROJECT_DATA_TABLE_SET_REQUIRED') throw error;
  }}
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


if __name__ == "__main__":
    unittest.main()
