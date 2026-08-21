from __future__ import annotations

import hashlib
import json
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
N8N = ROOT / "integrations" / "n8n"
WORKFLOW = N8N / "workflows" / "15-finance-mcp-facade.json"
CONTRACT = ROOT / "config" / "mcp-facade.json"
MANIFEST = N8N / "application-manifest.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def declared_tools(workflow: dict) -> dict[str, dict]:
    """Extract declared MCP tool nodes from the workflow graph itself."""
    trigger = next(node for node in workflow["nodes"] if node["type"].endswith("mcpTrigger"))
    path = trigger.get("parameters", {}).get("path")
    if path != "finance-operations-v1":
        raise AssertionError("MCP trigger path must use the stable short route")
    if trigger.get("webhookId") != path:
        raise AssertionError("MCP trigger webhookId must equal the stable short route")
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


def declared_tools_list(workflow: dict) -> list[str]:
    """Derive the declared tool names from the authoritative n8n graph."""
    return sorted(declared_tools(workflow))


def build_tool_call_envelope(workflow: dict, name: str, arguments: dict[str, str]) -> dict:
    """Build the bounded source call envelope for structural contract tests.

    n8n evaluates the ``$fromAI`` expressions at runtime. This structural
    harness supplies those values and returns the exact subworkflow envelope;
    it does not publish a workflow or call n8n.
    """
    tools = declared_tools(workflow)
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
    def test_w15_source_contract_matches_application_contract(self) -> None:
        workflow = load_json(WORKFLOW)
        contract = load_json(CONTRACT)
        manifest = load_json(MANIFEST)
        operation_rows = contract["operations"]
        expected_names = [row["name"] for row in operation_rows]

        trigger = next(node for node in workflow["nodes"] if node["type"].endswith("mcpTrigger"))
        self.assertEqual(trigger["parameters"]["path"], "finance-operations-v1")
        self.assertEqual(trigger["webhookId"], "finance-operations-v1")
        self.assertEqual(manifest["mcp"]["path"], contract["path"])
        self.assertEqual(manifest["mcp"]["operations"], expected_names)
        self.assertEqual(
            manifest["mcp"]["contract"]["sha256"],
            hashlib.sha256(CONTRACT.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        )

        tools = declared_tools(workflow)
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
        tools = declared_tools(load_json(WORKFLOW))
        external_names = set(tools)
        self.assertNotIn("finance_status", external_names)
        self.assertNotIn("artifact_submit_reviewed", external_names)
        self.assertNotIn("document_request", external_names)
        self.assertNotIn("finance.status", external_names)
        self.assertNotIn("artifact.submit_reviewed", external_names)
        self.assertNotIn("document.request", external_names)

    def test_source_graph_declares_three_tools_and_exact_call_envelopes(self) -> None:
        workflow = load_json(WORKFLOW)
        self.assertEqual(
            declared_tools_list(workflow),
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
                result = build_tool_call_envelope(workflow, name, arguments)
                self.assertEqual(result["tool"], name)
                self.assertEqual(
                    result["workflow_id"], "10000000-0000-4000-8000-000000000010"
                )
                self.assertEqual(result["operation_code"], operation_code)

    def test_source_graph_rejects_unknown_tool_and_invalid_arguments(self) -> None:
        workflow = load_json(WORKFLOW)
        with self.assertRaisesRegex(LookupError, "MCP_TOOL_NOT_FOUND"):
            build_tool_call_envelope(workflow, "finance.unknown.v1", {})
        with self.assertRaisesRegex(ValueError, "MCP_TOOL_ARGUMENTS_INVALID"):
            build_tool_call_envelope(workflow, "finance.status.v1", {"unexpected": "value"})

    def test_source_graph_rejects_missing_or_prefixed_webhook_identity(self) -> None:
        for webhook_id in (None, "", "workflow/10000000-0000-4000-8000-000000000015/finance-operations-v1"):
            with self.subTest(webhook_id=webhook_id):
                workflow = deepcopy(load_json(WORKFLOW))
                trigger = next(node for node in workflow["nodes"] if node["type"].endswith("mcpTrigger"))
                trigger["webhookId"] = webhook_id
                with self.assertRaisesRegex(AssertionError, "webhookId"):
                    declared_tools(workflow)

        workflow = deepcopy(load_json(WORKFLOW))
        trigger = next(node for node in workflow["nodes"] if node["type"].endswith("mcpTrigger"))
        trigger["parameters"]["path"] = "workflow/10000000-0000-4000-8000-000000000015/finance-operations-v1"
        with self.assertRaisesRegex(AssertionError, "path"):
            declared_tools(workflow)

    def test_source_graph_rejects_reversed_or_empty_tool_wiring(self) -> None:
        workflow = deepcopy(load_json(WORKFLOW))
        trigger = next(node for node in workflow["nodes"] if node["type"].endswith("mcpTrigger"))
        tool = next(node for node in workflow["nodes"] if node["type"].endswith("toolWorkflow"))

        workflow["connections"] = {
            trigger["name"]: {
                "ai_tool": [[{"node": tool["name"], "type": "ai_tool", "index": 0}]]
            }
        }
        with self.assertRaisesRegex(AssertionError, "must not be the ai_tool source"):
            declared_tools(workflow)

        workflow = deepcopy(load_json(WORKFLOW))
        workflow["connections"] = {}
        with self.assertRaisesRegex(AssertionError, "not connected to the trigger"):
            declared_tools(workflow)


if __name__ == "__main__":
    unittest.main()
