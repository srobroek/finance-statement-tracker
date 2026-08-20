from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "deploy" / "finance-runtime"


class FinanceMcpDisposableProofTests(unittest.TestCase):
    def test_ack_scope_and_boundaries_are_explicit(self):
        source = (RUNTIME / "run-finance-mcp-disposable-proof.sh").read_text()
        self.assertIn("ACTIVATE_W15_ONLY", source)
        self.assertIn("FINANCE_MCP_BINDER_VERIFIED", source)
        self.assertIn('runtime_scope}" == "disposable"', source)
        self.assertIn('workflow_scope}" == "W15"', source)
        self.assertIn("wrong-mcp-secret", source)
        self.assertIn("wrong-cloudflare-authority", source)
        self.assertIn("runner-bearer", source)
        self.assertNotIn("curl", source)

    def test_trap_has_all_required_teardown_operations(self):
        source = (RUNTIME / "run-finance-mcp-disposable-proof.sh").read_text()
        for action in ("deactivate", "unpublish", "remove-webhook", "remove-disposable-rows", "readback-clean"):
            self.assertIn(f"run_gate {action}", source)
        self.assertIn('"cleanup":"REQUIRED_ON_EXIT"', source)
        self.assertIn('"values":"REDACTED"', source)

    def test_workflow_export_remains_inactive_before_proof(self):
        workflow = json.loads(
            (ROOT / "integrations/n8n/workflows/15-finance-mcp-facade.json").read_text()
        )
        self.assertFalse(workflow["active"])
        self.assertIsNone(workflow.get("activeVersionId"))
        self.assertFalse(workflow["meta"]["instanceMcpRequired"])


if __name__ == "__main__":
    unittest.main()
