from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
N8N = ROOT / "integrations" / "n8n"
WORKFLOW = N8N / "workflows" / "15-finance-mcp-facade.json"
CONTRACT = ROOT / "config" / "mcp-facade.json"
MANIFEST = N8N / "application-manifest.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def exported_tools(workflow: dict) -> dict[str, dict]:
    """Extract the MCP tools as n8n exports them, not from a second fixture."""
    trigger = next(node for node in workflow["nodes"] if node["type"].endswith("mcpTrigger"))
    tools = {
        node["parameters"]["name"]: node
        for node in workflow["nodes"]
        if node["type"].endswith("toolWorkflow")
    }
    if trigger["name"] in workflow["connections"]:
        raise AssertionError("MCP trigger must not be the ai_tool source")
    for tool_name in tools:
        expected = {
            "ai_tool": [[
                {"node": trigger["name"], "type": "ai_tool", "index": 0}
            ]]
        }
        if workflow["connections"].get(tool_name) != expected:
            raise AssertionError(f"MCP tool {tool_name} is not connected to the trigger")
    return tools


def published_tools_list(workflow: dict) -> list[str]:
    """Model the MCP publication registry from the authoritative n8n graph."""
    return sorted(exported_tools(workflow))


def call_published_tool(workflow: dict, name: str, arguments: dict[str, str]) -> dict:
    """Execute the bounded source contract used by publication tests.

    n8n evaluates the ``$fromAI`` expressions at runtime. This harness supplies
    those values and returns the exact subworkflow call envelope without making
    any external or production mutation.
    """
    tools = exported_tools(workflow)
    if name not in tools:
        raise LookupError("MCP_TOOL_NOT_FOUND")
    parameters = tools[name]["parameters"]
    values = parameters["workflowInputs"]["value"]
    required = {key for key, value in values.items() if "$fromAI(" in str(value)}
    if set(arguments) != required:
        raise ValueError("MCP_TOOL_ARGUMENTS_INVALID")
    inputs = {
        key: arguments.get(key, value)
        for key, value in values.items()
    }
    return {
        "tool": name,
        "workflow_id": parameters["workflowId"]["value"],
        "operation_code": inputs["operation_code"],
        "inputs": inputs,
    }


class FinanceMcpContractTests(unittest.TestCase):
    def test_exported_w15_contract_matches_application_contract(self) -> None:
        workflow = load_json(WORKFLOW)
        contract = load_json(CONTRACT)
        manifest = load_json(MANIFEST)
        operation_rows = contract["operations"]
        expected_names = [row["name"] for row in operation_rows]

        trigger = next(node for node in workflow["nodes"] if node["type"].endswith("mcpTrigger"))
        self.assertEqual(trigger["parameters"]["path"], "finance-operations-v1")
        self.assertEqual(manifest["mcp"]["path"], contract["path"])
        self.assertEqual(manifest["mcp"]["operations"], expected_names)
        self.assertEqual(
            manifest["mcp"]["contract"]["sha256"],
            hashlib.sha256(CONTRACT.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        )

        tools = exported_tools(workflow)
        self.assertEqual(set(tools), set(expected_names))
        self.assertEqual(
            sorted(node["name"] for node in tools.values()),
            sorted(expected_names),
        )
        for row in operation_rows:
            inputs = tools[row["name"]]["parameters"]["workflowInputs"]["value"]
            self.assertEqual(inputs["operation_code"], row["internal_operation_code"])
            self.assertNotIn("_", tools[row["name"]]["parameters"]["name"])

    def test_external_tool_names_reject_legacy_underscore_and_unversioned_forms(self) -> None:
        tools = exported_tools(load_json(WORKFLOW))
        external_names = set(tools)
        self.assertNotIn("finance_status", external_names)
        self.assertNotIn("artifact_submit_reviewed", external_names)
        self.assertNotIn("document_request", external_names)
        self.assertNotIn("finance.status", external_names)
        self.assertNotIn("artifact.submit_reviewed", external_names)
        self.assertNotIn("document.request", external_names)

    def test_source_publication_lists_three_tools_and_executes_exact_calls(self) -> None:
        workflow = load_json(WORKFLOW)
        self.assertEqual(
            published_tools_list(workflow),
            [
                "finance.document.request.v1",
                "finance.reviewed-artifact.handoff.v1",
                "finance.status.v1",
            ],
        )
        calls = (
            ("finance.status.v1", {}, "finance.status"),
            (
                "finance.reviewed-artifact.handoff.v1",
                {"artifact_id": "artifact-123"},
                "artifact.submit_reviewed",
            ),
            (
                "finance.document.request.v1",
                {
                    "document_id": "document-123",
                    "expected_sha256": "a" * 64,
                    "document_profile": "BANK_STATEMENT_V1",
                    "requested_schema_version": "1",
                },
                "document.request",
            ),
        )
        for name, arguments, operation_code in calls:
            with self.subTest(tool=name):
                result = call_published_tool(workflow, name, arguments)
                self.assertEqual(result["tool"], name)
                self.assertEqual(
                    result["workflow_id"], "10000000-0000-4000-8000-000000000010"
                )
                self.assertEqual(result["operation_code"], operation_code)

    def test_source_publication_rejects_unknown_tool_and_invalid_arguments(self) -> None:
        workflow = load_json(WORKFLOW)
        with self.assertRaisesRegex(LookupError, "MCP_TOOL_NOT_FOUND"):
            call_published_tool(workflow, "finance.unknown.v1", {})
        with self.assertRaisesRegex(ValueError, "MCP_TOOL_ARGUMENTS_INVALID"):
            call_published_tool(workflow, "finance.status.v1", {"unexpected": "value"})


if __name__ == "__main__":
    unittest.main()
