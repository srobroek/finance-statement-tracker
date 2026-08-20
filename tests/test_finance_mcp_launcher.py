from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "deploy" / "finance-runtime" / "launch-codex-finance-mcp.sh"


class FinanceMcpLauncherTests(unittest.TestCase):
    def test_launcher_uses_op_child_injection_and_redacted_cleanup(self):
        source = LAUNCHER.read_text()
        self.assertIn("op run --env", source)
        self.assertIn("op://FinanceRuntime/Finance Statement Tracker Runtime/finance_n8n_mcp_bearer", source)
        self.assertIn("Parent FINANCE_N8N_MCP_BEARER must be unset", source)
        self.assertIn('"secret":"REDACTED"', source)
        self.assertNotIn(".env", source)

    def test_preexisting_parent_secret_is_rejected(self):
        environment = os.environ.copy()
        environment["FINANCE_N8N_MCP_BEARER"] = "parent-secret"
        completed = subprocess.run(
            [str(LAUNCHER), "true"],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn("parent-secret", completed.stdout + completed.stderr)

    def test_fake_op_receives_reference_not_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_op = Path(directory) / "op"
            child_seen = Path(directory) / "child-env"
            fake_op.write_text(
                "#!/bin/sh\n"
                "[ \"$1\" = run ] || exit 11\n"
                "shift 4\n"
                "FINANCE_N8N_MCP_BEARER=child-secret \"$@\"\n"
            )
            fake_op.chmod(fake_op.stat().st_mode | stat.S_IXUSR)
            child = [
                "python3",
                "-c",
                "import os,pathlib; pathlib.Path(r'" + str(child_seen) + "').write_text(os.environ['FINANCE_N8N_MCP_BEARER'])",
            ]
            environment = os.environ.copy()
            environment.pop("FINANCE_N8N_MCP_BEARER", None)
            environment["OP_BIN"] = str(fake_op)
            completed = subprocess.run([str(LAUNCHER), *child], env=environment, capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(child_seen.read_text(), "child-secret")
            self.assertNotIn("child-secret", completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
