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
    connections = {
        edge["node"]
        for edge in workflow["connections"][trigger["name"]]["ai_tool"][0]
    }
    if set(tools) != connections:
        raise AssertionError("MCP export connections and tool declarations differ")
    return tools


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


if __name__ == "__main__":
    unittest.main()
