from __future__ import annotations

from copy import deepcopy
import base64
import json
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import re
import unittest
from unittest import mock
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, ValidationError


ROOT = Path(__file__).resolve().parents[1]
N8N = ROOT / "integrations" / "n8n"
WORKFLOWS = N8N / "workflows"
ERROR_WORKFLOW_ID = "10000000-0000-4000-8000-000000000016"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def git_canonical_sha256(path: Path) -> str:
    """Hash the LF bytes that a normal Git checkout exposes on Linux."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_bootstrap_generator():
    generator_path = N8N / "generate_platform_bootstrap.py"
    spec = importlib.util.spec_from_file_location("finance_bootstrap_generator", generator_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load generator: {generator_path}")
    sys.path.insert(0, str(N8N))
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def validate_fixture_against_schema(schema: dict, value: object, path: str = "$") -> None:
    """Execute the JSON-schema subset used by the browser capture contract."""
    expected_type = schema.get("type")
    if expected_type:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        matches = any(
            kind == "object" and isinstance(value, dict)
            or kind == "array" and isinstance(value, list)
            or kind == "string" and isinstance(value, str)
            or kind == "boolean" and isinstance(value, bool)
            or kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool)
            for kind in types
        )
        if not matches:
            raise AssertionError(f"{path}: expected {expected_type}")
    if "const" in schema and value != schema["const"]:
        raise AssertionError(f"{path}: const mismatch")
    if "enum" in schema and value not in schema["enum"]:
        raise AssertionError(f"{path}: enum mismatch")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise AssertionError(f"{path}: minLength")
        if "pattern" in schema:
            if not re.search(schema["pattern"], value):
                raise AssertionError(f"{path}: pattern")
        if schema.get("format") == "date":
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError as error:
                raise AssertionError(f"{path}: date") from error
        elif schema.get("format") == "date-time":
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as error:
                raise AssertionError(f"{path}: date-time") from error
        elif schema.get("format") == "uri":
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise AssertionError(f"{path}: uri")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise AssertionError(f"{path}: missing {key}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            if unknown:
                raise AssertionError(f"{path}: forbidden {sorted(unknown)}")
        for key, child in value.items():
            if key in properties:
                validate_fixture_against_schema(properties[key], child, f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        for index, child in enumerate(value):
            validate_fixture_against_schema(schema["items"], child, f"{path}[{index}]")


class N8nWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_json(N8N / "pipeline-registry.json")
        cls.workflows = {
            path.name: load_json(path) for path in sorted(WORKFLOWS.glob("*.json"))
        }
        cls.tables = load_json(N8N / "data-tables.json")
        cls.fixtures = load_json(N8N / "resilience-fixtures.json")

    def workflow(self, filename: str) -> dict:
        return self.workflows[filename]

    def nodes(self, filename: str) -> dict[str, dict]:
        return {node["name"]: node for node in self.workflow(filename)["nodes"]}

    def run_exported_node(
        self,
        node_name: str,
        json_input: dict,
        binary: dict,
        references: dict[str, dict],
    ) -> dict:
        code = self.nodes("11-interactive-artifact-handoff.json")[node_name]["parameters"]["jsCode"]
        script = f"""
const code = {json.dumps(code)};
const jsonInput = {json.dumps(json_input)};
const binary = {json.dumps(binary)};
const references = {json.dumps(references)};
const lookup = name => ({{ first: () => references[name] }});
try {{
  const output = new Function('$json', '$binary', '$', 'require', code)(jsonInput, binary, lookup, require);
  process.stdout.write(JSON.stringify({{ ok: true, output }}));
}} catch (error) {{
  process.stdout.write(JSON.stringify({{ ok: false, error: String(error.message || error) }}));
}}
"""
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for exported W11 contract execution")
        environment = os.environ.copy()
        ajv_modules = Path("/home/sjors/.cache/typescript/5.9/node_modules")
        if ajv_modules.is_dir():
            environment["NODE_PATH"] = str(ajv_modules)
        result = subprocess.run(
            [node, "-e", script],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout, result.stderr)
        return json.loads(result.stdout)

    def run_exported_workflow_node(
        self,
        workflow_filename: str,
        node_name: str,
        json_input: dict,
        references: dict[str, dict],
    ) -> dict:
        code = self.nodes(workflow_filename)[node_name]["parameters"]["jsCode"]
        script = f"""
const code = {json.dumps(code)};
const jsonInput = {json.dumps(json_input)};
const references = {json.dumps(references)};
const lookup = name => ({{ first: () => references[name] }});
try {{
  const output = new Function('$json', '$', 'require', code)(jsonInput, lookup, require);
  process.stdout.write(JSON.stringify({{ ok: true, output }}));
}} catch (error) {{
  process.stdout.write(JSON.stringify({{ ok: false, error: String(error.message || error) }}));
}}
"""
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for exported workflow contract execution")
        result = subprocess.run(
            [node, "-e", script],
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout, result.stderr)
        return json.loads(result.stdout)

    def test_registry_maps_every_existing_codex_automation(self) -> None:
        automations = load_json(ROOT / "config" / "codex-automations.json")
        mapped = {
            row["replaces_codex_automation"]
            for row in self.registry["workflows"]
            if row.get("replaces_codex_automation")
        }
        self.assertEqual(mapped, {row["id"] for row in automations["automations"]})

    def test_registry_is_postgres_spec_only_without_execution_claims(self) -> None:
        self.assertEqual(self.registry["schema_version"], 2)
        self.assertEqual(self.registry["n8n_version"], "2.36.2")
        self.assertEqual(
            self.registry["deployment_mode"], "regular-postgres-external-runners"
        )
        self.assertEqual(self.registry["contract_status"], "SPEC_ONLY")
        self.assertEqual(set(self.registry["execution_evidence"].values()), {False})
        self.assertTrue(
            all(row["status"] == "SPEC_ONLY" for row in self.registry["workflows"])
        )

    def test_registry_and_workflow_exports_are_bijective(self) -> None:
        expected = {row["file"] for row in self.registry["workflows"]}
        self.assertEqual(expected, set(self.workflows))
        codes = {row["code"] for row in self.registry["workflows"]}
        exported = {
            workflow["meta"]["financeWorkflowCode"]
            for workflow in self.workflows.values()
        }
        self.assertEqual(codes, exported)

    def test_mcp_is_disabled_instance_wide_and_facade_is_bounded(self) -> None:
        mcp = self.registry["mcp"]
        self.assertFalse(mcp["instance_mcp_enabled"])
        self.assertEqual(mcp["facade_workflow_code"], "FINANCE_MCP_FACADE")
        self.assertEqual(
            set(mcp["allowed_operation_codes"]),
            {
                "finance.status.v1",
                "finance.reviewed-artifact.handoff.v1",
                "finance.document.request.v1",
            },
        )
        exposed = [row for row in self.registry["workflows"] if row["mcp_exposed"]]
        self.assertEqual([row["code"] for row in exposed], ["FINANCE_MCP_FACADE"])
        facade = self.workflow("15-finance-mcp-facade.json")
        self.assertEqual(
            {node["type"] for node in facade["nodes"]},
            {
                "@n8n/n8n-nodes-langchain.mcpTrigger",
                "@n8n/n8n-nodes-langchain.toolWorkflow",
                "n8n-nodes-base.stickyNote",
            },
        )
        self.assertEqual(
            set(facade["meta"]["allowedOperationCodes"]),
            set(mcp["allowed_operation_codes"]),
        )
        raw = json.dumps(facade).casefold()
        for field in mcp["caller_forbidden_fields"]:
            self.assertNotIn(f"$fromai('{field}'", raw)

    def test_adcb_has_no_recurring_n8n_pipeline(self) -> None:
        scheduled_sources = {
            row.get("source") for row in self.registry["workflows"] if row["schedule"]
        }
        self.assertNotIn("ADCB_CASHBACK", scheduled_sources)
        self.assertIn(
            "ADCB_CASHBACK",
            {row["source"] for row in self.registry["retired_or_not_migrated"]},
        )

    def test_workflow_exports_are_inactive_sanitized_and_fail_closed(self) -> None:
        forbidden_types = {"n8n-nodes-base.executeCommand", "n8n-nodes-base.ssh"}
        forbidden_markers = (
            "ADCB_STATEMENT_PASSWORD_PLACEHOLDER", "sjor2908", "actual_password", "cashback_ingest_token",
            "172.20.10.20", "notion", "$env", "gpt-5-mini", "lmchatopenai",
            "financetransform", "unlockifprotected",
        )
        for filename, workflow in self.workflows.items():
            with self.subTest(workflow=filename):
                self.assertFalse(workflow["active"])
                self.assertTrue(workflow["id"] and workflow["name"])
                self.assertTrue(workflow["nodes"])
                self.assertEqual(workflow["meta"]["migrationStatus"], "SPEC_ONLY")
                settings = workflow["settings"]
                self.assertEqual(settings["timezone"], "Asia/Dubai")
                self.assertEqual(settings["saveDataSuccessExecution"], "none")
                self.assertEqual(settings["saveDataErrorExecution"], "none")
                if filename == "16-operations-error-handler.json":
                    self.assertNotIn("errorWorkflow", settings)
                else:
                    self.assertEqual(settings["errorWorkflow"], ERROR_WORKFLOW_ID)
                types = {node["type"] for node in workflow["nodes"]}
                self.assertFalse(types & forbidden_types)
                raw = json.dumps(workflow).casefold()
                for marker in forbidden_markers:
                    self.assertNotIn(marker, raw)

    def test_workflow_nodes_are_postgres_jsonb_compatible(self) -> None:
        for filename, workflow in self.workflows.items():
            with self.subTest(workflow=filename):
                serialized_nodes = json.dumps(workflow["nodes"], ensure_ascii=True)
                self.assertNotIn("\\u0000", serialized_nodes)
                self.assertNotIn("\x00", serialized_nodes)

        shared_code = self.nodes("03-shared-statement-pipeline.json")[
            "Merge Allowed AI Proposals"
        ]["parameters"]["jsCode"]
        proposal_code = self.nodes("09-ai-proposal.json")[
            "Validate Proposal Schema and Policy Boundary"
        ]["parameters"]["jsCode"]
        compact_shared = re.sub(r"\s+", "", shared_code)
        self.assertIn("constpair=JSON.stringify([", compact_shared)
        self.assertIn(
            "String(proposal.transaction_id),String(proposal.field)",
            compact_shared,
        )
        self.assertIn(
            "JSON.stringify([proposal.transaction_id, proposal.field])",
            proposal_code,
        )

    def test_custom_node_contract_is_narrow_and_frozen(self) -> None:
        contract = self.registry["custom_nodes"]
        self.assertEqual((contract["package"], contract["version"]), ("n8n-nodes-finance", "0.1.0"))
        expected = contract["node_types"]
        seen: set[tuple[str, str]] = set()
        for workflow in self.workflows.values():
            for node in workflow["nodes"]:
                if not node["type"].startswith("n8n-nodes-finance."):
                    continue
                short = node["type"].rsplit(".", 1)[1]
                params = node.get("parameters", {})
                self.assertIn(short, expected)
                self.assertIn(params["operation"], expected[short])
                allowed = {"operation", "readShape"} if short == "actualBudget" else {"operation"}
                self.assertLessEqual(set(params), allowed)
                if "readShape" in params:
                    self.assertEqual(params["operation"], "read")
                    self.assertIn(params["readShape"], {"accounts", "categories", "transactionsByImportedIds"})
                if short == "actualBudget":
                    self.assertIn("actualBudgetApi", node.get("credentials", {}))
                if short == "financePdf" and params["operation"] == "unlock":
                    self.assertIn("financeStatementPassword", node.get("credentials", {}))
                seen.add((short, params["operation"]))
        for short, operations in expected.items():
            for operation in operations:
                if short == "actualBudget" and operation in {"doctor", "read"}:
                    continue
                self.assertIn((short, operation), seen)

    def test_pdf_text_extraction_stays_inside_fixed_finance_node(self) -> None:
        nodes = self.workflow("14-local-pdf-extraction.json")["nodes"]
        pdf_ops = [
            node["parameters"]["operation"]
            for node in nodes
            if node["type"] == "n8n-nodes-finance.financePdf"
        ]
        self.assertEqual(pdf_ops[:3], ["validate", "unlock", "profile"])
        shared = self.nodes("03-shared-statement-pipeline.json")
        self.assertEqual(
            shared["Run Isolated PDF Extraction"]["parameters"]["workflowId"]["value"],
            self.workflow("14-local-pdf-extraction.json")["id"],
        )
        self.assertFalse(any(
            node["type"] == "n8n-nodes-base.extractFromFile"
            and node.get("parameters", {}).get("operation") == "pdf"
            for workflow in self.workflows.values()
            for node in workflow["nodes"]
        ))

    def test_data_table_contract_is_postgres_and_schema_valid(self) -> None:
        self.assertEqual(self.tables["storage"], "n8n-data-tables-on-postgres")
        self.assertEqual(self.tables["contract_status"], "SPEC_ONLY")
        schema = load_json(N8N / "data-tables.schema.json")
        try:
            import jsonschema
        except ImportError:
            jsonschema = None
        if jsonschema:
            jsonschema.validators.validator_for(schema).check_schema(schema)
            jsonschema.validate(self.tables, schema)
        names = {row["name"] for row in self.tables["tables"]}
        self.assertTrue({
            "finance_source_contracts", "finance_source_cursors",
            "finance_acquisition_receipts", "finance_archive_receipts",
            "finance_document_operations", "finance_pipeline_runs",
            "finance_actual_outbox", "finance_actual_verifications",
            "finance_reconciliations", "finance_config_versions",
            "finance_provider_circuits", "finance_execution_failures",
            "finance_mcp_requests", "finance_agent_jobs", "finance_ai_policy_contracts",
        }.issubset(names))
        self.assertEqual(set(self.tables["state_policies"]), names)
        for name, policy in self.tables["state_policies"].items():
            with self.subTest(table=name):
                self.assertTrue(policy["retention"])
                self.assertTrue(policy["idempotency"])
                self.assertTrue(policy["concurrency"])
                self.assertTrue(policy["index_semantics"])

    def test_every_declared_data_table_is_referenced_by_a_connected_executable_node(self) -> None:
        referenced: set[str] = set()
        for workflow in self.workflows.values():
            connected = set(workflow["connections"])
            for outputs in workflow["connections"].values():
                for channel in outputs.values():
                    for branch in channel:
                        connected.update(edge["node"] for edge in branch)
            for node in workflow["nodes"]:
                if node["name"] not in connected or node["type"] != "n8n-nodes-base.dataTable":
                    continue
                value = node.get("parameters", {}).get("dataTableId", {}).get("value")
                if value:
                    referenced.add(value)
        declared = {row["name"] for row in self.tables["tables"]}
        self.assertEqual(referenced, declared)

    def test_outbox_holds_only_pointer_hash_and_state_metadata(self) -> None:
        table = next(row for row in self.tables["tables"] if row["name"] == "finance_actual_outbox")
        columns = set(table["columns"])
        self.assertTrue({"artifact_item_id", "artifact_etag", "payload_sha256", "config_version", "parser_version", "state"}.issubset(columns))
        self.assertFalse({"transactions", "transaction", "payload_json", "statement_rows"} & columns)
        self.assertEqual(table["allowed_states"], ["PREPARED", "ACTUAL_OBSERVED", "VERIFIED", "COMMITTED", "FAILED"])

    def test_document_state_machine_marks_plaintext_ephemeral(self) -> None:
        table = next(row for row in self.tables["tables"] if row["name"] == "finance_document_operations")
        self.assertTrue({
            "RECEIVED", "VALIDATED", "DECRYPTED_EPHEMERAL", "EXTRACTED_EPHEMERAL",
            "SCHEMA_VALIDATED", "READY_FOR_PARSE", "COMMITTED", "QUARANTINED",
            "UNSUPPORTED", "PASSWORD_FAILED",
        }.issubset(set(table["allowed_states"])))
        self.assertEqual(table["idempotency_key"], ["source_sha256", "document_profile", "requested_schema_version"])

    def test_outlook_sweep_freezes_window_exhausts_and_returns_one_heartbeat(self) -> None:
        workflow = self.workflow("12-outlook-message-sweep.json")
        nodes = self.nodes("12-outlook-message-sweep.json")
        outlook = nodes["Exhaust Outlook Pagination"]
        self.assertTrue(outlook["parameters"]["returnAll"])
        self.assertTrue(outlook["alwaysOutputData"])
        code = nodes["Freeze Trusted Cursor Window"]["parameters"]["jsCode"] + nodes["Aggregate Exact Window Heartbeat"]["parameters"]["jsCode"]
        compact = re.sub(r"\s+", "", code)
        for term in ("run_upper_bound", "pagination_exhausted:true", "scanned_count", "heartbeat", "received>=start", "received<upper"):
            self.assertIn(term, compact)
        self.assertTrue(workflow["meta"]["aggregateOutputAlwaysOne"])
        self.assertTrue(workflow["meta"]["cursorCommitExactlyOnce"])
        self.assertTrue(workflow["meta"]["cursorAdvanceRequiresDownstreamReceipt"])
        self.assertIn("finance_acquisition_receipts", json.dumps(nodes["Upsert ENUMERATED Receipt"]))
        self.assertEqual(nodes["CAS Update Source Cursor"]["parameters"]["operation"], "update")
        cas_filters = nodes["CAS Update Source Cursor"]["parameters"]["filters"]["conditions"]
        self.assertEqual([row["keyName"] for row in cas_filters], ["source_code", "cursor_version"])
        self.assertIn("SOURCE_CURSOR_VERSION_CONFLICT", nodes["Build Cursor CAS Update"]["parameters"]["jsCode"])

    def test_fixture_matrix_covers_zero_101_late_duplicates_and_failures(self) -> None:
        self.assertEqual(self.fixtures["contract_status"], "SPEC_ONLY")
        cases = {row["id"]: row for row in self.fixtures["mail_sweep_cases"]}
        required = {
            "zero-messages", "one-hundred-one-messages", "pagination-failure",
            "late-out-of-order", "duplicate-message-attachment-hash",
            "failure-before-cursor", "failure-after-cursor",
        }
        self.assertTrue(required.issubset(cases))
        self.assertEqual(cases["zero-messages"]["expected"]["output_items"], 1)
        self.assertEqual(cases["one-hundred-one-messages"]["expected"]["pages_fetched"], 2)
        self.assertEqual(cases["pagination-failure"]["expected"]["cursor_commits"], 0)
        self.assertEqual(cases["failure-before-cursor"]["expected"]["cursor_commits"], 0)

    def test_monthly_workflows_poll_daily_until_deadline(self) -> None:
        rows = {row["code"]: row for row in self.registry["workflows"] if row["code"] in {
            "EI_MONTHLY_STATEMENT", "WIO_MONTHLY_STATEMENT", "RAK_MONTHLY_STATEMENT", "SC_MONTHLY_STATEMENT",
        }}
        self.assertEqual(len(rows), 4)
        for row in rows.values():
            self.assertTrue(row["schedule"].startswith("FREQ=DAILY;"))
            self.assertGreater(row["cycle_poll"]["cycle_day"], 0)
            self.assertGreater(row["cycle_poll"]["deadline_days"], 0)
        shared_names = set(self.nodes("22-shared-monthly-statement-cycle.json"))
        for name in ("Upsert Waiting or Deadline Receipt", "Read Back Waiting or Deadline Receipt"):
            self.assertIn(name, shared_names)
        for filename in ("04-ei-monthly-statement.json", "05-wio-monthly-statement.json"):
            names = set(self.nodes(filename))
            self.assertEqual(
                {name for name in names if not name.startswith("Stage ")},
                {"Daily 20:40 Cycle Poll", "Open Configured Cycle Window", "Run Shared Monthly Statement Cycle"},
            )
            self.assertIn("Run Shared Monthly Statement Cycle", names)
        for name in (
            "Monthly Cycle Context",
            "Load Trusted Source Contract",
            "Acquire Archive and Read Back",
            "Initialize Source Cursor via W12",
            "Run Shared Statement Pipeline",
            "Commit Source Cursor via W12",
        ):
            self.assertIn(name, shared_names)

    def test_shared_pipeline_archives_delta_before_prepared_and_reads_every_state(self) -> None:
        names = [node["name"] for node in self.workflow("03-shared-statement-pipeline.json")["nodes"]]
        self.assertLess(names.index("Verify Durable Canonical Delta"), names.index("Upsert PREPARED Actual Outbox"))
        self.assertIn("Apply Prepared Outbox Safely", names)
        writer = set(self.nodes("20-actual-outbox-apply.json"))
        for name in (
            "Download Immutable Delta Artifact", "SHA-256 Recovered Delta",
            "Verify Recovery Contract", "Acquire Recovery Writer Fence",
            "Assert Recovery Fence Before Import", "Release Recovery Writer Fence",
            "Upsert Exact Actual Verification Receipt",
            "Read Back Exact Actual Verification Receipt",
            "Compare Exact Actual Verification Receipt",
            "Read Back Released Recovery Writer Fence",
            "Route Recovery State", "Read Back COMMITTED Recovery Replay",
            "Read Back Exact Actual Verification Receipt Replay",
            "Read Back Released Recovery Writer Fence Replay",
            "Return Verified Commit Receipt Replay",
        ):
            self.assertIn(name, writer)
        code = self.nodes("20-actual-outbox-apply.json")["Verify Recovery Contract"]["parameters"]["jsCode"]
        self.assertIn("expected_transactions", code)
        self.assertIn("expected_account_balance", code)
        self.assertIn("card_code", code)
        self.assertNotIn("imported_ids:", code)
        writer_nodes = self.nodes("20-actual-outbox-apply.json")
        self.assertIn(
            "card_code",
            writer_nodes["Upsert Exact Actual Verification Receipt"]["parameters"]["columns"]["value"],
        )
        self.assertNotIn(
            "receipt.card_code || receipt.account_id",
            writer_nodes["Return Verified Commit Receipt"]["parameters"]["jsCode"],
        )
        self.assertEqual(
            self.workflow("20-actual-outbox-apply.json")["connections"]["Route Recovery State"]["main"][3][0]["node"],
            "Read Back COMMITTED Recovery Replay",
        )
        self.assertIn(
            "replay_readback_only",
            writer_nodes["Return Verified Commit Receipt Replay"]["parameters"]["jsCode"],
        )
        self.assertIn(
            "ACTUAL_WRITER_LEASE_RELEASE_NOT_READ_BACK",
            writer_nodes["Return Verified Commit Receipt"]["parameters"]["jsCode"],
        )
        self.assertEqual(
            self.workflow("20-actual-outbox-apply.json")["connections"]["Release Recovery Writer Fence"]["main"][0][0]["node"],
            "Read Back Released Recovery Writer Fence",
        )
        self.assertEqual(
            self.workflow("20-actual-outbox-apply.json")["connections"]["Read Back Exact Actual Verification Receipt Replay"]["main"][0][0]["node"],
            "Read Back Released Recovery Writer Fence Replay",
        )

    def test_shared_pipeline_reuses_existing_outbox_before_prepared_upsert(self) -> None:
        workflow = self.workflow("03-shared-statement-pipeline.json")
        nodes = self.nodes("03-shared-statement-pipeline.json")
        connections = workflow["connections"]
        prepare_code = nodes["Prepare Outbox Intent"]["parameters"]["jsCode"]
        self.assertIn("outbox_id: `statement:${source.document_sha256}`", prepare_code)
        self.assertNotIn("outbox_id: `${source.run_id}:${source.document_sha256}`", prepare_code)
        self.assertTrue(nodes["Read Back Existing Actual Outbox"]["alwaysOutputData"])
        self.assertEqual(
            connections["Prepare Outbox Intent"]["main"][0][0]["node"],
            "Read Back Existing Actual Outbox",
        )
        self.assertEqual(
            connections["Select Existing Outbox Or Prepare"]["main"][0][0]["node"],
            "Route Existing Outbox State",
        )
        self.assertEqual(
            connections["Route Existing Outbox State"]["main"][0][0]["node"],
            "Apply Prepared Outbox Safely",
        )
        self.assertEqual(
            connections["Route Existing Outbox State"]["main"][1][0]["node"],
            "Upsert PREPARED Actual Outbox",
        )
        digest = "a" * 64
        draft = {
            "outbox_id": "retry-run:new-outbox",
            "imported_id": "statement:payload",
            "actual_file_id": "actual-file:replay",
            "account_id": "actual-account:EI_AMAZON",
            "card_code": "EI_AMAZON",
            "payload_sha256": digest,
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
            "state": "PREPARED",
        }
        existing = {
            **draft,
            "outbox_id": "statement:stable-existing",
            "state": "COMMITTED",
            "lease_owner": "n8n:recovery:statement:stable-existing",
            "lease_fence": 9,
        }
        selected = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Select Existing Outbox Or Prepare",
            existing,
            {"Prepare Outbox Intent": {"json": draft}},
        )
        self.assertTrue(selected["ok"], selected)
        selected_row = selected["output"][0]["json"]
        self.assertTrue(selected_row["existing_outbox_replay"])
        self.assertEqual(selected_row["state"], "COMMITTED")
        self.assertEqual(selected_row["outbox_id"], "statement:stable-existing")
        self.assertEqual(selected_row["lease_fence"], 9)
        fresh = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Select Existing Outbox Or Prepare",
            {},
            {"Prepare Outbox Intent": {"json": draft}},
        )
        self.assertTrue(fresh["ok"], fresh)
        self.assertFalse(fresh["output"][0]["json"]["existing_outbox_replay"])
        stale = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Select Existing Outbox Or Prepare",
            {**existing, "payload_sha256": "b" * 64},
            {"Prepare Outbox Intent": {"json": draft}},
        )
        self.assertFalse(stale["ok"])

        receipt = {
            "outbox_id": "statement:stable-existing",
            "actual_file_id": "actual-file:replay",
            "account_id": "actual-account:EI_AMAZON",
            "card_code": "EI_AMAZON",
            "verification_version": 1,
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
            "expected_payload_sha256": digest,
            "observed_payload_sha256": digest,
            "invariants_passed": True,
        }
        replay = self.run_exported_workflow_node(
            "20-actual-outbox-apply.json",
            "Return Verified Commit Receipt Replay",
            receipt,
            {
                "Verify Recovery Contract": {"json": {
                    "outbox_row": selected_row,
                    "manifest": {
                        "actual_file_id": "actual-file:replay",
                        "account_id": "actual-account:EI_AMAZON",
                        "card_code": "EI_AMAZON",
                        "period_start": "2026-08-01",
                        "period_end": "2026-08-31",
                    },
                }},
                "Read Back COMMITTED Recovery Replay": {"json": selected_row},
                "Read Back Exact Actual Verification Receipt Replay": {"json": receipt},
                "Read Back Released Recovery Writer Fence Replay": {"json": {
                    "resource_key": "actual:actual-file:replay",
                    "lease_owner": "n8n:recovery:statement:stable-existing",
                    "fencing_token": 9,
                    "released": True,
                }},
            },
        )
        self.assertTrue(replay["ok"], replay)
        self.assertTrue(replay["output"][0]["json"]["replay_readback_only"])

    def test_recovery_rehydrates_artifact_and_preserves_all_outbox_transitions(self) -> None:
        recovery = set(self.nodes("17-actual-outbox-recovery.json"))
        self.assertIn("Apply Nonterminal Outbox Safely", recovery)
        names = set(self.nodes("20-actual-outbox-apply.json"))
        for name in (
            "Download Immutable Delta Artifact", "SHA-256 Recovered Delta",
            "Verify Recovery Contract", "Acquire Recovery Writer Fence",
            "Assert Recovery Fence Before Import", "Upsert ACTUAL OBSERVED Recovery",
            "Read Back ACTUAL OBSERVED Recovery", "Build Recovery Verification Contract",
            "Upsert VERIFIED Recovery", "Read Back VERIFIED Recovery",
            "Upsert COMMITTED Recovery", "Read Back COMMITTED Recovery",
            "Release Recovery Writer Fence",
        ):
            self.assertIn(name, names)
        cases = {row["id"] for row in self.fixtures["writer_lease_cases"]}
        self.assertTrue({
            "concurrent-acquire", "expired-reacquire", "stale-token-before-import",
            "kill-after-prepared", "kill-after-actual-observed", "kill-after-verified",
        }.issubset(cases))

    def test_committed_actual_replay_is_readback_only_and_rejects_stale_receipts(self) -> None:
        digest = "a" * 64
        committed = {
            "outbox_id": "outbox:replay-1",
            "actual_file_id": "actual-file:replay-1",
            "state": "COMMITTED",
            "lease_owner": "n8n:recovery:outbox:replay-1",
            "lease_fence": 7,
        }
        receipt = {
            "outbox_id": "outbox:replay-1",
            "actual_file_id": "actual-file:replay-1",
            "account_id": "actual-account:EI_AMAZON",
            "card_code": "EI_AMAZON",
            "verification_version": 1,
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
            "expected_payload_sha256": digest,
            "observed_payload_sha256": digest,
            "expected_count": 1,
            "observed_count": 1,
            "expected_amount_sum_minor": 100,
            "observed_amount_sum_minor": 100,
            "expected_account_balance": -100,
            "observed_account_balance": -100,
            "invariants_passed": True,
            "verified_at": "2026-08-20T00:00:00+00:00",
        }
        references = {
            "Verify Recovery Contract": {
                "json": {
                    "outbox_row": committed,
                    "manifest": {
                        "actual_file_id": "actual-file:replay-1",
                        "account_id": "actual-account:EI_AMAZON",
                        "card_code": "EI_AMAZON",
                        "period_start": "2026-08-01",
                        "period_end": "2026-08-31",
                    },
                }
            },
            "Read Back COMMITTED Recovery Replay": {"json": committed},
            "Read Back Exact Actual Verification Receipt Replay": {"json": receipt},
            "Read Back Released Recovery Writer Fence Replay": {"json": {
                "resource_key": "actual:actual-file:replay-1",
                "lease_owner": "n8n:recovery:outbox:replay-1",
                "fencing_token": 7,
                "released": True,
            }},
        }
        replay = self.run_exported_workflow_node(
            "20-actual-outbox-apply.json",
            "Return Verified Commit Receipt Replay",
            receipt,
            references,
        )
        self.assertTrue(replay["ok"], replay)
        self.assertTrue(replay["output"][0]["json"]["replay_readback_only"])
        self.assertEqual(replay["output"][0]["json"]["state"], "COMMITTED")
        self.assertTrue(replay["output"][0]["json"]["writer_release_verified"])
        normal = self.run_exported_workflow_node(
            "20-actual-outbox-apply.json",
            "Return Verified Commit Receipt",
            receipt,
            {
                "Read Back COMMITTED Recovery": {"json": committed},
                "Recovery Verify Actual": {"json": {"actual": {"status": "VERIFIED"}}},
                "Compare Exact Actual Verification Receipt": {"json": receipt},
                "Build Recovery Fence Release": {"json": {
                    "resource_key": "actual:actual-file:replay",
                    "lease_id": "00000000-0000-4000-8000-000000000007",
                    "lease_owner": "n8n:recovery:outbox:replay-1",
                    "fencing_token": 7,
                }},
                "Read Back Released Recovery Writer Fence": {"json": {
                    "resource_key": "actual:actual-file:replay",
                    "lease_id": "00000000-0000-4000-8000-000000000007",
                    "lease_owner": "n8n:recovery:outbox:replay-1",
                    "fencing_token": 7,
                    "released": True,
                }},
            },
        )
        self.assertTrue(normal["ok"], normal)
        self.assertTrue(normal["output"][0]["json"]["writer_release_verified"])
        unreleased_normal = self.run_exported_workflow_node(
            "20-actual-outbox-apply.json",
            "Return Verified Commit Receipt",
            receipt,
            {
                "Read Back COMMITTED Recovery": {"json": committed},
                "Recovery Verify Actual": {"json": {"actual": {"status": "VERIFIED"}}},
                "Compare Exact Actual Verification Receipt": {"json": receipt},
                "Build Recovery Fence Release": {"json": {
                    "resource_key": "actual:actual-file:replay",
                    "lease_id": "00000000-0000-4000-8000-000000000007",
                    "lease_owner": "n8n:recovery:outbox:replay-1",
                    "fencing_token": 7,
                }},
                "Read Back Released Recovery Writer Fence": {"json": {"released": False}},
            },
        )
        self.assertFalse(unreleased_normal["ok"])
        invalid_cases = (
            ({**committed, "state": "ACTUAL_OBSERVED"}, receipt),
            (committed, {**receipt, "card_code": "RAK_WORLD"}),
            (committed, {**receipt, "observed_payload_sha256": "b" * 64}),
        )
        for invalid_committed, invalid_receipt in invalid_cases:
            rejected = self.run_exported_workflow_node(
                "20-actual-outbox-apply.json",
                "Return Verified Commit Receipt Replay",
                invalid_receipt,
                {
                    **references,
                    "Read Back COMMITTED Recovery Replay": {"json": invalid_committed},
                    "Read Back Exact Actual Verification Receipt Replay": {"json": invalid_receipt},
                },
            )
            self.assertFalse(rejected["ok"])
        unreleased = self.run_exported_workflow_node(
            "20-actual-outbox-apply.json",
            "Return Verified Commit Receipt Replay",
            receipt,
            {
                **references,
                "Read Back Released Recovery Writer Fence Replay": {"json": {
                    "resource_key": "actual:actual-file:replay-1",
                    "lease_owner": "n8n:recovery:outbox:replay-1",
                    "fencing_token": 7,
                    "released": False,
                }},
            },
        )
        self.assertFalse(unreleased["ok"])

    def test_writer_lease_uses_only_fixed_parameterized_postgres_functions(self) -> None:
        workflow = self.workflow("18-finance-writer-lease.json")
        postgres = [node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.postgres"]
        self.assertEqual(len(postgres), 3)
        queries = "\n".join(node["parameters"]["query"] for node in postgres)
        for function in ("finance_ops.acquire_writer_lease", "finance_ops.assert_writer_lease", "finance_ops.release_writer_lease"):
            self.assertIn(function, queries)
        self.assertNotIn("={{", queries)
        self.assertTrue(all("$1" in node["parameters"]["query"] for node in postgres))
        migration = (N8N / "postgres" / "001-finance-writer-lease.sql").read_text(encoding="utf-8")
        for term in ("ON CONFLICT (resource_key) DO UPDATE", "current.fencing_token + 1", "current.expires_at <= clock_timestamp()", "assert_writer_lease", "release_writer_lease"):
            self.assertIn(term, migration)

    def test_error_workflow_redacts_then_upserts_reads_compares_and_marks(self) -> None:
        workflow = self.workflow("16-operations-error-handler.json")
        names = [
            node["name"] for node in workflow["nodes"]
            if node["type"] != "n8n-nodes-base.stickyNote"
        ]
        self.assertEqual(names[:7], [
            "Finance Workflow Failed", "Redact and Classify Failure",
            "Upsert Durable Failure Receipt", "Read Back Failure Receipt",
            "Compare Failure Receipt Readback", "Mark Failure Readback Verified",
            "Read Back Verified Failure Receipt",
        ])
        self.assertEqual(names[7:], [
            "Route Retryable Provider Failure", "Read Provider Circuit after Failure",
            "Build OPEN Provider Circuit", "Upsert OPEN Provider Circuit",
            "Read Back OPEN Provider Circuit", "Verify OPEN Circuit Readback",
        ])
        code = self.nodes("16-operations-error-handler.json")["Redact and Classify Failure"]["parameters"]["jsCode"]
        self.assertIn("[REDACTED]", code)
        self.assertIn("provider_code", code)
        self.assertIn("PROVIDER_CIRCUIT_READBACK_MISMATCH", self.nodes("16-operations-error-handler.json")["Verify OPEN Circuit Readback"]["parameters"]["jsCode"])

    def test_ai_contract_uses_subscription_runner_and_value_domains(self) -> None:
        contract = load_json(N8N / "codex-agent-handoff.json")
        runner = contract["runner_contract"]
        self.assertEqual(runner["credential"], "CHATGPT_CACHED_LOGIN")
        self.assertEqual(runner["forced_login_method"], "chatgpt")
        self.assertTrue(runner["api_key_fallback_forbidden"])
        self.assertEqual(runner["server_model_policy"], {
            "NORMAL": {"model": "gpt-5.6-luna", "reasoning_effort": "max"},
            "EXCEPTION": {"model": "gpt-5.6-sol", "reasoning_effort": "medium"},
        })
        handoff = load_json(N8N / contract["request_schema"])
        proposal = load_json(N8N / contract["output_schema"])
        try:
            import jsonschema
        except ImportError:
            jsonschema = None
        if jsonschema:
            jsonschema.validators.validator_for(handoff).check_schema(handoff)
            jsonschema.validators.validator_for(proposal).check_schema(proposal)
            base = {
                "schema_version": 1,
                "job_id": f"finance-ai:{'a' * 64}",
                "idempotency_key": "a" * 64,
                "agent_provider": "CODEX_SUBSCRIPTION",
                "policy_id": "classify-unresolved",
                "policy_class": "NORMAL",
                "policy_sha256": "b" * 64,
                "config_sha256": "c" * 64,
                "output_schema_sha256": "d" * 64,
                "runner_receipt_id": "receipt-1",
                "runner_model": "gpt-5.6-luna",
                "runner_reasoning_effort": "max",
                "auth_mode": "CHATGPT_SUBSCRIPTION",
            }
            typed_values = {
                "vendor": "Carrefour",
                "tags": ["grocery", "in_store"],
                "review_required": False,
                "category_recommendation": {
                    "name": "Groceries", "group": "Living", "reason": "Exact merchant",
                },
                "rule_recommendation": {"enabled": False, "evidence_count": 3},
            }
            for field, value in typed_values.items():
                result = {
                    **base,
                    "proposals": [{
                        "transaction_id": "actual:fixture:1",
                        "field": field,
                        "value": value,
                        "confidence": 0.95,
                        "reason_code": "FIXTURE_MATCH",
                    }],
                }
                jsonschema.validate(result, proposal)
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate({
                    **base,
                    "proposals": [{
                        "transaction_id": "actual:fixture:1",
                        "field": "vendor",
                        "value": False,
                        "confidence": 0.95,
                        "reason_code": "TYPE_MISMATCH",
                    }],
                }, proposal)
            claude = {
                **base,
                "agent_provider": "CLAUDE_SUBSCRIPTION",
                "runner_model": "claude-sonnet-4-6",
                "runner_reasoning_effort": "default",
                "auth_mode": "CLAUDE_SUBSCRIPTION",
                "proposals": [],
            }
            jsonschema.validate(claude, proposal)
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate({**claude, "auth_mode": "CHATGPT_SUBSCRIPTION"}, proposal)
        proposal_item = proposal["properties"]["proposals"]["items"]
        self.assertIn("value", proposal_item["required"])
        self.assertNotIn("value_json", json.dumps(proposal_item))
        self.assertEqual(len(proposal_item["oneOf"]), 5)
        unresolved = handoff["$defs"]["unresolved"]
        self.assertIn("allowed_values", unresolved["required"])
        self.assertEqual(unresolved["properties"]["allowed_values"]["maxProperties"], 10)
        self.assertEqual(
            set(proposal["properties"]["auth_mode"]["enum"]),
            {"CHATGPT_SUBSCRIPTION", "CLAUDE_SUBSCRIPTION"},
        )
        self.assertEqual(
            set(handoff["properties"]["agent_provider"]["enum"]),
            {"CODEX_SUBSCRIPTION", "CLAUDE_SUBSCRIPTION"},
        )

    def test_ai_workflow_derives_profile_enforces_domains_and_omits_internal_hash(self) -> None:
        nodes = self.nodes("09-ai-proposal.json")
        untrusted = nodes["Validate Untrusted Proposal Request"]["parameters"]["jsCode"]
        validation = nodes["Build Authoritative Redacted Proposal Job"]["parameters"]["jsCode"]
        response = nodes["Validate Proposal Schema and Policy Boundary"]["parameters"]["jsCode"]
        for forbidden in ("agent_profile", "policy_sha256", "config_sha256", "output_schema_sha256"):
            self.assertIn(f"'{forbidden}'", untrusted)
        self.assertIn("finance_ai_policy_contracts", json.dumps(nodes["Read Active Server AI Policy Contract"]))
        compact_validation = re.sub(r"\s+", "", validation)
        self.assertIn("LUNA_MAX:'NORMAL'", compact_validation)
        self.assertIn("SOL_MEDIUM:'EXCEPTION'", compact_validation)
        self.assertIn("agent_provider", validation)
        self.assertIn("Missing bounded server value domain", validation)
        self.assertIn("request_canonical", validation)
        self.assertIn("Proposal outside configured domain", response)
        self.assertIn("Duplicate proposal field", response)
        self.assertIn("Agent proposal envelope mismatch", response)
        self.assertIn("CLAUDE_SUBSCRIPTION", response)
        self.assertIn("claude-sonnet-4-6", response)
        self.assertNotIn("CLAUDE_SUBSCRIPTION_RUNNER_NOT_ACTIVATED", response)
        handoff_code = nodes["Build Idempotent Agent Handoff"]["parameters"]["jsCode"]
        self.assertIn("agent_provider: request.agent_provider", handoff_code)
        adapter = nodes["Invoke Subscription Agent Adapter"]
        self.assertEqual(adapter["type"], "n8n-nodes-base.executeWorkflow")
        self.assertEqual(
            adapter["parameters"]["workflowId"]["value"],
            self.workflow("21-subscription-agent-adapter.json")["id"],
        )

    def test_ai_policy_targets_are_complete_and_profile_owned(self) -> None:
        policies = load_json(ROOT / "config" / "ai-policies.json")["policies"]
        target_contract = load_json(N8N / "ai-policy-targets.json")
        configured = {target for policy in policies for target in policy["target_fields"]}
        self.assertEqual(configured, set(target_contract["target_fields"]))
        schema = load_json(N8N / "contracts" / "codex-agent-handoff-v1.schema.json")
        schema_targets = set(schema["$defs"]["unresolved"]["properties"]["allowed_fields"]["items"]["enum"])
        self.assertTrue(configured.issubset(schema_targets))
        self.assertEqual(schema_targets - configured, {"review_required"})
        for policy in policies:
            self.assertIn(policy["agent_profile"], target_contract["profile_policy"])
            self.assertRegex(policy["policy_id"], r"^[a-z0-9][a-z0-9:_-]{0,127}$")

    def test_ai_policy_contract_compiler_is_current_and_server_owned(self) -> None:
        result = subprocess.run(
            [sys.executable, str(N8N / "compile_ai_policy_contracts.py")],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source = load_json(ROOT / "config" / "ai-policies.json")["policies"]
        seed = load_json(N8N / "generated" / "ai-policy-contracts.seed.json")
        self.assertEqual(seed["contract_status"], "SPEC_ONLY")
        self.assertEqual({row["policy_id"] for row in source}, {row["policy_id"] for row in seed["rows"]})
        for row in seed["rows"]:
            self.assertRegex(row["policy_sha256"], r"^[a-f0-9]{64}$")
            self.assertRegex(row["config_sha256"], r"^[a-f0-9]{64}$")
            self.assertRegex(row["output_schema_sha256"], r"^[a-f0-9]{64}$")
            self.assertTrue(json.loads(row["allowed_fields_json"]))
            self.assertIsInstance(json.loads(row["allowed_values_json"]), dict)

    def test_platform_bootstrap_generator_is_current(self) -> None:
        result = subprocess.run(
            [sys.executable, str(N8N / "generate_platform_bootstrap.py"), "--check"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = load_json(N8N / "generated" / "platform-bootstrap-manifest.json")
        self.assertEqual(manifest["contract_status"], "SPEC_ONLY")
        self.assertEqual(manifest["n8n_version"], "2.36.2")
        self.assertEqual(set(manifest["execution_evidence"].values()), {False})
        self.assertEqual(
            manifest["sources"]["data_tables_sha256"],
            git_canonical_sha256(N8N / "data-tables.json"),
        )
        self.assertEqual(
            manifest["sources"]["ai_policy_seed_sha256"],
            git_canonical_sha256(
                N8N / "generated" / "ai-policy-contracts.seed.json"
            ),
        )
        self.assertEqual(
            manifest["sources"]["config_version_seed_sha256"],
            git_canonical_sha256(N8N / "generated" / "config-versions.seed.json"),
        )
        self.assertEqual(
            manifest["sources"]["application_contract_bundle_sha256"],
            git_canonical_sha256(N8N / "generated" / "application-contract-bundle.json"),
        )
        self.assertEqual(
            manifest["sources"]["application_contract_bundle_schema_sha256"],
            git_canonical_sha256(N8N / "generated" / "application-contract-bundle.schema.json"),
        )
        result = subprocess.run(
            [sys.executable, str(N8N / "compile_config_versions.py")],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_platform_bootstrap_check_detects_stale_artifact(self) -> None:
        generator = load_bootstrap_generator()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = tuple(root / name for name in ("bundle", "schema", "manifest", "workflow"))
            expected = ("bundle\n", "schema\n", "manifest\n", "workflow\n")
            for path, text in zip(paths, expected):
                path.write_text(text, encoding="utf-8")
            with mock.patch.object(generator, "BUNDLE_PATH", paths[0]), \
                mock.patch.object(generator, "BUNDLE_SCHEMA_PATH", paths[1]), \
                mock.patch.object(generator, "MANIFEST_PATH", paths[2]), \
                mock.patch.object(generator, "WORKFLOW_PATH", paths[3]), \
                mock.patch.object(generator, "render", return_value=expected):
                self.assertEqual(generator.main(["--check"]), 0)
                paths[0].write_text("stale\n", encoding="utf-8")
                self.assertEqual(generator.main(["--check"]), 1)
                with self.assertRaises(SystemExit) as error:
                    generator.main(["--check", "--write"])
                self.assertEqual(error.exception.code, 2)

    def test_application_contract_bundle_is_schema_valid_ordered_and_self_hashed(self) -> None:
        bundle_path = N8N / "generated" / "application-contract-bundle.json"
        schema_path = N8N / "generated" / "application-contract-bundle.schema.json"
        bundle = load_json(bundle_path)
        schema = load_json(schema_path)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        validator.validate(bundle)
        self.assertEqual(bundle["schema_version"], 1)
        self.assertEqual(bundle["contract_status"], "SPEC_ONLY")
        self.assertEqual(bundle["schema_sha256"], git_canonical_sha256(schema_path))
        self.assertEqual(bundle["resolver_order"], ["W02", "W04", "W05", "W09"])
        self.assertEqual(
            [resolver["workflow_code"] for resolver in bundle["resolver_maps"]],
            bundle["resolver_order"],
        )
        payload = dict(bundle)
        payload.pop("bundle_content_sha256")
        canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        self.assertEqual(
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            bundle["bundle_content_sha256"],
        )
        expected_sources = {
            "config/statement-sources.json",
            "config/transaction-email-sources.json",
            "config/ai-policies.json",
        }
        self.assertEqual({source["path"] for source in bundle["source_documents"]}, expected_sources)
        for source in bundle["source_documents"]:
            self.assertEqual(source["sha256"], git_canonical_sha256(ROOT / source["path"]))
        self.assertEqual(
            [resolver["sources"][0]["path"] for resolver in bundle["resolver_maps"]],
            [
                "config/transaction-email-sources.json",
                "config/statement-sources.json",
                "config/statement-sources.json",
                "config/ai-policies.json",
            ],
        )
        workflow = self.workflow("19-platform-data-table-bootstrap.json")
        nodes = self.nodes("19-platform-data-table-bootstrap.json")
        self.assertEqual(
            nodes["Load and Validate Application Contract Bundle"]["type"],
            "n8n-nodes-base.code",
        )
        self.assertEqual(
            nodes["SHA-256 Application Contract Bundle"]["parameters"]["type"],
            "SHA256",
        )
        verify = nodes["Verify Application Contract Bundle Digest and Maps"]["parameters"]["jsCode"]
        self.assertIn("APPLICATION_CONTRACT_BUNDLE_DIGEST_MISMATCH", verify)
        self.assertIn("APPLICATION_CONTRACT_BUNDLE_RESOLVER_MAP_INVALID", verify)
        self.assertEqual(
            workflow["connections"]["Manual Platform Bootstrap Only"]["main"][0][0]["node"],
            "Load and Validate Application Contract Bundle",
        )
        duplicate_key = deepcopy(bundle)
        duplicate_key["resolver_maps"][0]["entries"][1]["key"] = duplicate_key["resolver_maps"][0]["entries"][0]["key"]
        with self.assertRaises(ValidationError):
            validator.validate(duplicate_key)
        arbitrary_source_contract = deepcopy(bundle)
        arbitrary_source_contract["source_contracts"][0]["contract"]["unapproved"] = True
        with self.assertRaises(ValidationError):
            validator.validate(arbitrary_source_contract)
        arbitrary_policy_contract = deepcopy(bundle)
        arbitrary_policy_contract["ai_policy_contracts"][0]["contract"]["unapproved"] = True
        with self.assertRaises(ValidationError):
            validator.validate(arbitrary_policy_contract)

    def test_application_contract_bundle_preserves_transaction_semantics_and_unique_ids(self) -> None:
        transaction_sources = load_json(ROOT / "config" / "transaction-email-sources.json")["sources"]
        expected_codes = [row["code"] for row in transaction_sources]
        self.assertEqual(expected_codes, [
            "RAKBANK_CARD_TRANSACTION",
            "STANDARD_CHARTERED_CARD_TRANSACTION",
        ])
        bundle = load_json(N8N / "generated" / "application-contract-bundle.json")
        transaction_contracts = [
            row for row in bundle["source_contracts"]
            if row["source_path"] == "config/transaction-email-sources.json"
        ]
        self.assertEqual([row["source_code"] for row in transaction_contracts], expected_codes)
        self.assertEqual(
            [row["key"] for row in bundle["resolver_maps"][0]["entries"]],
            expected_codes,
        )
        self.assertEqual(len({row["source_code"] for row in transaction_contracts}), len(expected_codes))

        generator = load_bootstrap_generator()
        documents = {
            path: load_json(ROOT / path)
            for path in generator.APPLICATION_CONFIG_PATHS
        }
        documents["config/transaction-email-sources.json"]["sources"][1]["code"] = expected_codes[0]
        with self.assertRaisesRegex(ValueError, "duplicate code identities"):
            generator.build_source_contracts(documents, bundle["source_documents"])

    def test_generated_source_hashes_use_git_canonical_lf_bytes(self) -> None:
        seed = load_json(N8N / "generated" / "ai-policy-contracts.seed.json")
        policies_hash = git_canonical_sha256(ROOT / "config" / "ai-policies.json")
        schema_hash = git_canonical_sha256(
            N8N / "contracts" / "ai-proposal-v1.schema.json"
        )
        for row in seed["rows"]:
            self.assertEqual(row["config_sha256"], policies_hash)
            self.assertEqual(row["output_schema_sha256"], schema_hash)

        fixture_manifest = load_json(N8N / "disposable" / "fixture-manifest.json")
        for filename, digest in fixture_manifest["source_workflow_sha256"].items():
            self.assertEqual(digest, git_canonical_sha256(WORKFLOWS / filename))

    def test_platform_bootstrap_is_manual_only_native_and_nonfinancial(self) -> None:
        workflow = self.workflow("19-platform-data-table-bootstrap.json")
        registry_row = next(
            row for row in self.registry["workflows"]
            if row["code"] == "PLATFORM_DATA_TABLE_BOOTSTRAP"
        )
        self.assertTrue(registry_row["manual_only"])
        self.assertIsNone(registry_row["schedule"])
        self.assertFalse(registry_row["mcp_exposed"])
        self.assertEqual(workflow["meta"]["migrationStatus"], "SPEC_ONLY")
        self.assertTrue(workflow["meta"]["manualOnly"])
        self.assertTrue(workflow["meta"]["platformBootstrapOnly"])
        self.assertTrue(workflow["meta"]["financeLedgerMutationForbidden"])
        self.assertTrue(workflow["meta"]["actualMutationForbidden"])
        triggers = [
            node for node in workflow["nodes"]
            if node["type"] == "n8n-nodes-base.manualTrigger"
        ]
        self.assertEqual(len(triggers), 1)
        self.assertEqual(
            {node["type"] for node in workflow["nodes"]},
            {
                "n8n-nodes-base.manualTrigger",
                "n8n-nodes-base.dataTable",
                "n8n-nodes-base.code",
                "n8n-nodes-base.crypto",
                "n8n-nodes-base.stickyNote",
            },
        )
        self.assertFalse(any(
            node["type"].startswith("n8n-nodes-finance.")
            or node["type"] in {
                "n8n-nodes-base.postgres", "n8n-nodes-base.httpRequest",
                "n8n-nodes-base.microsoftOutlook", "n8n-nodes-base.microsoftOneDrive",
            }
            for node in workflow["nodes"]
        ))

    def test_platform_bootstrap_creates_every_declared_table_with_exact_columns(self) -> None:
        workflow = self.workflow("19-platform-data-table-bootstrap.json")
        manifest = load_json(N8N / "generated" / "platform-bootstrap-manifest.json")
        expected = {row["name"]: row["columns"] for row in self.tables["tables"]}
        creates = [
            node for node in workflow["nodes"]
            if node["type"] == "n8n-nodes-base.dataTable"
            and node["parameters"].get("resource") == "table"
            and node["parameters"].get("operation") == "create"
        ]
        self.assertEqual(len(creates), len(expected))
        self.assertEqual({node["parameters"]["tableName"] for node in creates}, set(expected))
        for node in creates:
            parameters = node["parameters"]
            name = parameters["tableName"]
            self.assertEqual(parameters["options"], {"createIfNotExists": True})
            self.assertEqual(
                parameters["columns"]["column"],
                [
                    {"name": column, "type": column_type}
                    for column, column_type in expected[name].items()
                ],
            )
        self.assertEqual(
            [entry["parameters"] for entry in manifest["table_create_operations"]],
            [node["parameters"] for node in creates],
        )
        self.assertEqual(
            creates[0]["parameters"]["tableName"], "finance_execution_failures"
        )

    def test_platform_bootstrap_seeds_and_exactly_reads_policy_and_config_contracts(self) -> None:
        nodes = self.nodes("19-platform-data-table-bootstrap.json")
        data_nodes = [
            node for node in nodes.values()
            if node["type"] == "n8n-nodes-base.dataTable"
            and node["parameters"].get("resource") == "row"
        ]
        self.assertEqual(
            [(node["parameters"]["operation"], node["parameters"]["dataTableId"]["value"])
             for node in data_nodes],
            [
                ("upsert", "finance_ai_policy_contracts"),
                ("get", "finance_ai_policy_contracts"),
                ("upsert", "finance_config_versions"),
                ("get", "finance_config_versions"),
            ],
        )
        upsert = nodes["Upsert AI Policy Contracts"]["parameters"]
        self.assertEqual(
            [(row["keyName"], row["condition"]) for row in upsert["filters"]["conditions"]],
            [("policy_id", "eq"), ("policy_version", "eq")],
        )
        readback = nodes["Read Back All ACTIVE AI Policy Contracts"]
        self.assertTrue(readback["parameters"]["returnAll"])
        self.assertTrue(readback["alwaysOutputData"])
        self.assertEqual(
            readback["parameters"]["filters"]["conditions"],
            [{"keyName": "state", "condition": "eq", "keyValue": "ACTIVE"}],
        )
        compare = nodes["Exact Compare AI Policy Seed Readback"]["parameters"]["jsCode"]
        compact_compare = re.sub(r"\s+", "", compare)
        for marker in (
            "AI_POLICY_ACTIVE_COUNT_MISMATCH",
            "AI_POLICY_DUPLICATE_ACTIVE_VERSION",
            "AI_POLICY_READBACK_MISSING",
            "AI_POLICY_READBACK_MISMATCH",
            "AI_POLICY_UPDATED_AT_INVALID",
            "finance_ledger_writes:false",
            "actual_writes:false",
        ):
            self.assertIn(marker, compact_compare if ":false" in marker else compare)
        seed = load_json(N8N / "generated" / "ai-policy-contracts.seed.json")
        policy_code = nodes["Emit Versioned AI Policy Seed"]["parameters"]["jsCode"]
        for row in seed["rows"]:
            for field in ("policy_id", "policy_sha256", "agent_profile", "agent_provider"):
                self.assertIn(json.dumps(row[field]), policy_code)
        config_seed = load_json(N8N / "generated" / "config-versions.seed.json")
        config_code = nodes["Emit Versioned Config Fingerprints"]["parameters"]["jsCode"]
        for row in config_seed["rows"]:
            for field in ("config_name", "version", "content_sha256"):
                self.assertIn(json.dumps(row[field]), config_code)
        for node_name in ("Upsert AI Policy Contracts", "Upsert Config Version Fingerprints"):
            mappings = nodes[node_name]["parameters"]["columns"]["value"]
            for value in mappings.values():
                self.assertRegex(value, r"^=\{\{ \$json\.[a-z0-9_]+ \}\}$")
        self.assertEqual(
            nodes["Upsert AI Policy Contracts"]["parameters"]["columns"]["value"]["policy_version"],
            "={{ $json.policy_version }}",
        )

    def test_mcp_facade_dispatch_is_durably_audited_and_read_back(self) -> None:
        facade = self.nodes("15-finance-mcp-facade.json")
        for name in (
            "finance.status.v1",
            "finance.reviewed-artifact.handoff.v1",
            "finance.document.request.v1",
        ):
            params = facade[name]["parameters"]
            self.assertEqual(params["workflowId"]["value"], "10000000-0000-4000-8000-000000000010")
            self.assertIn("_mcp_request_id", params["workflowInputs"]["value"])
        artifact_inputs = facade["finance.reviewed-artifact.handoff.v1"]["parameters"]["workflowInputs"]["value"]
        self.assertEqual(set(artifact_inputs), {"_mcp_request_id", "operation_code", "artifact_id"})
        self.assertNotIn("expected_sha256", artifact_inputs)
        self.assertIn("server-owned", facade["finance.reviewed-artifact.handoff.v1"]["parameters"]["description"])
        nodes = self.nodes("10-finance-operations-status.json")
        for name in (
            "Upsert ACCEPTED MCP Request", "Read Back ACCEPTED MCP Request",
            "Upsert Terminal MCP Request", "Read Back Terminal MCP Request",
            "Mark MCP Receipt Verified", "Read Verified MCP Receipt",
        ):
            self.assertIn("finance_mcp_requests", json.dumps(nodes[name]))
        terminal = nodes["Build Redacted MCP Terminal Receipt"]["parameters"]["jsCode"]
        self.assertIn("[REDACTED]", terminal)
        self.assertIn("FAILED", terminal)
        dispatch_validation = nodes["Validate Bounded MCP Dispatch"]["parameters"]["jsCode"]
        self.assertIn("'artifact.submit_reviewed': ['artifact_id']", dispatch_validation)
        self.assertNotIn("'artifact.submit_reviewed': ['artifact_id', 'expected_sha256']", dispatch_validation)
        dispatch_inputs = nodes["Dispatch Reviewed Artifact"]["parameters"]["workflowInputs"]["value"]
        self.assertEqual(set(dispatch_inputs), {"operation_code", "artifact_id"})
        self.assertNotIn("expected_sha256", dispatch_inputs)

    def test_ai_proposal_is_archived_hash_verified_and_left_pending_review(self) -> None:
        nodes = self.nodes("09-ai-proposal.json")
        self.assertEqual(nodes["Convert Proposal Artifact to File"]["type"], "n8n-nodes-base.convertToFile")
        self.assertEqual(nodes["Archive Proposal Artifact in OneDrive"]["type"], "n8n-nodes-base.microsoftOneDrive")
        self.assertEqual(nodes["Read Back Proposal Artifact"]["parameters"]["operation"], "download")
        self.assertIn("AGENT_PROPOSAL_ARTIFACT_HASH_MISMATCH", nodes["Verify Proposal Artifact Readback"]["parameters"]["jsCode"])
        values = nodes["Upsert SUCCEEDED Agent Job"]["parameters"]["columns"]["value"]
        self.assertEqual(values["review_state"], "PENDING")
        self.assertTrue({"proposal_artifact_item_id", "proposal_artifact_etag", "proposal_artifact_schema"}.issubset(values))

    def test_disposable_fixture_workflows_are_generated_current_and_hashed(self) -> None:
        disposable = N8N / "disposable"
        generated = disposable / "generated"
        result = subprocess.run(
            [sys.executable, str(disposable / "generate_fixture_workflows.py")],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = load_json(disposable / "fixture-manifest.json")
        self.assertEqual(manifest["contract_status"], "DISPOSABLE_ONLY")
        self.assertTrue(manifest["production_import_forbidden"])
        self.assertEqual(manifest["required_acknowledgement"], "DISPOSABLE_ONLY")
        self.assertEqual(len(manifest["workflows"]), 18)
        for row in manifest["workflows"]:
            path = generated / row["file"]
            self.assertTrue(path.is_file())
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row["sha256"])

    def test_disposable_fixtures_are_inactive_manual_and_external_write_free(self) -> None:
        generated = N8N / "disposable" / "generated"
        fixtures = [load_json(path) for path in sorted(generated.glob("*.json"))]
        production_ids = {workflow["id"] for workflow in self.workflows.values()}
        registry_files = {row["file"] for row in self.registry["workflows"]}
        forbidden = {
            "n8n-nodes-base.scheduleTrigger",
            "n8n-nodes-base.webhook",
            "@n8n/n8n-nodes-langchain.mcpTrigger",
            "n8n-nodes-base.microsoftOutlook",
            "n8n-nodes-base.microsoftOneDrive",
            "n8n-nodes-finance.actualBudget",
        }
        for workflow in fixtures:
            with self.subTest(workflow=workflow["name"]):
                self.assertFalse(workflow["active"])
                self.assertNotIn(workflow["id"], production_ids)
                self.assertTrue(workflow["meta"]["disposableOnly"])
                self.assertTrue(workflow["meta"]["productionImportForbidden"])
                self.assertFalse({node["type"] for node in workflow["nodes"]} & forbidden)
                self.assertTrue(any(
                    node["type"] in {
                        "n8n-nodes-base.manualTrigger",
                        "n8n-nodes-base.executeWorkflowTrigger",
                    }
                    for node in workflow["nodes"]
                ))
        self.assertFalse(registry_files & {path.name for path in generated.glob("*.json")})

    def test_disposable_fixture_matrix_covers_runtime_requested_boundaries(self) -> None:
        manifest = load_json(N8N / "disposable" / "fixture-manifest.json")
        scenarios = manifest["scenario_contract"]
        self.assertEqual(scenarios["sweep_zero"]["expected"]["scanned_count"], 0)
        self.assertTrue(scenarios["sweep_zero"]["expected"]["heartbeat"])
        self.assertEqual(scenarios["sweep_one_no_attachments"]["expected"]["scanned_count"], 1)
        self.assertEqual(scenarios["sweep_one_no_attachments"]["expected"]["matched_count"], 1)
        self.assertEqual(scenarios["sweep_one_no_attachments"]["expected"]["attachment_identity_keys"], [])
        fixture_ids = {row["id"] for row in manifest["workflows"]}
        scenario_ids = {
            workflow_id
            for scenario in scenarios.values()
            for workflow_id in (
                ([scenario["workflow_id"]] if "workflow_id" in scenario else [])
                + scenario.get("workflow_ids", [])
            )
        }
        self.assertTrue(scenario_ids <= fixture_ids)
        self.assertEqual(scenarios["sweep_101"]["expected"]["attachment_identity_keys"], [])
        self.assertEqual(scenarios["sweep_101"]["expected"]["scanned_count"], 101)
        self.assertEqual(scenarios["sweep_late_order"]["expected_ids"], ["m1", "m2", "m3"])
        self.assertEqual(scenarios["sweep_pagination_failure"]["expected_exit"], "nonzero")
        self.assertTrue(scenarios["lease_concurrency"]["run_concurrently"])
        self.assertEqual(scenarios["lease_concurrency"]["expected_successes"], 1)
        self.assertEqual(scenarios["lease_stale"]["expected_error"], "WRITER_LEASE_STALE")
        self.assertEqual(scenarios["ai_negative"]["runner_calls"], 0)
        self.assertEqual(
            scenarios["ai_positive_luna"],
            {
                "workflow_id": "90000000-0000-4000-8000-000000000911",
                "expected_exit": 0,
                "policy_id": "classify-unresolved",
                "expected_model": "gpt-5.6-luna",
                "expected_reasoning_effort": "max",
                "expected_auth_mode": "CHATGPT_SUBSCRIPTION",
                "finance_writes": 0,
            },
        )
        sol = scenarios["ai_positive_sol_gated"]
        self.assertEqual(sol["workflow_id"], "90000000-0000-4000-8000-000000000912")
        self.assertEqual(sol["expected_model"], "gpt-5.6-sol")
        self.assertEqual(sol["expected_reasoning_effort"], "medium")
        self.assertEqual(sol["execution_gate"], "DISPOSABLE_ALLOW_SOL_MEDIUM")
        self.assertTrue(sol["default_execution_forbidden"])
        self.assertEqual(scenarios["outbox_recovery"]["expected_state"], "COMMITTED")
        self.assertEqual(scenarios["outbox_recovery"]["finance_writes"], 0)
        self.assertEqual(scenarios["error_redaction"]["receipt_table"], "finance_execution_failures")
        self.assertEqual(
            set(manifest["blocked_runtime_scenarios"]),
            {
                "bounded_mcp_network_negative",
                "real_actual_recovery_write",
            },
        )

    def test_positive_ai_wrappers_are_fixed_redacted_and_model_unselectable(self) -> None:
        generated = N8N / "disposable" / "generated"
        luna = load_json(generated / "106-ai-positive-luna.json")
        sol = load_json(generated / "107-ai-positive-sol-gated.json")
        for workflow, policy_id in (
            (luna, "classify-unresolved"),
            (sol, "recommend-category"),
        ):
            serialized = json.dumps(workflow)
            self.assertIn(policy_id, serialized)
            self.assertIn("10000000-0000-4000-8000-000000000009", serialized)
            for forbidden in (
                '"model"', '"url"', '"credential"', '"prompt"',
                '"policy_sha256"', '"config_sha256"', '"output_schema_sha256"',
                '"amount"', '"source_id"', '"dedupe_key"',
            ):
                self.assertNotIn(forbidden, serialized)
            self.assertTrue(workflow["meta"]["financeWritesImpossible"])
        self.assertEqual(sol["meta"]["executionGate"], "DISPOSABLE_ALLOW_SOL_MEDIUM")
        self.assertTrue(sol["meta"]["defaultExecutionForbidden"])

    def test_rule_ownership_compiler_is_current_disjoint_and_complete(self) -> None:
        result = subprocess.run(
            [sys.executable, str(N8N / "compile_rule_ownership.py")],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = load_json(N8N / "generated" / "rule-ownership-manifest.json")
        actual = load_json(N8N / "generated" / "actual-rules.json")
        n8n_rules = load_json(N8N / "generated" / "n8n-runtime-rules.json")
        self.assertEqual(manifest["overlap"], [])
        self.assertEqual(manifest["unowned"], [])
        actual_keys = {(row["rule_id"], scope) for row in actual["rules"] for scope in row["rule_sets"]}
        n8n_keys = {(row["rule_id"], scope) for row in n8n_rules["rules"] for scope in row["rule_sets"]}
        owned_keys = {(row["rule_id"], row["rule_set"]) for row in manifest["ownership"]}
        self.assertFalse(actual_keys & n8n_keys)
        self.assertEqual(actual_keys | n8n_keys, owned_keys)
        self.assertTrue(all(row["execution_owner"] == "ACTUAL" and row["actual_representable"] for row in actual["rules"]))
        self.assertTrue(all(row["execution_owner"] == "N8N_ONLY" and not row["actual_representable"] for row in n8n_rules["rules"]))

    def test_workflow_ui_renderer_is_current_readable_and_idempotent(self) -> None:
        renderer = N8N / "refactor_workflow_ui.py"
        before = {path.name: path.read_bytes() for path in WORKFLOWS.glob("*.json")}
        result = subprocess.run(
            [sys.executable, str(renderer), "--check"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        after = {path.name: path.read_bytes() for path in WORKFLOWS.glob("*.json")}
        self.assertEqual(before, after)
        for filename, workflow in self.workflows.items():
            self.assertNotIn("SPEC ONLY", workflow["name"].upper())
            self.assertTrue(workflow["name"].endswith("Setup Required"))
            notes = [node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.stickyNote"]
            self.assertTrue(notes, filename)
            for note in notes:
                content = note["parameters"]["content"]
                self.assertIn("**Input:**", content)
                self.assertIn("**Output:**", content)
                self.assertIn("failure", content.casefold())
            for node in workflow["nodes"]:
                if node["type"] != "n8n-nodes-base.code":
                    continue
                code = node["parameters"]["jsCode"]
                self.assertEqual(code.count("// Purpose:"), 1, f"{filename}::{node['name']}")
                self.assertGreaterEqual(len(code.splitlines()), 2, f"{filename}::{node['name']}")
                self.assertLessEqual(max(map(len, code.splitlines())), 600, f"{filename}::{node['name']}")

    def test_canvas_groups_are_native_valid_and_exclude_triggers(self) -> None:
        trigger_types = {
            "n8n-nodes-base.manualTrigger", "n8n-nodes-base.scheduleTrigger",
            "n8n-nodes-base.executeWorkflowTrigger", "n8n-nodes-base.errorTrigger",
            "@n8n/n8n-nodes-langchain.mcpTrigger",
        }
        grouped_workflows = 0
        for filename, workflow in self.workflows.items():
            by_id = {node["id"]: node for node in workflow["nodes"]}
            groups = workflow.get("nodeGroups", [])
            grouped_workflows += bool(groups)
            seen: set[str] = set()
            for group in groups:
                self.assertEqual(set(group), {"name", "nodeIds", "description"})
                self.assertGreaterEqual(len(group["nodeIds"]), 2)
                self.assertTrue(group["description"].startswith("Finance stage"))
                for node_id in group["nodeIds"]:
                    self.assertIn(node_id, by_id, filename)
                    self.assertNotIn(by_id[node_id]["type"], trigger_types)
                    self.assertNotIn(node_id, seen, f"duplicate canvas group membership {filename}")
                    seen.add(node_id)
        self.assertGreaterEqual(grouped_workflows, 15)

    def test_workflow_folder_manifest_is_complete_and_post_import_guarded(self) -> None:
        contract = load_json(N8N / "workflow-folders.json")
        self.assertEqual(contract["n8n_version"], "2.36.2")
        self.assertEqual(len(contract["folders"]), 8)
        mapped = [code for folder in contract["folders"] for code in folder["workflow_codes"]]
        self.assertEqual(len(mapped), len(set(mapped)))
        self.assertEqual(set(mapped), {row["code"] for row in self.registry["workflows"]})
        by_code = {
            code: folder for folder in contract["folders"] for code in folder["workflow_codes"]
        }
        for workflow in self.workflows.values():
            code = workflow["meta"]["financeWorkflowCode"]
            self.assertEqual(workflow["meta"]["workflowFolder"]["id"], by_code[code]["id"])
            self.assertEqual(workflow["meta"]["workflowTags"], contract["tags"])
            self.assertEqual([tag["name"] for tag in workflow["tags"]], contract["tags"])
            self.assertEqual(len({tag["id"] for tag in workflow["tags"]}), len(contract["tags"]))
            self.assertNotIn("parentFolderId", workflow)
        sql = (N8N / "workflow-folder-placement.sql").read_text(encoding="utf-8")
        for marker in (
            "finance_project_id", "w.active = TRUE", "shared_workflow",
            "WORKFLOW_FOLDER_MAP_COUNT_MISMATCH", "WORKFLOW_FOLDER_READBACK_MISMATCH",
        ):
            self.assertIn(marker, sql)

    def test_execute_subworkflow_references_use_from_list(self) -> None:
        workflow_names = {workflow["id"]: workflow["name"] for workflow in self.workflows.values()}
        count = 0
        for filename, workflow in self.workflows.items():
            for node in workflow["nodes"]:
                if node["type"] not in {
                    "n8n-nodes-base.executeWorkflow",
                    "@n8n/n8n-nodes-langchain.toolWorkflow",
                }:
                    continue
                count += 1
                reference = node["parameters"]["workflowId"]
                self.assertEqual(reference["mode"], "list", f"{filename}::{node['name']}")
                self.assertEqual(reference["cachedResultName"], workflow_names[reference["value"]])
        self.assertGreaterEqual(count, 20)

    def test_outlook_and_onedrive_nodes_use_exact_binary_and_server_filter_contracts(self) -> None:
        for filename in ("01-outlook-finance-acquisition.json", "12-outlook-message-sweep.json"):
            outlook_nodes = [
                node for node in self.workflow(filename)["nodes"]
                if node["type"] == "n8n-nodes-base.microsoftOutlook"
                and node.get("parameters", {}).get("resource") == "folderMessage"
                and node.get("parameters", {}).get("operation") == "getAll"
            ]
            expected_count = 1 if filename == "12-outlook-message-sweep.json" else 0
            self.assertEqual(len(outlook_nodes), expected_count)
            if not outlook_nodes:
                continue
            params = outlook_nodes[0]["parameters"]
            self.assertEqual(params["output"], "raw")
            self.assertTrue(params["returnAll"])
            values = params["filtersUI"]["values"]
            self.assertEqual(values["filterBy"], "filters")
            filters = values["filters"]
            self.assertIn("receivedAfter", filters)
            self.assertIn("receivedBefore", filters)
            self.assertIn("custom", filters)
            self.assertNotIn("filters", params)
        uploads = []
        for workflow in self.workflows.values():
            uploads.extend(
                node for node in workflow["nodes"]
                if node["type"] == "n8n-nodes-base.microsoftOneDrive"
                and node.get("parameters", {}).get("operation") == "upload"
            )
        self.assertTrue(uploads)
        for node in uploads:
            self.assertEqual(node["typeVersion"], 1.1)
            self.assertTrue(node["parameters"]["binaryData"])
            self.assertEqual(node["parameters"]["binaryPropertyName"], "data")

        acquisition = self.workflow("01-outlook-finance-acquisition.json")
        self.assertNotIn("Get Messages from Configured Folder", acquisition["connections"])
        self.assertNotIn(
            "Exact Sender Subject and Window Filter",
            {node["name"] for node in acquisition["nodes"]},
        )
        sweep_connections = self.workflow("12-outlook-message-sweep.json")["connections"]
        self.assertEqual(
            sweep_connections["Exhaust Outlook Pagination"]["main"][0][0]["node"],
            "Aggregate Exact Window Heartbeat",
        )

    def test_interactive_browser_handoff_validates_before_archive_and_is_idempotent(self) -> None:
        table = next(
            row for row in self.tables["tables"]
            if row["name"] == "finance_document_operations"
        )
        nodes = self.nodes("11-interactive-artifact-handoff.json")
        self.assertTrue({"source_code", "config_version", "actual_file_id", "account_id", "period_key"}.issubset(table["columns"]))
        connections = self.workflow("11-interactive-artifact-handoff.json")["connections"]
        self.assertEqual(
            connections["Validate Browser Capture Schema"]["main"][0][0]["node"],
            "Load Existing Browser Archive Receipt",
        )
        self.assertEqual(
            connections["Check Existing Browser Artifact"]["main"][0][0]["node"],
            "New Browser Artifact?",
        )
        self.assertEqual(
            connections["New Browser Artifact?"]["main"][0][0]["node"],
            "Archive Browser Capture in OneDrive",
        )
        self.assertEqual(
            nodes["New Browser Artifact?"]["parameters"]["conditions"]["conditions"][0]["leftValue"],
            "={{ $json.idempotency_action }}",
        )
        idempotency = nodes["Check Existing Browser Artifact"]["parameters"]["jsCode"]
        self.assertIn("BROWSER_ARTIFACT_ID_HASH_CONFLICT", idempotency)
        self.assertIn("existing.output_sha256", idempotency)
        self.assertIn("idempotency_action: 'NOOP'", idempotency)
        verify = nodes["Verify Browser Archive Receipt"]["parameters"]["jsCode"]
        self.assertIn("BROWSER_ARCHIVE_HASH_INVALID", verify)
        self.assertTrue(nodes["Parse Browser Capture JSON Before Archive"]["type"] == "n8n-nodes-base.code")
        validate = nodes["Validate Browser Capture Schema"]["parameters"]["jsCode"]
        self.assertIn("BROWSER_CAPTURE_BINARY_HASH_MISMATCH", validate)
        self.assertIn("expected_source_sha256", validate)
        self.assertIn("expected_capture_sha256", validate)
        self.assertTrue(self.workflow("11-interactive-artifact-handoff.json")["meta"]["reuploadForbidden"])
        self.assertEqual(
            self.workflow("11-interactive-artifact-handoff.json")["meta"]["artifactIdHashConflict"],
            "BROWSER_ARTIFACT_ID_HASH_CONFLICT",
        )
        self.assertEqual(
            nodes["Dispatch Browser Capture to Headless Pipeline"]["parameters"]["workflowId"]["mode"],
            "list",
        )

        self.assertIn("MCP Reviewed Artifact?", nodes)
        self.assertIn("Load MCP Reviewed Document Record", nodes)
        self.assertIn("Validate MCP Durable Document Reference", nodes)
        self.assertIn("Download MCP Reviewed Capture", nodes)
        self.assertIn("Resolve Capture Hash Contract", nodes)
        reference = nodes["Validate Reviewed Artifact Reference"]["parameters"]["jsCode"]
        self.assertIn("MCP_REVIEWED_BINARY_FORBIDDEN", reference)
        self.assertIn("MCP_REVIEWED_HASHES_MUST_BE_SERVER_DERIVED", reference)
        self.assertIn("MCP_REVIEWED_FIELDS_FORBIDDEN", reference)
        self.assertIn("expected_source_sha256", reference)
        self.assertIn("expected_capture_sha256", reference)
        durable = nodes["Validate MCP Durable Document Reference"]["parameters"]["jsCode"]
        self.assertIn("MCP_REVIEWED_DOCUMENT_NOT_FOUND", durable)
        self.assertIn("server_source_sha256", durable)
        self.assertEqual(
            connections["Validate Reviewed Artifact Reference"]["main"][0][0]["node"],
            "MCP Reviewed Artifact?",
        )
        self.assertEqual(
            connections["MCP Reviewed Artifact?"]["main"][0][0]["node"],
            "Load MCP Reviewed Document Record",
        )
        self.assertEqual(
            connections["MCP Reviewed Artifact?"]["main"][1][0]["node"],
            "SHA-256 Browser Capture Input",
        )
        self.assertEqual(
            connections["Validate MCP Durable Document Reference"]["main"][0][0]["node"],
            "Download MCP Reviewed Capture",
        )
        self.assertEqual(
            connections["SHA-256 Browser Capture Input"]["main"][0][0]["node"],
            "Resolve Capture Hash Contract",
        )
        self.assertEqual(
            set(self.workflow("11-interactive-artifact-handoff.json")["meta"]["browserHandoff"]["handoff_modes"]),
            {"HEADED_CAPTURE", "MCP_REVIEWED"},
        )
        self.assertEqual(
            self.workflow("11-interactive-artifact-handoff.json")["meta"]["browserHandoff"]["mcp_reviewed_contract"],
            ["artifact_id"],
        )

    def test_browser_capture_fixtures_execute_against_embedded_canonical_schema(self) -> None:
        schema = load_json(ROOT / "config" / "browser-capture-schema-v1.json")
        code = self.nodes("11-interactive-artifact-handoff.json")["Validate Browser Capture Schema"]["parameters"]["jsCode"]
        embedded, _ = json.JSONDecoder().raw_decode(code.split("const schema = ", 1)[1])
        self.assertEqual(embedded, schema)

        valid = load_json(ROOT / "tests" / "fixtures" / "browser-captures" / "valid-transaction-rows.json")
        validate_fixture_against_schema(embedded, valid)
        capture_binary = json.dumps(
            valid,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        capture_binary_sha256 = hashlib.sha256(capture_binary).hexdigest()
        source_content_sha256 = valid["artifact"]["source_content_sha256"]
        self.assertNotEqual(capture_binary_sha256, source_content_sha256)
        self.assertEqual(valid, json.loads(capture_binary))

        invalid = load_json(ROOT / "tests" / "fixtures" / "browser-captures" / "invalid-forbidden-field.json")
        durable_storage: list[dict] = []

        def archive_if_valid(capture: dict, expected_source: str, expected_binary: str) -> None:
            validate_fixture_against_schema(embedded, capture)
            self.assertEqual(capture["artifact"]["source_content_sha256"], expected_source)
            actual_binary = hashlib.sha256(json.dumps(
                capture,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            self.assertEqual(actual_binary, expected_binary)
            durable_storage.append(capture)

        with self.assertRaises(AssertionError):
            archive_if_valid(invalid, source_content_sha256, capture_binary_sha256)
        self.assertEqual(durable_storage, [])

        archive_if_valid(valid, source_content_sha256, capture_binary_sha256)
        self.assertEqual([row["capture_id"] for row in durable_storage], [valid["capture_id"]])

        receipt = {
            "document_id": valid["capture_id"],
            "source_sha256": source_content_sha256,
            "output_sha256": capture_binary_sha256,
        }

        def replay(source_hash: str, binary_hash: str) -> str:
            if receipt["source_sha256"] != source_hash or receipt["output_sha256"] != binary_hash:
                raise ValueError("BROWSER_ARTIFACT_ID_HASH_CONFLICT")
            return "NOOP"

        self.assertEqual(replay(source_content_sha256, capture_binary_sha256), "NOOP")
        with self.assertRaisesRegex(ValueError, "BROWSER_ARTIFACT_ID_HASH_CONFLICT"):
            replay(source_content_sha256, "c" * 64)
        with self.assertRaisesRegex(ValueError, "BROWSER_ARTIFACT_ID_HASH_CONFLICT"):
            replay("d" * 64, capture_binary_sha256)

    def test_exported_w11_validator_and_idempotency_execute_real_fixture_bytes(self) -> None:
        valid = load_json(ROOT / "tests" / "fixtures" / "browser-captures" / "valid-transaction-rows.json")
        capture_binary = json.dumps(
            valid,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        capture_binary_sha256 = hashlib.sha256(capture_binary).hexdigest()
        source_content_sha256 = valid["artifact"]["source_content_sha256"]
        # n8n binary data is base64; keep the bytes exact while making this
        # harness independent of n8n's runtime item wrappers.
        binary = {"data": {"data": base64.b64encode(capture_binary).decode("ascii")}}
        contract = {
            "handoff_mode": "HEADED_CAPTURE",
            "artifact_id": valid["capture_id"],
            "expected_source_sha256": source_content_sha256,
            "expected_capture_sha256": capture_binary_sha256,
        }
        mcp_request = {
            "_mcp_request_id": "mcp-fixture-1",
            "operation_code": "artifact.submit_reviewed",
            "artifact_id": valid["capture_id"],
        }
        result = self.run_exported_node("Validate Reviewed Artifact Reference", mcp_request, {}, {})
        self.assertEqual(result["output"][0]["json"]["handoff_mode"], "MCP_REVIEWED")
        result = self.run_exported_node("Validate Reviewed Artifact Reference", mcp_request, binary, {})
        self.assertEqual(result, {"ok": False, "error": "MCP_REVIEWED_BINARY_FORBIDDEN"})
        result = self.run_exported_node(
            "Validate Reviewed Artifact Reference",
            {**mcp_request, "expected_capture_sha256": capture_binary_sha256},
            {},
            {},
        )
        self.assertEqual(result, {"ok": False, "error": "MCP_REVIEWED_HASHES_MUST_BE_SERVER_DERIVED"})
        result = self.run_exported_node(
            "Validate Reviewed Artifact Reference",
            {**mcp_request, "url": "https://client-controlled.example.test"},
            {},
            {},
        )
        self.assertEqual(result, {"ok": False, "error": "Artifact metadata must be resolved from durable server state"})
        result = self.run_exported_node(
            "Validate Reviewed Artifact Reference",
            {"artifact_id": valid["capture_id"], "expected_sha256": source_content_sha256,
             "expected_source_sha256": source_content_sha256, "expected_capture_sha256": capture_binary_sha256},
            binary,
            {},
        )
        self.assertEqual(result, {"ok": False, "error": "EXPECTED_SHA256_LEGACY_FORBIDDEN"})
        references = {
            "SHA-256 Browser Capture Input": {"json": {"input_sha256": capture_binary_sha256}},
            "Resolve Capture Hash Contract": {"json": contract, "binary": binary},
        }
        result = self.run_exported_node("Validate Browser Capture Schema", valid, binary, references)
        self.assertTrue(result["ok"], result)

        invalid = load_json(ROOT / "tests" / "fixtures" / "browser-captures" / "invalid-forbidden-field.json")
        invalid_bytes = json.dumps(
            invalid,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        invalid_binary = {"data": {"data": base64.b64encode(invalid_bytes).decode("ascii")}}
        invalid_hash = hashlib.sha256(invalid_bytes).hexdigest()
        invalid_refs = {
            "SHA-256 Browser Capture Input": {"json": {"input_sha256": invalid_hash}},
            "Resolve Capture Hash Contract": {"json": {
                **contract,
                "artifact_id": invalid["capture_id"],
                "expected_source_sha256": invalid["artifact"]["source_content_sha256"],
                "expected_capture_sha256": invalid_hash,
            }, "binary": invalid_binary},
        }
        result = self.run_exported_node("Validate Browser Capture Schema", invalid, invalid_binary, invalid_refs)
        self.assertEqual(result, {"ok": False, "error": "BROWSER_CAPTURE_FORBIDDEN_FIELD:capture.password"})
        durable_storage: list[dict] = []
        if result["ok"]:
            durable_storage.append(invalid)
        self.assertEqual(durable_storage, [])

        source_mismatch = {**contract, "expected_source_sha256": "d" * 64}
        mismatch_refs = {
            **references,
            "Resolve Capture Hash Contract": {"json": source_mismatch, "binary": binary},
        }
        result = self.run_exported_node("Validate Browser Capture Schema", valid, binary, mismatch_refs)
        self.assertEqual(result, {"ok": False, "error": "BROWSER_CAPTURE_PROVENANCE_MISMATCH"})

        binary_mismatch = {**contract, "expected_capture_sha256": "c" * 64}
        mismatch_refs["Resolve Capture Hash Contract"] = {"json": binary_mismatch, "binary": binary}
        result = self.run_exported_node("Validate Browser Capture Schema", valid, binary, mismatch_refs)
        self.assertEqual(result, {"ok": False, "error": "BROWSER_CAPTURE_BINARY_HASH_MISMATCH"})

        existing = {
            "document_id": valid["capture_id"],
            "source_sha256": source_content_sha256,
            "output_sha256": capture_binary_sha256,
            "onedrive_item_id": "one-drive-item",
            "document_profile": "BROWSER_CAPTURE_V1",
        }
        idempotency_references = {
            "Resolve Capture Hash Contract": {"json": contract, "binary": binary},
            "Validate Browser Capture Schema": {"json": valid, "binary": binary},
            "SHA-256 Browser Capture Input": {"json": {"input_sha256": capture_binary_sha256}},
        }
        result = self.run_exported_node("Check Existing Browser Artifact", existing, binary, idempotency_references)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["output"][0]["json"]["idempotency_action"], "NOOP")

        source_conflict = {**existing, "source_sha256": "d" * 64}
        result = self.run_exported_node("Check Existing Browser Artifact", source_conflict, binary, idempotency_references)
        self.assertEqual(result, {"ok": False, "error": "BROWSER_ARTIFACT_ID_HASH_CONFLICT"})

        binary_conflict = {**existing, "output_sha256": "c" * 64}
        result = self.run_exported_node("Check Existing Browser Artifact", binary_conflict, binary, idempotency_references)
        self.assertEqual(result, {"ok": False, "error": "BROWSER_ARTIFACT_ID_HASH_CONFLICT"})

    def test_browser_capture_pipeline_is_write_disabled_and_skips_pdf_and_cashback(self) -> None:
        workflow = self.workflow("03-shared-statement-pipeline.json")
        nodes = self.nodes("03-shared-statement-pipeline.json")
        connections = workflow["connections"]

        self.assertEqual(
            connections["Browser Capture?"]["main"][0][0]["node"],
            "Parse Browser Capture Adapter",
        )
        self.assertEqual(
            connections["Browser Capture Write?"]["main"][0][0]["node"],
            "Complete Browser Capture Headless Receipt",
        )
        self.assertFalse(any(node["type"] == "n8n-nodes-finance.actualBudget" for node in workflow["nodes"]))
        terminal = nodes["Complete Browser Capture Headless Receipt"]["parameters"]["jsCode"]
        self.assertIn("direct_actual_writer", terminal)
        self.assertIn("direct_cashback_writer", terminal)

    def test_shared_pipeline_binds_cashback_close_to_post_actual_receipt(self) -> None:
        workflow = self.workflow("03-shared-statement-pipeline.json")
        nodes = self.nodes("03-shared-statement-pipeline.json")
        self.assertEqual(
            workflow["connections"]["Apply Prepared Outbox Safely"]["main"][0][0]["node"],
            "Build Trusted Cashback Finalization",
        )
        self.assertEqual(
            workflow["connections"]["Build Trusted Cashback Finalization"]["main"][0][0]["node"],
            "Convert Trusted Actual Receipt to File",
        )
        self.assertEqual(
            workflow["connections"]["Convert Trusted Actual Receipt to File"]["main"][0][0]["node"],
            "SHA-256 Trusted Actual Receipt",
        )
        self.assertEqual(
            workflow["connections"]["SHA-256 Trusted Actual Receipt"]["main"][0][0]["node"],
            "Finalize Trusted Cashback Payload",
        )
        self.assertEqual(
            workflow["connections"]["Finalize Trusted Cashback Payload"]["main"][0][0]["node"],
            "Build Cashback Reconciliation Request",
        )
        self.assertEqual(
            workflow["connections"]["Build Cashback Reconciliation Request"]["main"][0][0]["node"],
            "Reconcile Cashback Statement",
        )
        self.assertEqual(
            workflow["connections"]["Reconcile Cashback Statement"]["main"][0][0]["node"],
            "Cashback Close Required",
        )
        self.assertEqual(
            workflow["connections"]["Finalize Eligible Cashback Period"]["main"][0][0]["node"],
            "Validate Cashback Finalization Response",
        )
        self.assertEqual(
            workflow["connections"]["Validate Cashback Finalization Response"]["main"][0][0]["node"],
            "Upsert Reconciliation Receipt",
        )
        self.assertEqual(
            nodes["Convert Trusted Actual Receipt to File"]["type"],
            "n8n-nodes-base.convertToFile",
        )
        self.assertEqual(
            nodes["SHA-256 Trusted Actual Receipt"]["type"],
            "n8n-nodes-base.crypto",
        )
        self.assertEqual(
            nodes["SHA-256 Trusted Actual Receipt"]["parameters"]["dataPropertyName"],
            "actual_import_receipt_sha256",
        )
        self.assertTrue(
            nodes["Reconcile Cashback Statement"]["parameters"]["url"].endswith("/api/reconcile")
        )
        self.assertIn(
            "Build Cashback Reconciliation Request",
            nodes["Reconcile Cashback Statement"]["parameters"]["jsonBody"],
        )
        self.assertIn("CASHBACK_FINALIZE_RESPONSE_BINDING_MISMATCH", nodes["Validate Cashback Finalization Response"]["parameters"]["jsCode"])
        self.assertIn("close_id", nodes["Upsert Reconciliation Receipt"]["parameters"]["columns"]["value"]["cashback_close_id"])
        body = nodes["Finalize Eligible Cashback Period"]["parameters"]["jsonBody"]
        self.assertIn("Finalize Trusted Cashback Payload", body)
        self.assertNotIn("cashback_finalization", nodes["Validate Statement Reconciliation and IDs"]["parameters"]["jsCode"])
        trusted_builder = nodes["Build Trusted Cashback Finalization"]["parameters"]["jsCode"]
        self.assertNotIn("source.card_code || source.account_id", trusted_builder)
        self.assertNotIn("actual.card_code || actual.account_id", trusted_builder)

        digest = "a" * 64
        source = {
            "source_code": "EI_AMAZON",
            "document_sha256": "b" * 64,
            "onedrive_item_id": "drive-statement-1",
            "actual_file_id": "actual-file-1",
            "account_id": "actual-account:EI_AMAZON",
            "card_code": "EI_AMAZON",
        }
        statement = {
            "statement_reference": "EI-2026-08",
            "statement_sha256": "c" * 64,
            "transactions": [{
                "transaction_id": "statement-transaction-1",
                "transaction_date": "2026-08-15",
                "description": "Synthetic Merchant",
                "amount_aed": "8.00",
                "currency_original": "AED",
                "transaction_type": "PURCHASE",
                "purchase_type": "GROCERY",
            }],
        }
        manifest = {"period_start": "2026-08-01", "period_end": "2026-08-31", "card_code": "EI_AMAZON"}
        actual = {
            "outbox_id": "outbox:ei-2026-08",
            "actual_file_id": "actual-file-1",
            "account_id": "actual-account:EI_AMAZON",
            "card_code": "EI_AMAZON",
            "state": "COMMITTED",
            "writer_release_verified": True,
            "verification_version": 1,
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
            "expected_payload_sha256": digest,
            "observed_payload_sha256": digest,
            "expected_count": 2,
            "observed_count": 2,
            "expected_amount_sum_minor": 800,
            "observed_amount_sum_minor": 800,
            "expected_account_balance": -800,
            "observed_account_balance": -800,
            "invariants_passed": True,
            "verified_at": "2026-08-20T00:00:00+00:00",
        }
        references = {
            "Verify Archive and Execution Context": {"json": source},
            "Validate Statement Reconciliation and IDs": {"json": statement},
            "Build Canonical Delta Artifact": {"json": {"manifest": manifest}},
        }
        result = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Build Trusted Cashback Finalization",
            actual,
            references,
        )
        self.assertTrue(result["ok"], result)
        close = result["output"][0]["json"]["cashback_finalization"]
        self.assertEqual(close["actual_import_receipt"]["state"], "COMMITTED")
        self.assertEqual(close["actual_import_receipt"]["account_id"], "actual-account:EI_AMAZON")
        self.assertEqual(close["actual_import_receipt"]["card_code"], "EI_AMAZON")
        self.assertEqual(close["actual_import_receipt"]["period_end"], "2026-08-31")
        self.assertEqual(close["actual_import_receipt"]["expected_payload_sha256"], digest)
        receipt_digest = hashlib.sha256(
            json.dumps(
                close["actual_import_receipt"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest()
        finalized = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Finalize Trusted Cashback Payload",
            {"actual_import_receipt_sha256": receipt_digest},
            {**references, "Build Trusted Cashback Finalization": result["output"][0]},
        )
        self.assertTrue(finalized["ok"], finalized)
        self.assertEqual(
            finalized["output"][0]["json"]["actual_import_receipt_sha256"],
            receipt_digest,
        )
        self.assertEqual(
            finalized["output"][0]["json"]["cashback_finalization"]["actual_import_receipt_sha256"],
            receipt_digest,
        )
        reconciled = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Build Cashback Reconciliation Request",
            finalized["output"][0]["json"],
            references,
        )
        self.assertTrue(reconciled["ok"], reconciled)
        reconcile_request = reconciled["output"][0]["json"]["cashback_reconcile"]
        self.assertEqual(reconcile_request["card_code"], "EI_AMAZON")
        self.assertEqual(reconcile_request["period_start"], "2026-08-01")
        self.assertEqual(
            reconcile_request["transactions"][0]["statement_transaction_id"],
            "statement-transaction-1",
        )
        self.assertEqual(reconcile_request["transactions"][0]["event_type"], "PURCHASE")
        self.assertEqual(reconcile_request["transactions"][0]["purchase_type"], "GROCERY")
        close_id = "cashback-close:EI_AMAZON:2026-08-01:2026-08-31"
        finalize_response = {
            "period": {
                "close_id": close_id,
                "card_code": "EI_AMAZON",
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
                "statement_reference": "EI-2026-08",
                "statement_sha256": "c" * 64,
                "actual_import_receipt_sha256": receipt_digest,
                "actual_verification_sha256": receipt_digest,
                "status": "FINALIZED",
                "idempotent_replay": False,
            }
        }
        validated_close = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Validate Cashback Finalization Response",
            finalize_response,
            {
                **references,
                "Build Cashback Reconciliation Request": reconciled["output"][0],
                "Finalize Trusted Cashback Payload": finalized["output"][0],
            },
        )
        self.assertTrue(validated_close["ok"], validated_close)
        self.assertEqual(validated_close["output"][0]["json"]["close_id"], close_id)
        for invalid_response in (
            {"period": {key: value for key, value in finalize_response["period"].items() if key != "close_id"}},
            {"period": {**finalize_response["period"], "status": "COMMITTED"}},
            {"period": {**finalize_response["period"], "actual_verification_sha256": "d" * 64}},
        ):
            rejected_response = self.run_exported_workflow_node(
                "03-shared-statement-pipeline.json",
                "Validate Cashback Finalization Response",
                invalid_response,
                {
                    **references,
                    "Build Cashback Reconciliation Request": reconciled["output"][0],
                    "Finalize Trusted Cashback Payload": finalized["output"][0],
                },
            )
            self.assertFalse(rejected_response["ok"])
        empty_statement_references = {
            **references,
            "Validate Statement Reconciliation and IDs": {"json": {**statement, "transactions": []}},
        }
        empty_reconcile = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Build Cashback Reconciliation Request",
            finalized["output"][0]["json"],
            empty_statement_references,
        )
        self.assertFalse(empty_reconcile["ok"])
        missing_card_references = {
            **references,
            "Verify Archive and Execution Context": {"json": {key: value for key, value in source.items() if key != "card_code"}},
        }
        missing_card_reconcile = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Build Cashback Reconciliation Request",
            finalized["output"][0]["json"],
            missing_card_references,
        )
        self.assertFalse(missing_card_reconcile["ok"])
        replay = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Build Trusted Cashback Finalization",
            actual,
            references,
        )
        replay_finalized = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Finalize Trusted Cashback Payload",
            {"actual_import_receipt_sha256": receipt_digest},
            {**references, "Build Trusted Cashback Finalization": replay["output"][0]},
        )
        self.assertEqual(
            replay_finalized["output"][0]["json"]["actual_import_receipt_sha256"],
            finalized["output"][0]["json"]["actual_import_receipt_sha256"],
        )

        for label, invalid in {
            "missing": {key: value for key, value in actual.items() if key != "observed_payload_sha256"},
            "stale": {**actual, "state": "ACTUAL_OBSERVED"},
            "cross-account": {**actual, "account_id": "RAK_WORLD"},
            "missing-card": {key: value for key, value in actual.items() if key != "card_code"},
            "cross-card": {**actual, "card_code": "RAK_WORLD"},
            "cross-period": {**actual, "period_end": "2026-09-01"},
            "digest-mismatch": {**actual, "observed_payload_sha256": "d" * 64},
        }.items():
            rejected = self.run_exported_workflow_node(
                "03-shared-statement-pipeline.json",
                "Build Trusted Cashback Finalization",
                invalid,
                references,
            )
            self.assertFalse(rejected["ok"], label)

    def test_reconciliation_readback_rejects_stale_version_close_and_digest(self) -> None:
        nodes = self.nodes("03-shared-statement-pipeline.json")
        self.assertIn(
            "RECONCILIATION_READBACK_BINDING_MISMATCH",
            nodes["Validate Reconciliation Readback"]["parameters"]["jsCode"],
        )
        source = {
            "source_code": "EI_AMAZON",
            "period_key": "2026-08",
            "cashback_close_required": True,
        }
        request = {"statement_sha256": "a" * 64}
        actual = {"observed_payload_sha256": "b" * 64}
        row = {
            "source_code": "EI_AMAZON",
            "period_key": "2026-08",
            "reconciliation_version": 1,
            "statement_sha256": "a" * 64,
            "actual_verification_sha256": "b" * 64,
            "cashback_close_id": "cashback-close:EI_AMAZON:2026-08-01:2026-08-31",
            "state": "COMMITTED",
        }
        refs = {
            "Verify Archive and Execution Context": {"json": source},
            "Apply Prepared Outbox Safely": {"json": actual},
            "Build Cashback Reconciliation Request": {"json": {"cashback_reconcile": request}},
            "Validate Cashback Finalization Response": {"json": {"close_id": row["cashback_close_id"]}},
        }
        valid = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Validate Reconciliation Readback",
            row,
            refs,
        )
        self.assertTrue(valid["ok"], valid)
        for invalid in (
            {**row, "reconciliation_version": 2},
            {**row, "cashback_close_id": "cashback-close:RAK_WORLD:2026-08-01:2026-08-31"},
            {**row, "actual_verification_sha256": "c" * 64},
            {**row, "statement_sha256": "d" * 64},
        ):
            rejected = self.run_exported_workflow_node(
                "03-shared-statement-pipeline.json",
                "Validate Reconciliation Readback",
                invalid,
                refs,
            )
            self.assertFalse(rejected["ok"])

    def test_subscription_adapter_uses_pinned_community_nodes_and_server_owned_controls(self) -> None:
        lock = load_json(N8N / "community-node-lock.json")
        self.assertEqual(
            {(row["package"], row["version"]) for row in lock["packages"]},
            {
                ("n8n-nodes-prodex", "0.5.1"),
                ("@ggomez91npm/n8n-nodes-claude-code", "0.8.0"),
            },
        )
        nodes = self.nodes("21-subscription-agent-adapter.json")
        codex = nodes["Run Codex Subscription Provider"]
        self.assertEqual((codex["type"], codex["typeVersion"]), ("n8n-nodes-prodex.prodex", 2))
        self.assertEqual(
            set(codex["parameters"]),
            {
                "operation", "useN8nCredentials", "systemPrompt", "skills", "prompt",
                "model", "reasoningEffort", "personality", "threadMode", "sandbox",
                "workingDirectory", "options",
            },
        )
        self.assertEqual(codex["parameters"]["sandbox"], "read_only")
        self.assertEqual(codex["parameters"]["threadMode"], "new")
        self.assertEqual(codex["parameters"]["workingDirectory"], "/tmp/finance-ai")
        self.assertEqual(
            set(codex["parameters"]["options"]),
            {"outputSchema", "streamProgress", "timeoutSeconds"},
        )
        self.assertTrue(codex["parameters"]["options"]["outputSchema"])
        claude = nodes["Run Claude Subscription Provider"]
        self.assertEqual(
            (claude["type"], claude["typeVersion"]),
            ("@ggomez91npm/n8n-nodes-claude-code.claude", 1),
        )
        self.assertEqual(claude["parameters"]["responseFormat"], "json")
        self.assertFalse(claude["parameters"]["options"]["useCache"])
        self.assertEqual(claude["parameters"]["model"], "={{ $json.provider_model }}")
        proposal_schema = load_json(N8N / "contracts" / "ai-proposal-v1.schema.json")
        self.assertEqual(
            json.loads(codex["parameters"]["options"]["outputSchema"]),
            proposal_schema,
        )
        assignments = {
            row["name"]: row["value"]
            for row in nodes["Subscription Provider Parameters"]["parameters"]["assignments"]["assignments"]
        }
        self.assertEqual(json.loads(assignments["proposal_output_schema"]), proposal_schema)
        build = nodes["Validate and Build Fixed Provider Invocation"]["parameters"]["jsCode"]
        for forbidden in ("command", "working_directory", "sandbox", "prompt"):
            self.assertIn(forbidden, build)
        for expected in (
            "CODEX_SUBSCRIPTION", "CLAUDE_SUBSCRIPTION", "provider_model",
            "provider_reasoning_effort", "provider_auth_mode", "Output JSON Schema",
        ):
            self.assertIn(expected, build)
        validator_name = "Validate Claude Proposal Schema and Normalize Provider Output"
        normalizer = nodes[validator_name]["parameters"]["jsCode"]
        self.assertIn("FINANCE_AI_SCHEMA_V1", normalizer)
        claude_targets = self.workflow("21-subscription-agent-adapter.json")["connections"][
            "Run Claude Subscription Provider"
        ]["main"]
        self.assertEqual(claude_targets[0][0]["node"], validator_name)
        for expected in (
            "runner_model: invocation.provider_model",
            "runner_reasoning_effort: invocation.provider_reasoning_effort",
            "auth_mode: invocation.provider_auth_mode",
        ):
            self.assertIn(expected, normalizer)
        adapter_json = json.dumps(self.workflow("21-subscription-agent-adapter.json"))
        self.assertNotIn("CLAUDE_SUBSCRIPTION_RUNNER_NOT_ACTIVATED", adapter_json)
        self.assertEqual(
            self.workflow("21-subscription-agent-adapter.json")["meta"]["providerBranchesEnabled"],
            ["CODEX_SUBSCRIPTION", "CLAUDE_SUBSCRIPTION"],
        )
        route = self.workflow("21-subscription-agent-adapter.json")["connections"]["Provider Route"]["main"]
        self.assertEqual(
            [branch[0]["node"] for branch in route[:2]],
            ["Run Codex Subscription Provider", "Run Claude Subscription Provider"],
        )
        self.assertIn("gpt-5.6-luna", json.dumps(nodes["Subscription Provider Parameters"]))
        self.assertIn("gpt-5.6-sol", json.dumps(nodes["Subscription Provider Parameters"]))

    def test_custom_node_registry_uses_exact_full_types_and_versions(self) -> None:
        contract = self.registry["custom_nodes"]
        used = {
            node["type"]: node["typeVersion"]
            for workflow in self.workflows.values()
            for node in workflow["nodes"]
            if node["type"].startswith("n8n-nodes-finance.")
        }
        self.assertEqual(set(used), set(contract["full_node_types"].values()))
        self.assertEqual(used, contract["type_versions"])

    def test_readme_does_not_claim_exports_are_executable(self) -> None:
        readme = (N8N / "README.md").read_text(encoding="utf-8")
        self.assertIn("SPEC_ONLY", readme)
        self.assertIn("have not yet passed exact-image import", readme)
        self.assertIn("API-key fallback is forbidden", readme)
        self.assertIn("native n8n OpenAI credential is not used", readme)


if __name__ == "__main__":
    unittest.main()
