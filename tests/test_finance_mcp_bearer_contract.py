from __future__ import annotations

import json
import os
import stat
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "deploy" / "finance-runtime"
WORKFLOW = ROOT / "integrations" / "n8n" / "workflows" / "15-finance-mcp-facade.json"


class FinanceMcpBearerContractTests(unittest.TestCase):
    def test_schema_rejects_missing_extra_empty_newline_and_wrong_type_values(self):
        schema = json.loads((RUNTIME / "finance-n8n-mcp-bearer.schema.json").read_text())
        expected = {name: value["const"] for name, value in schema["properties"].items()}

        def valid(instance):
            return (
                set(instance) == set(schema["required"])
                and all(instance[name] == expected[name] and isinstance(instance[name], str) for name in expected)
            )

        self.assertTrue(valid(expected))
        for invalid in (
            {key: value for key, value in expected.items() if key != "placeholder"},
            {**expected, "unexpected": "value"},
            {**expected, "workflow_path": ""},
            {**expected, "workflow_path": "finance-operations-v1\n"},
            {**expected, "credential_type": 1},
        ):
            self.assertFalse(valid(invalid))

    def test_schema_is_closed_and_pins_every_boundary(self):
        schema = json.loads((RUNTIME / "finance-n8n-mcp-bearer.schema.json").read_text())
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            schema["required"],
            [
                "item_path",
                "concealed_field",
                "environment_name",
                "workflow_path",
                "credential_name",
                "credential_type",
                "placeholder",
            ],
        )
        self.assertEqual(
            {name: value["const"] for name, value in schema["properties"].items()},
            {
                "item_path": "FinanceRuntime/Finance Statement Tracker Runtime",
                "concealed_field": "finance_n8n_mcp_bearer",
                "environment_name": "FINANCE_N8N_MCP_BEARER",
                "workflow_path": "finance-operations-v1",
                "credential_name": "Finance MCP Facade Bearer",
                "credential_type": "httpBearerAuth",
                "placeholder": "BIND_FINANCE_MCP_FACADE",
            },
        )

    def test_workflow_path_and_placeholder_are_exact(self):
        workflow = json.loads(WORKFLOW.read_text())
        trigger = next(node for node in workflow["nodes"] if node["name"] == "Finance MCP Server Trigger")
        self.assertFalse(workflow["active"])
        self.assertEqual(trigger["parameters"]["path"], "finance-operations-v1")
        self.assertEqual(
            trigger["credentials"]["httpBearerAuth"]["id"],
            "BIND_FINANCE_MCP_FACADE",
        )

    def test_provisioner_is_absent_only_and_redacts(self):
        source = (RUNTIME / "provision-finance-mcp-bearer").read_text()
        self.assertNotIn("op item edit", source)
        self.assertEqual(source.count("openssl rand"), 1)
        self.assertIn("CREATE_FINANCE_MCP_BEARER_ONCE", source)
        self.assertIn("present_nonempty", source)
        self.assertNotIn("value=${", source)

    def test_shell_entrypoints_are_private_executables(self):
        for filename in (
            "provision-finance-mcp-bearer",
            "run-finance-mcp-disposable-proof.sh",
            "launch-codex-finance-mcp.sh",
        ):
            mode = stat.S_IMODE((RUNTIME / filename).stat().st_mode)
            self.assertEqual(mode & 0o111, 0o111, filename)


if __name__ == "__main__":
    unittest.main()
