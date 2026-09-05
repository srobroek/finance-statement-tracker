import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cashback_disposable", ROOT / "scripts/run-n8n-cashback-disposable.py")
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


class CashbackDisposableBoundaryTests(unittest.TestCase):
    def test_fixture_preserves_production_receipt_and_cursor_gates(self):
        original = json.loads((ROOT / "integrations/n8n/workflows/02-rakbank-live-cashback.json").read_text())
        fixture = HARNESS.derive_workflow(ROOT, datetime(2026, 9, 5, 12, tzinfo=timezone.utc))
        originals = {n["name"]: n for n in original["nodes"]}
        for node in fixture["nodes"]:
            if node["name"] in {"Freeze Cursor Minus Overlap Window", "Build Frozen Mailbox Envelope",
                                 "Verify Service Receipt Before Cursor", "Verify Exact Cursor Readback"}:
                self.assertEqual(node, originals[node["name"]])
        self.assertEqual(fixture["connections"], original["connections"])

    def test_fixture_cannot_retain_provider_or_production_credentials(self):
        fixture = HARNESS.derive_workflow(ROOT, datetime(2026, 9, 5, 12, tzinfo=timezone.utc))
        self.assertFalse(fixture["active"])
        self.assertTrue(fixture["meta"]["productionImportForbidden"])
        for node in fixture["nodes"]:
            self.assertNotIn(node["type"], {"n8n-nodes-base.executeWorkflow", "n8n-nodes-base.dataTable", "n8n-nodes-base.scheduleTrigger"})
            if node["type"] == "n8n-nodes-base.httpRequest":
                self.assertTrue(node["parameters"]["url"].startswith("http://127.0.0.1:5010/api/"))
            for credential in node.get("credentials", {}).values():
                self.assertEqual(credential["id"], "DISPOSABLE_CASHBACK_ONLY")


if __name__ == "__main__":
    unittest.main()
