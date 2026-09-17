from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "deploy" / "finance-runtime"
WORKFLOW = ROOT / "integrations" / "n8n" / "workflows" / "15-finance-mcp-facade.json"


def executable(path: Path, source: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(source), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


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

    def test_provisioner_fake_1password_state_machine_is_absent_only(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory:
            root = Path(directory)
            state = root / "state.json"
            op_log = root / "op.log"
            mutator_log = root / "mutator.log"
            fake_op = executable(
                root / "op",
                f"""
                import json, os, pathlib, sys
                pathlib.Path(r'{op_log}').open('a').write(json.dumps(sys.argv[1:]) + '\\n')
                if sys.argv[1:4] != ['item', 'get', 'Finance Statement Tracker Runtime'] or sys.argv[4:6] != ['--vault', 'FinanceRuntime']:
                    raise SystemExit(11)
                fields = [{{'label': 'finance_n8n_mcp_bearer', 'type': 'CONCEALED', 'value': 'fixture-value'}}] if pathlib.Path(r'{state}').exists() else []
                print(json.dumps({{'title': 'Finance Statement Tracker Runtime', 'vault': {{'name': 'FinanceRuntime'}}, 'fields': fields}}))
                """,
            )
            mutator = executable(
                root / "mutator",
                f"""
                import json, pathlib, sys
                payload = json.load(sys.stdin)
                if payload['item_path'] != 'FinanceRuntime/Finance Statement Tracker Runtime' or payload['field'] != 'finance_n8n_mcp_bearer' or not payload['value']:
                    raise SystemExit(12)
                with pathlib.Path(r'{mutator_log}').open('a') as stream:
                    stream.write('called\\n')
                pathlib.Path(r'{state}').write_text('present')
                """,
            )
            environment = {
                **os.environ,
                "FINANCE_MCP_PROVISION_ACK": "CREATE_FINANCE_MCP_BEARER_ONCE",
                "OP_BIN": str(fake_op),
                "FINANCE_MCP_APPROVED_MUTATOR": str(mutator),
                "FINANCE_MCP_PROVISION_TMPDIR": str(root),
            }
            provisioner = RUNTIME / "provision-finance-mcp-bearer"
            first = subprocess.run([str(provisioner)], env=environment, text=True, capture_output=True, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertNotIn("fixture-value", first.stdout + first.stderr)
            second = subprocess.run([str(provisioner)], env=environment, text=True, capture_output=True, check=False)
            self.assertNotEqual(second.returncode, 0)
            self.assertNotIn("fixture-value", second.stdout + second.stderr)
            self.assertEqual(mutator_log.read_text().splitlines(), ["called"])
            self.assertIn("--vault", op_log.read_text())
            self.assertNotIn("edit", op_log.read_text())

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
