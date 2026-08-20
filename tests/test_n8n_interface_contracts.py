from __future__ import annotations

import json
import re
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
    "SHARED_STATEMENT_PIPELINE": (
        ("Request Scoped AI Proposals", "AI_PROPOSAL"),
        ("Run Isolated PDF Extraction", "LOCAL_PDF_EXTRACTION"),
        ("Apply Prepared Outbox Safely", "ACTUAL_OUTBOX_APPLY"),
    ),
    "EI_MONTHLY_STATEMENT": (
        ("Acquire Archive and Read Back", "OUTLOOK_FINANCE_ACQUISITION"),
        ("Run Shared Statement Pipeline", "SHARED_STATEMENT_PIPELINE"),
    ),
    "WIO_MONTHLY_STATEMENT": (
        ("Acquire Archive and Read Back", "OUTLOOK_FINANCE_ACQUISITION"),
        ("Run Shared Statement Pipeline", "SHARED_STATEMENT_PIPELINE"),
    ),
    "AI_PROPOSAL": (
        ("Invoke Subscription Agent Adapter", "SUBSCRIPTION_AGENT_ADAPTER"),
    ),
    "FINANCE_OPERATIONS_STATUS": (
        ("Dispatch Reviewed Artifact", "INTERACTIVE_ARTIFACT_HANDOFF"),
        ("Dispatch Document Request", "DOCUMENT_EXTRACTION_REQUEST"),
    ),
    "INTERACTIVE_ARTIFACT_HANDOFF": (
        ("Run Statement Pipeline", "SHARED_STATEMENT_PIPELINE"),
    ),
    "DOCUMENT_EXTRACTION_REQUEST": (
        ("Run Local Extraction Ladder", "LOCAL_PDF_EXTRACTION"),
    ),
    "FINANCE_MCP_FACADE": (
        ("finance.status", "FINANCE_OPERATIONS_STATUS"),
        ("artifact.submit_reviewed", "FINANCE_OPERATIONS_STATUS"),
        ("document.request", "FINANCE_OPERATIONS_STATUS"),
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
    input_fields: tuple[str, ...],
    output_fields: tuple[str, ...],
    declared_in: tuple[str, str],
    aliases: tuple[tuple[str, str], ...] = (),
) -> dict:
    return {
        "name": name,
        "edge": edge,
        "edges": (edge, *aliases),
        "target": target,
        "input_fields": input_fields,
        "output_fields": output_fields,
        "declared_in": declared_in,
    }


STATEMENT_CONTEXT = (
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
)
ACQUISITION_REQUEST = (
    "run_id",
    "source_code",
    "folder_id",
    "senders",
    "subjects",
    "window_start",
    "run_upper_bound",
    "onedrive_parent_id",
)
SWEEP_REQUEST = (
    "run_id",
    "source_code",
    "folder_id",
    "senders",
    "subjects",
    "window_start",
    "run_upper_bound",
    "max_messages",
)
DOCUMENT_REQUEST = (
    "document_id",
    "expected_sha256",
    "document_profile",
    "requested_schema_version",
)
EXTRACTION_DOCUMENT = ("document_id", "source_sha256", "document_profile")
OUTBOX_INPUT = (
    "outbox_id",
    "payload_sha256",
    "artifact_item_id",
    "artifact_schema_version",
    "actual_file_id",
    "config_version",
    "state",
    "attempt_count",
)

# Every direct executeWorkflow edge is represented once. EI and WIO share
# reviewed contracts, so aliases keep the fixture list small without hiding
# either caller from the observed topology check.
BOUNDARY_FIXTURES: tuple[dict, ...] = (
    boundary_case(
        "monthly acquisition request",
        ("EI_MONTHLY_STATEMENT", "Acquire Archive and Read Back"),
        "OUTLOOK_FINANCE_ACQUISITION",
        ACQUISITION_REQUEST,
        (
            "message_id",
            "source_attachment_id",
            "document_sha256",
            "onedrive_item_id",
            "archive_readback_verified",
        ),
        ("EI_MONTHLY_STATEMENT", "OUTLOOK_FINANCE_ACQUISITION"),
        (("WIO_MONTHLY_STATEMENT", "Acquire Archive and Read Back"),),
    ),
    boundary_case(
        "statement pipeline input",
        ("EI_MONTHLY_STATEMENT", "Run Shared Statement Pipeline"),
        "SHARED_STATEMENT_PIPELINE",
        STATEMENT_CONTEXT,
        (
            "run_id",
            "workflow_code",
            "state",
            "receipt_sha256",
            "terminal_readback_verified",
        ),
        ("EI_MONTHLY_STATEMENT", "SHARED_STATEMENT_PIPELINE"),
        (("WIO_MONTHLY_STATEMENT", "Run Shared Statement Pipeline"),),
    ),
    boundary_case(
        "statement pipeline to AI proposal",
        ("SHARED_STATEMENT_PIPELINE", "Request Scoped AI Proposals"),
        "AI_PROPOSAL",
        ("policy_id", "unresolved"),
        ("policy_id", "job_id", "idempotency_key", "proposals"),
        ("SHARED_STATEMENT_PIPELINE", "AI_PROPOSAL"),
    ),
    boundary_case(
        "statement pipeline to PDF extraction",
        ("SHARED_STATEMENT_PIPELINE", "Run Isolated PDF Extraction"),
        "LOCAL_PDF_EXTRACTION",
        EXTRACTION_DOCUMENT,
        ("extracted_text", "text_quality", "parser", "validation_status", "result_ref"),
        ("SHARED_STATEMENT_PIPELINE", "LOCAL_PDF_EXTRACTION"),
    ),
    boundary_case(
        "statement pipeline to Actual outbox apply",
        ("SHARED_STATEMENT_PIPELINE", "Apply Prepared Outbox Safely"),
        "ACTUAL_OUTBOX_APPLY",
        OUTBOX_INPUT,
        (
            "outbox_id",
            "account_id",
            "state",
            "observed_sha256",
            "expected_sha256",
            "current_balance",
            "writer_release_verified",
        ),
        ("SHARED_STATEMENT_PIPELINE", "ACTUAL_OUTBOX_APPLY"),
    ),
    boundary_case(
        "AI proposal to subscription adapter",
        ("AI_PROPOSAL", "Invoke Subscription Agent Adapter"),
        "SUBSCRIPTION_AGENT_ADAPTER",
        (
            "schema_version",
            "job_id",
            "idempotency_key",
            "operation_code",
            "agent_provider",
            "policy_id",
            "policy_class",
            "policy_sha256",
            "config_sha256",
            "output_schema_sha256",
            "unresolved",
        ),
        (
            "agent_provider",
            "provider_model",
            "provider_reasoning_effort",
            "provider_auth_mode",
            "schema_version",
            "proposals",
        ),
        ("AI_PROPOSAL", "SUBSCRIPTION_AGENT_ADAPTER"),
    ),
    boundary_case(
        "operations status to artifact handoff",
        ("FINANCE_OPERATIONS_STATUS", "Dispatch Reviewed Artifact"),
        "INTERACTIVE_ARTIFACT_HANDOFF",
        ("operation_code", "artifact_id", "expected_sha256"),
        ("run_id", "trigger_kind", "message_id", "attachment_id", "document_sha256"),
        ("FINANCE_OPERATIONS_STATUS", "INTERACTIVE_ARTIFACT_HANDOFF"),
    ),
    boundary_case(
        "operations status to document request",
        ("FINANCE_OPERATIONS_STATUS", "Dispatch Document Request"),
        "DOCUMENT_EXTRACTION_REQUEST",
        ("operation_code", *DOCUMENT_REQUEST),
        DOCUMENT_REQUEST,
        ("FINANCE_OPERATIONS_STATUS", "DOCUMENT_EXTRACTION_REQUEST"),
    ),
    boundary_case(
        "artifact handoff to statement pipeline",
        ("INTERACTIVE_ARTIFACT_HANDOFF", "Run Statement Pipeline"),
        "SHARED_STATEMENT_PIPELINE",
        STATEMENT_CONTEXT,
        ("run_id", "state"),
        ("INTERACTIVE_ARTIFACT_HANDOFF", "SHARED_STATEMENT_PIPELINE"),
    ),
    boundary_case(
        "document request to local extraction",
        ("DOCUMENT_EXTRACTION_REQUEST", "Run Local Extraction Ladder"),
        "LOCAL_PDF_EXTRACTION",
        EXTRACTION_DOCUMENT,
        ("extracted_text", "text_quality", "result_ref"),
        ("DOCUMENT_EXTRACTION_REQUEST", "LOCAL_PDF_EXTRACTION"),
    ),
    boundary_case(
        "status facade",
        ("FINANCE_MCP_FACADE", "finance.status"),
        "FINANCE_OPERATIONS_STATUS",
        ("_mcp_request_id", "operation_code"),
        ("status", "execution_count", "failed_count", "running_count", "checked_at"),
        ("FINANCE_MCP_FACADE", "FINANCE_OPERATIONS_STATUS"),
    ),
    boundary_case(
        "artifact facade",
        ("FINANCE_MCP_FACADE", "artifact.submit_reviewed"),
        "FINANCE_OPERATIONS_STATUS",
        ("_mcp_request_id", "operation_code", "artifact_id", "expected_sha256"),
        ("request_id", "operation_code", "target_workflow_code"),
        ("FINANCE_MCP_FACADE", "FINANCE_OPERATIONS_STATUS"),
    ),
    boundary_case(
        "document facade",
        ("FINANCE_MCP_FACADE", "document.request"),
        "FINANCE_OPERATIONS_STATUS",
        ("_mcp_request_id", "operation_code", *DOCUMENT_REQUEST),
        ("request_id", "operation_code", "target_workflow_code"),
        ("FINANCE_MCP_FACADE", "FINANCE_OPERATIONS_STATUS"),
    ),
    boundary_case(
        "outbox recovery",
        ("ACTUAL_OUTBOX_RECOVERY", "Apply Nonterminal Outbox Safely"),
        "ACTUAL_OUTBOX_APPLY",
        OUTBOX_INPUT,
        ("outbox_id", "state", "writer_release_verified"),
        ("ACTUAL_OUTBOX_RECOVERY", "ACTUAL_OUTBOX_APPLY"),
    ),
    boundary_case(
        "writer lease",
        ("ACTUAL_OUTBOX_APPLY", "Acquire Recovery Writer Fence"),
        "FINANCE_WRITER_LEASE",
        ("operation", "resource_key", "lease_owner", "ttl_seconds"),
        (
            "operation",
            "resource_key",
            "lease_id",
            "lease_owner",
            "fencing_token",
            "expires_at",
        ),
        ("ACTUAL_OUTBOX_APPLY", "FINANCE_WRITER_LEASE"),
        (
            ("ACTUAL_OUTBOX_APPLY", "Assert Recovery Fence Before Import"),
            ("ACTUAL_OUTBOX_APPLY", "Release Recovery Writer Fence"),
        ),
    ),
    boundary_case(
        "cashback sweep",
        ("RAKBANK_LIVE_CASHBACK", "Sweep Exact Outlook Messages"),
        "OUTLOOK_MESSAGE_SWEEP",
        SWEEP_REQUEST,
        (
            "run_id",
            "source_code",
            "window_start",
            "run_upper_bound",
            "scanned_count",
            "matched_count",
            "pagination_exhausted",
            "heartbeat",
            "messages",
            "cursor_commit_eligible",
        ),
        ("RAKBANK_LIVE_CASHBACK", "OUTLOOK_MESSAGE_SWEEP"),
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

    def test_boundary_fixtures_cover_reviewed_caller_callee_fields(self) -> None:
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

        for fixture in BOUNDARY_FIXTURES:
            caller_code, node_name = fixture["edge"]
            with self.subTest(boundary=fixture["name"]):
                self.assertEqual(
                    observed_edges[(caller_code, node_name)], fixture["target"]
                )
                input_fixture = synthetic_fixture(fixture["input_fields"])
                output_fixture = synthetic_fixture(fixture["output_fields"])
                self.assertEqual(set(input_fixture), set(fixture["input_fields"]))
                self.assertEqual(set(output_fixture), set(fixture["output_fields"]))
                documents = [
                    json.dumps(self.workflow_for_code(code), ensure_ascii=True).replace(
                        '\\"', '"'
                    )
                    for code in fixture["declared_in"]
                ]
                for field in (*fixture["input_fields"], *fixture["output_fields"]):
                    self.assertTrue(
                        any(
                            re.search(
                                rf"(?<![A-Za-z0-9_]){re.escape(field)}(?![A-Za-z0-9_])",
                                document,
                            )
                            for document in documents
                        ),
                        f"{fixture['name']} field {field} missing from declared boundary",
                    )

    def test_error_and_lease_boundaries_have_redacted_fixed_shapes(self) -> None:
        error = self.workflow_for_code("OPERATIONS_ERROR_HANDLER")
        error_text = json.dumps(error, ensure_ascii=True).replace('\\"', '"')
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
        lease_text = json.dumps(lease, ensure_ascii=True).replace('\\"', '"')
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
