from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
N8N = ROOT / "integrations" / "n8n"
WORKFLOWS = N8N / "workflows"
ERROR_WORKFLOW_ID = "10000000-0000-4000-8000-000000000016"
SUBWORKFLOW_TYPES = {
    "n8n-nodes-base.executeWorkflow",
    "@n8n/n8n-nodes-langchain.toolWorkflow",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# The names are intentionally codes from pipeline-registry.json, while the
# IDs are resolved from the exports. This catches an ID copied from another
# n8n environment and a stale cachedResultName independently.
EXPECTED_CALL_TARGETS: dict[str, tuple[tuple[str, str], ...]] = {
    "RAKBANK_LIVE_CASHBACK": (
        ("Sweep Exact Outlook Messages", "OUTLOOK_MESSAGE_SWEEP"),
    ),
    "OUTLOOK_MESSAGE_SWEEP": (
        ("Archive Enumerated Messages in W01", "OUTLOOK_FINANCE_ACQUISITION"),
    ),
    "SHARED_STATEMENT_PIPELINE": (
        ("Request Scoped AI Proposals", "AI_PROPOSAL"),
        ("Run Isolated PDF Extraction", "LOCAL_PDF_EXTRACTION"),
        ("Apply Prepared Outbox Safely", "ACTUAL_OUTBOX_APPLY"),
    ),
    "EI_MONTHLY_STATEMENT": (
        ("Acquire Archive and Read Back", "OUTLOOK_MESSAGE_SWEEP"),
        ("Initialize Source Cursor via W12", "OUTLOOK_MESSAGE_SWEEP"),
        ("Run Shared Statement Pipeline", "SHARED_STATEMENT_PIPELINE"),
        ("Commit Source Cursor via W12", "OUTLOOK_MESSAGE_SWEEP"),
    ),
    "WIO_MONTHLY_STATEMENT": (
        ("Acquire Archive and Read Back", "OUTLOOK_MESSAGE_SWEEP"),
        ("Initialize Source Cursor via W12", "OUTLOOK_MESSAGE_SWEEP"),
        ("Run Shared Statement Pipeline", "SHARED_STATEMENT_PIPELINE"),
        ("Commit Source Cursor via W12", "OUTLOOK_MESSAGE_SWEEP"),
    ),
    "AI_PROPOSAL": (
        ("Invoke Subscription Agent Adapter", "SUBSCRIPTION_AGENT_ADAPTER"),
    ),
    "FINANCE_OPERATIONS_STATUS": (
        ("Dispatch Reviewed Artifact", "INTERACTIVE_ARTIFACT_HANDOFF"),
        ("Dispatch Document Request", "DOCUMENT_EXTRACTION_REQUEST"),
    ),
    "INTERACTIVE_ARTIFACT_HANDOFF": (
        ("Dispatch Browser Capture to Headless Pipeline", "SHARED_STATEMENT_PIPELINE"),
    ),
    "DOCUMENT_EXTRACTION_REQUEST": (
        ("Run Local Extraction Ladder", "LOCAL_PDF_EXTRACTION"),
    ),
    "FINANCE_MCP_FACADE": (
        ("finance.status.v1", "FINANCE_OPERATIONS_STATUS"),
        ("finance.reviewed-artifact.handoff.v1", "FINANCE_OPERATIONS_STATUS"),
        ("finance.document.request.v1", "FINANCE_OPERATIONS_STATUS"),
    ),
    "ACTUAL_OUTBOX_RECOVERY": (
        ("Apply Nonterminal Outbox Safely", "ACTUAL_OUTBOX_APPLY"),
    ),
    "ACTUAL_OUTBOX_APPLY": (
        ("Acquire Recovery Writer Fence", "FINANCE_WRITER_LEASE"),
        ("Assert Recovery Fence Before Import", "FINANCE_WRITER_LEASE"),
        ("Release Recovery Writer Fence", "FINANCE_WRITER_LEASE"),
    ),
}


def boundary_case(
    name: str,
    edge: tuple[str, str],
    target: str,
    producer_fields: tuple[str, ...],
    consumer_fields: tuple[str, ...],
    aliases: tuple[tuple[str, str], ...] = (),
    adapters: dict[str, str] | None = None,
) -> dict:
    return {
        "name": name,
        "edge": edge,
        "edges": (edge, *aliases),
        "target": target,
        "producer_fields": producer_fields,
        "consumer_fields": consumer_fields,
        "adapters": adapters or {},
    }


ACQUISITION_CONTEXT = (
    "run_id",
    "source_code",
    "senders",
    "subjects",
    "window_start",
    "run_upper_bound",
    "onedrive_item_id",
)
MONTHLY_SWEEP_REQUEST = (
    "run_id",
    "source_code",
    "folder_id",
    "senders",
    "subjects",
    "window_start",
    "run_upper_bound",
    "onedrive_parent_id",
)
STATEMENT_CONTEXT = (
    "run_id",
    "source_code",
    "onedrive_item_id",
    "config_version",
    "period_key",
    "trigger_kind",
)
DOCUMENT_REQUEST = (
    "document_id",
    "expected_sha256",
    "document_profile",
    "requested_schema_version",
)
EXTRACTION_DOCUMENT = ("document_id", "source_sha256", "document_profile")
OUTBOX_CONTEXT = (
    "outbox_id",
    "payload_sha256",
    "artifact_item_id",
    "artifact_schema_version",
    "actual_file_id",
    "config_version",
    "state",
    "attempt_count",
    "account_id",
)
LEASE_ACQUIRE = ("operation", "resource_key", "lease_owner", "ttl_seconds")
LEASE_ASSERT = ("operation", "resource_key", "lease_id", "fencing_token")
SWEEP_CONTEXT = (
    "run_id",
    "source_code",
    "folder_id",
    "senders",
    "subjects",
    "window_start",
    "run_upper_bound",
    "scanned_count",
    "matched_count",
    "pagination_exhausted",
    "heartbeat",
    "messages",
)
CURSOR_INITIALIZATION_CONTEXT = (
    "operation",
    "run_id",
    "source_code",
    "config_version",
    "initial_cursor_value",
    "initial_cursor_source",
    "overlap_seconds",
)
CURSOR_COMMIT_CONTEXT = (
    "operation",
    "run_id",
    "source_code",
    "window_start",
    "run_upper_bound",
    "expected_cursor_version",
    "downstream_receipt_sha256",
    "pagination_exhausted",
    "cursor_commit_eligible",
    "attachment_verification_barrier",
    "email_evidence_receipt_barrier",
    "archive_ready",
    "receipt_readback_verified",
)

# Every direct executeWorkflow edge is represented once. EI and WIO share
# reviewed contracts, so aliases keep the fixture list small without hiding
# either caller from the observed topology check.
BOUNDARY_FIXTURES: tuple[dict, ...] = (
    boundary_case(
        "monthly acquisition request",
        ("EI_MONTHLY_STATEMENT", "Acquire Archive and Read Back"),
        "OUTLOOK_MESSAGE_SWEEP",
        MONTHLY_SWEEP_REQUEST,
        MONTHLY_SWEEP_REQUEST,
        (("WIO_MONTHLY_STATEMENT", "Acquire Archive and Read Back"),),
    ),
    boundary_case(
        "versioned source cursor initialization",
        ("EI_MONTHLY_STATEMENT", "Initialize Source Cursor via W12"),
        "OUTLOOK_MESSAGE_SWEEP",
        CURSOR_INITIALIZATION_CONTEXT,
        CURSOR_INITIALIZATION_CONTEXT,
        (("WIO_MONTHLY_STATEMENT", "Initialize Source Cursor via W12"),),
    ),
    boundary_case(
        "statement pipeline input",
        ("EI_MONTHLY_STATEMENT", "Run Shared Statement Pipeline"),
        "SHARED_STATEMENT_PIPELINE",
        STATEMENT_CONTEXT,
        STATEMENT_CONTEXT,
        (("WIO_MONTHLY_STATEMENT", "Run Shared Statement Pipeline"),),
    ),
    boundary_case(
        "durable source cursor commit",
        ("EI_MONTHLY_STATEMENT", "Commit Source Cursor via W12"),
        "OUTLOOK_MESSAGE_SWEEP",
        CURSOR_COMMIT_CONTEXT,
        CURSOR_COMMIT_CONTEXT,
        (("WIO_MONTHLY_STATEMENT", "Commit Source Cursor via W12"),),
    ),
    boundary_case(
        "statement pipeline to AI proposal",
        ("SHARED_STATEMENT_PIPELINE", "Request Scoped AI Proposals"),
        "AI_PROPOSAL",
        ("policy_id", "unresolved", "proposals"),
        ("policy_id", "unresolved"),
    ),
    boundary_case(
        "statement pipeline to PDF extraction",
        ("SHARED_STATEMENT_PIPELINE", "Run Isolated PDF Extraction"),
        "LOCAL_PDF_EXTRACTION",
        ("id", "readback_sha256"),
        ("document_id", "source_sha256"),
        adapters={
            "document_id": "id",
            "source_sha256": "readback_sha256",
        },
    ),
    boundary_case(
        "statement pipeline to Actual outbox apply",
        ("SHARED_STATEMENT_PIPELINE", "Apply Prepared Outbox Safely"),
        "ACTUAL_OUTBOX_APPLY",
        OUTBOX_CONTEXT,
        OUTBOX_CONTEXT,
    ),
    boundary_case(
        "AI proposal to subscription adapter",
        ("AI_PROPOSAL", "Invoke Subscription Agent Adapter"),
        "SUBSCRIPTION_AGENT_ADAPTER",
        (
            "schema_version",
            "job_id",
            "idempotency_key",
            "agent_provider",
            "policy_id",
            "policy_class",
            "policy_sha256",
            "config_sha256",
            "output_schema_sha256",
            "unresolved",
            "proposals",
        ),
        (
            "schema_version",
            "job_id",
            "idempotency_key",
            "agent_provider",
            "policy_id",
            "policy_class",
            "policy_sha256",
            "config_sha256",
            "output_schema_sha256",
            "unresolved",
        ),
    ),
    boundary_case(
        "operations status to artifact handoff",
        ("FINANCE_OPERATIONS_STATUS", "Dispatch Reviewed Artifact"),
        "INTERACTIVE_ARTIFACT_HANDOFF",
        ("artifact_id", "expected_sha256"),
        ("artifact_id", "expected_sha256"),
    ),
    boundary_case(
        "operations status to document request",
        ("FINANCE_OPERATIONS_STATUS", "Dispatch Document Request"),
        "DOCUMENT_EXTRACTION_REQUEST",
        DOCUMENT_REQUEST,
        DOCUMENT_REQUEST,
    ),
    boundary_case(
        "artifact handoff to statement pipeline",
        ("INTERACTIVE_ARTIFACT_HANDOFF", "Dispatch Browser Capture to Headless Pipeline"),
        "SHARED_STATEMENT_PIPELINE",
        (
            "run_id",
            "source_code",
            "message_id",
            "attachment_id",
            "document_sha256",
            "onedrive_item_id",
            "config_version",
            "actual_file_id",
            "account_id",
            "period_key",
            "trigger_kind",
        ),
        (
            "run_id",
            "source_code",
            "message_id",
            "attachment_id",
            "document_sha256",
            "onedrive_item_id",
            "config_version",
            "actual_file_id",
            "account_id",
            "period_key",
            "trigger_kind",
        ),
    ),
    boundary_case(
        "document request to local extraction",
        ("DOCUMENT_EXTRACTION_REQUEST", "Run Local Extraction Ladder"),
        "LOCAL_PDF_EXTRACTION",
        EXTRACTION_DOCUMENT,
        EXTRACTION_DOCUMENT,
    ),
    boundary_case(
        "status facade",
        ("FINANCE_MCP_FACADE", "finance.status.v1"),
        "FINANCE_OPERATIONS_STATUS",
        ("_mcp_request_id", "operation_code"),
        ("_mcp_request_id", "operation_code"),
    ),
    boundary_case(
        "artifact facade",
        ("FINANCE_MCP_FACADE", "finance.reviewed-artifact.handoff.v1"),
        "FINANCE_OPERATIONS_STATUS",
        ("_mcp_request_id", "operation_code", "artifact_id", "expected_sha256"),
        ("_mcp_request_id", "operation_code", "artifact_id", "expected_sha256"),
    ),
    boundary_case(
        "document facade",
        ("FINANCE_MCP_FACADE", "finance.document.request.v1"),
        "FINANCE_OPERATIONS_STATUS",
        ("_mcp_request_id", "operation_code", *DOCUMENT_REQUEST),
        ("_mcp_request_id", "operation_code", *DOCUMENT_REQUEST),
    ),
    boundary_case(
        "outbox recovery",
        ("ACTUAL_OUTBOX_RECOVERY", "Apply Nonterminal Outbox Safely"),
        "ACTUAL_OUTBOX_APPLY",
        ("state",),
        ("state",),
    ),
    boundary_case(
        "writer lease acquire",
        ("ACTUAL_OUTBOX_APPLY", "Acquire Recovery Writer Fence"),
        "FINANCE_WRITER_LEASE",
        LEASE_ACQUIRE,
        LEASE_ACQUIRE,
    ),
    boundary_case(
        "writer lease assert",
        ("ACTUAL_OUTBOX_APPLY", "Assert Recovery Fence Before Import"),
        "FINANCE_WRITER_LEASE",
        LEASE_ASSERT,
        LEASE_ASSERT,
    ),
    boundary_case(
        "writer lease release",
        ("ACTUAL_OUTBOX_APPLY", "Release Recovery Writer Fence"),
        "FINANCE_WRITER_LEASE",
        LEASE_ASSERT,
        LEASE_ASSERT,
    ),
    boundary_case(
        "cashback sweep",
        ("RAKBANK_LIVE_CASHBACK", "Sweep Exact Outlook Messages"),
        "OUTLOOK_MESSAGE_SWEEP",
        SWEEP_CONTEXT,
        SWEEP_CONTEXT,
    ),
    boundary_case(
        "message sweep to acquisition",
        ("OUTLOOK_MESSAGE_SWEEP", "Archive Enumerated Messages in W01"),
        "OUTLOOK_FINANCE_ACQUISITION",
        SWEEP_CONTEXT,
        tuple(field for field in ACQUISITION_CONTEXT if field != "onedrive_item_id"),
    ),
)

ERROR_INPUT_FIELDS = ("execution", "workflow")
ERROR_OUTPUT_FIELDS = (
    "execution_id",
    "workflow_id",
    "workflow_name",
    "workflow_code",
    "run_id",
    "provider_code",
    "error_class",
    "error_message_redacted",
    "first_seen_at",
    "readback_verified",
)


def synthetic_fixture(fields: tuple[str, ...]) -> dict:
    """Make shape-only values without repeating provider-looking fixtures."""
    values = {}
    for field in fields:
        if field in {"senders", "subjects", "messages", "proposals", "unresolved"}:
            values[field] = []
        elif (
            field.startswith("is_")
            or field.endswith("_verified")
            or field
            in {
                "heartbeat",
                "pagination_exhausted",
                "cursor_commit_eligible",
            }
        ):
            values[field] = True
        elif field.endswith("_count") or field in {"attempt_count", "current_balance"}:
            values[field] = 1
        elif field.endswith("sha256") or field == "expected_sha256":
            values[field] = "a" * 64
        elif field == "operation":
            values[field] = "ACQUIRE"
        elif field == "operation_code":
            values[field] = "fixture.operation"
        else:
            values[field] = f"fixture-{field}"
    return values


def assert_directional_compatibility(
    producer_fields: tuple[str, ...],
    consumer_fields: tuple[str, ...],
    adapters: dict[str, str] | None = None,
) -> None:
    """Require every consumer field from the producer or an explicit adapter."""
    adapters = adapters or {}
    unknown_adapters = set(adapters) - set(consumer_fields)
    if unknown_adapters:
        raise AssertionError(
            f"adapter keys are not consumer fields: {unknown_adapters}"
        )
    missing = {
        field
        for field in consumer_fields
        if field not in producer_fields and field not in adapters
    }
    if missing:
        raise AssertionError(f"consumer fields missing from producer output: {missing}")
    for consumer_field, producer_field in adapters.items():
        if not producer_field:
            raise AssertionError(f"adapter for {consumer_field} has no producer source")
        if producer_field not in producer_fields:
            raise AssertionError(
                f"adapter for {consumer_field} references undeclared producer field "
                f"{producer_field}"
            )


ATTACHMENT_ALIAS_NEGATIVE_FIXTURE = {
    "producer_fields": ("source_attachment_id",),
    "consumer_fields": ("attachment_id",),
}

ATTACHMENT_ALIAS_CONTEXT = {
    "run_id": "fixture-run",
    "source_code": "FIXTURE_SOURCE",
    "message_id": "fixture-message",
    "document_sha256": "a" * 64,
    "onedrive_item_id": "fixture-item",
    "manifest_onedrive_parent_id": "fixture-parent",
    "config_version": "fixture-config",
    "actual_file_id": "fixture-actual-file",
    "account_id": "fixture-account",
    "card_code": "FIXTURE_CARD",
    "cashback_close_required": False,
    "period_key": "2026-08",
    "trigger_kind": "SUBWORKFLOW",
}

ATTACHMENT_ALIAS_POSITIVE_FIXTURES = (
    {"source_attachment_id": "source-attachment-001"},
    {
        "source_attachment_id": "source-attachment-002",
        "attachment_id": "source-attachment-002",
    },
)
ATTACHMENT_ALIAS_NEGATIVE_FIXTURES = (
    ({}, "Missing trusted immutable field source_attachment_id"),
    (
        {"attachment_id": "attachment-only-003"},
        "Missing trusted immutable field source_attachment_id",
    ),
    (
        {
            "source_attachment_id": "source-attachment-003",
            "attachment_id": "different-attachment-003",
        },
        "ATTACHMENT_ID_ALIAS_MISMATCH",
    ),
)


def execute_js_code(js_code: str, payload: dict) -> dict:
    """Execute one n8n Code-node body with the context used by this contract."""
    runner = r'''
const payload = JSON.parse(require('fs').readFileSync(0, 'utf8'));
try {
  const output = new Function('$json', '$binary', payload.js_code)(payload.json, payload.binary || {});
  process.stdout.write(JSON.stringify({ok: true, output}));
} catch (error) {
  process.stdout.write(JSON.stringify({ok: false, error: String(error.message || error)}));
}
'''
    completed = subprocess.run(
        ["node", "-e", runner],
        input=json.dumps({"js_code": js_code, **payload}),
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout)


class N8nInterfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_json(N8N / "pipeline-registry.json")
        cls.workflows = {
            path.name: load_json(path) for path in sorted(WORKFLOWS.glob("*.json"))
        }
        cls.by_code = {
            workflow["meta"]["financeWorkflowCode"]: workflow
            for workflow in cls.workflows.values()
        }
        cls.registry_by_code = {row["code"]: row for row in cls.registry["workflows"]}

    def workflow_for_code(self, code: str) -> dict:
        return self.by_code[code]

    def test_every_execute_workflow_and_error_target_maps_to_registry_code(
        self,
    ) -> None:
        registry_codes = set(self.registry_by_code)
        actual_codes = set(self.by_code)
        self.assertEqual(actual_codes, registry_codes)
        self.assertEqual(len(actual_codes), 21)

        ids = {workflow["id"]: workflow for workflow in self.workflows.values()}
        for filename, workflow in self.workflows.items():
            caller_code = workflow["meta"]["financeWorkflowCode"]
            expected = EXPECTED_CALL_TARGETS.get(caller_code, ())
            actual = []
            for node in workflow["nodes"]:
                if node["type"] not in SUBWORKFLOW_TYPES:
                    continue
                reference = node.get("parameters", {}).get("workflowId")
                self.assertIsInstance(reference, dict, f"{filename}::{node['name']}")
                target_id = reference.get("value")
                self.assertIn(target_id, ids, f"{filename}::{node['name']}")
                target = ids[target_id]
                target_code = target["meta"]["financeWorkflowCode"]
                self.assertIn(target_code, registry_codes)
                self.assertEqual(
                    reference.get("mode"), "list", f"{filename}::{node['name']}"
                )
                self.assertEqual(
                    reference.get("cachedResultName"),
                    target["name"],
                    f"{filename}::{node['name']}",
                )
                actual.append((node["name"], target_code))
            self.assertEqual(tuple(actual), expected, filename)

            configured_error = workflow.get("settings", {}).get("errorWorkflow")
            if caller_code == "OPERATIONS_ERROR_HANDLER":
                self.assertIsNone(configured_error, filename)
            else:
                self.assertEqual(configured_error, ERROR_WORKFLOW_ID, filename)
                self.assertEqual(
                    ids[configured_error]["meta"]["financeWorkflowCode"],
                    "OPERATIONS_ERROR_HANDLER",
                    filename,
                )

    def test_boundary_fixtures_cover_directional_contracts(self) -> None:
        by_edge = {
            edge: fixture for fixture in BOUNDARY_FIXTURES for edge in fixture["edges"]
        }
        observed_edges = {
            (caller_code, node["name"]): self._target_code(node)
            for caller_code, workflow in self.by_code.items()
            for node in workflow["nodes"]
            if node["type"] in SUBWORKFLOW_TYPES
        }
        self.assertEqual(
            set(by_edge),
            set(observed_edges),
            "every executeWorkflow edge needs one explicit boundary fixture",
        )

        for edge, fixture in by_edge.items():
            caller_code, _node_name = edge
            producer_document = self._document(caller_code)
            consumer_document = self._document(fixture["target"])
            with self.subTest(boundary=fixture["name"], edge=edge):
                self.assertEqual(observed_edges[edge], fixture["target"])
                assert_directional_compatibility(
                    fixture["producer_fields"],
                    fixture["consumer_fields"],
                    fixture["adapters"],
                )
                producer_fixture = synthetic_fixture(fixture["producer_fields"])
                consumer_fixture = synthetic_fixture(fixture["consumer_fields"])
                self.assertEqual(set(producer_fixture), set(fixture["producer_fields"]))
                self.assertEqual(set(consumer_fixture), set(fixture["consumer_fields"]))
                for field in fixture["producer_fields"]:
                    self.assertRegex(
                        producer_document,
                        rf"(?<![A-Za-z0-9_]){re.escape(field)}(?![A-Za-z0-9_])",
                        f"{fixture['name']} producer field {field} missing from {caller_code}",
                    )
                for field in fixture["consumer_fields"]:
                    self.assertRegex(
                        consumer_document,
                        rf"(?<![A-Za-z0-9_]){re.escape(field)}(?![A-Za-z0-9_])",
                        f"{fixture['name']} consumer field {field} missing from {fixture['target']}",
                    )

    def test_attachment_alias_requires_explicit_adapter(self) -> None:
        producer = self._document("OUTLOOK_FINANCE_ACQUISITION")
        consumer = self._document("SHARED_STATEMENT_PIPELINE")
        self.assertRegex(
            producer, r"(?<![A-Za-z0-9_])source_attachment_id(?![A-Za-z0-9_])"
        )
        self.assertRegex(consumer, r"(?<![A-Za-z0-9_])attachment_id(?![A-Za-z0-9_])")
        self.assertIn("ATTACHMENT_ID_ALIAS_MISMATCH", consumer)
        self.assertIn("source_attachment_id: sourceAttachmentId", consumer)
        with self.assertRaisesRegex(AssertionError, "attachment_id"):
            assert_directional_compatibility(
                ATTACHMENT_ALIAS_NEGATIVE_FIXTURE["producer_fields"],
                ATTACHMENT_ALIAS_NEGATIVE_FIXTURE["consumer_fields"],
            )
        assert_directional_compatibility(
            ATTACHMENT_ALIAS_NEGATIVE_FIXTURE["producer_fields"],
            ATTACHMENT_ALIAS_NEGATIVE_FIXTURE["consumer_fields"],
            {"attachment_id": "source_attachment_id"},
        )

    def test_attachment_alias_adapter_executes_positive_and_negative_fixtures(
        self,
    ) -> None:
        consumer = self.workflow_for_code("SHARED_STATEMENT_PIPELINE")
        verify_code = next(
            node["parameters"]["jsCode"]
            for node in consumer["nodes"]
            if node["name"] == "Verify Archive and Execution Context"
        )

        for aliases in ATTACHMENT_ALIAS_POSITIVE_FIXTURES:
            with self.subTest(case=aliases):
                result = execute_js_code(
                    verify_code,
                    {"json": {**ATTACHMENT_ALIAS_CONTEXT, **aliases}},
                )
                self.assertTrue(result["ok"], result)
                output = result["output"][0]["json"]
                expected_source_id = aliases["source_attachment_id"]
                self.assertEqual(output["source_attachment_id"], expected_source_id)
                self.assertEqual(output["attachment_id"], expected_source_id)

        for aliases, expected_error in ATTACHMENT_ALIAS_NEGATIVE_FIXTURES:
            with self.subTest(case=aliases):
                result = execute_js_code(
                    verify_code,
                    {"json": {**ATTACHMENT_ALIAS_CONTEXT, **aliases}},
                )
                self.assertFalse(result["ok"], result)
                self.assertIn(expected_error, result["error"])

    def test_outlook_source_attachment_mapping_is_immutable(self) -> None:
        producer = self.workflow_for_code("OUTLOOK_FINANCE_ACQUISITION")
        expand_code = next(
            node["parameters"]["jsCode"]
            for node in producer["nodes"]
            if node["name"] == "Expand Enumerated Attachment Items"
        )
        result = execute_js_code(
            expand_code,
            {
                "json": {
                    "message_id": "source-message-001",
                    "source_code": "OUTLOOK_FIXTURE",
                    "onedrive_parent_id": "fixture-parent",
                    "attachment_inventory": [
                        {"id": "source-attachment-001", "name": "statement.pdf"}
                    ],
                }
            },
        )
        self.assertTrue(result["ok"], result)
        output = result["output"][0]["json"]
        self.assertEqual(output["source_attachment_id"], "source-attachment-001")
        self.assertEqual(output["attachment_id"], "source-attachment-001")

    def test_error_and_lease_boundaries_have_redacted_fixed_shapes(self) -> None:
        error = self.workflow_for_code("OPERATIONS_ERROR_HANDLER")
        error_text = json.dumps(error, ensure_ascii=True).replace('"', '"')
        for field in (*ERROR_INPUT_FIELDS, *ERROR_OUTPUT_FIELDS):
            self.assertRegex(
                error_text,
                rf"(?<![A-Za-z0-9_]){re.escape(field)}(?![A-Za-z0-9_])",
            )
        error_code = next(
            node["parameters"]["jsCode"]
            for node in error["nodes"]
            if node["name"] == "Redact and Classify Failure"
        )
        self.assertIn("[REDACTED]", error_code)
        self.assertIn("readback_verified: false", error_code)

        lease = self.workflow_for_code("FINANCE_WRITER_LEASE")
        lease_text = json.dumps(lease, ensure_ascii=True).replace('"', '"')
        for field in (
            "operation",
            "resource_key",
            "lease_owner",
            "ttl_seconds",
            "lease_id",
            "fencing_token",
            "expires_at",
            "valid",
            "released",
        ):
            self.assertRegex(
                lease_text,
                rf"(?<![A-Za-z0-9_]){re.escape(field)}(?![A-Za-z0-9_])",
            )
        lease_code = next(
            node["parameters"]["jsCode"]
            for node in lease["nodes"]
            if node["name"] == "Validate Fixed Lease Operation"
        )
        for operation in ("ACQUIRE", "ASSERT", "RELEASE"):
            self.assertIn(operation, lease_code)

    def _document(self, code: str) -> str:
        return json.dumps(self.workflow_for_code(code), ensure_ascii=True).replace(
            '"', '"'
        )

    def _target_code(self, node: dict) -> str:
        target_id = node["parameters"]["workflowId"]["value"]
        target = next(
            workflow
            for workflow in self.workflows.values()
            if workflow["id"] == target_id
        )
        return target["meta"]["financeWorkflowCode"]


if __name__ == "__main__":
    unittest.main()
