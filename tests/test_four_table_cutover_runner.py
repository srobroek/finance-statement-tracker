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

ROOT = Path(__file__).resolve().parents[1]
RUNNER_DIR = ROOT / "integrations" / "n8n" / "setup-workflows" / "runner"
PYTHON_RUNNER = RUNNER_DIR / "four_table_cutover.py"
CJS_RUNNER = RUNNER_DIR / "n8n-cli-four-table-cutover.cjs"
SHELL_RUNNER = RUNNER_DIR / "run-four-table-cutover.sh"
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
            "FORWARD_JOURNAL_RECOVERY_ONLY",
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
        self.assertIn("recover_forward_runtime_receipt", shell_source)

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


if __name__ == "__main__":
    unittest.main()
