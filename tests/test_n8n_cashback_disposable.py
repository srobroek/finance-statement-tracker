import importlib.util
import json
import shutil
import subprocess
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


    @unittest.skipUnless(shutil.which("node"), "Node is required to execute the fixture boundary")
    def test_normalization_fixture_emits_compact_canonical_event_and_empty_heartbeat(self):
        fixture = HARNESS.derive_workflow(ROOT, datetime(2026, 9, 5, 12, tzinfo=timezone.utc))
        node = next(n for n in fixture["nodes"] if n["name"] == "Normalize Archived Notifications")
        code = node["parameters"]["jsCode"]
        for empty in (False, True):
            script = "const $ = () => ({first:()=>({json:{body:{empty:" + json.dumps(empty) + "}}})});"
            script += "const $input = {first:()=>({json:{source:'outlook:rakbank',completed_at:'2026-09-05T12:01:00Z',cursor:'2026-09-05T12:01:00Z'}})};"
            script += "const value = (()=>{" + code + "})();console.log(JSON.stringify(value[0].json));"
            result = json.loads(subprocess.check_output(["node", "-e", script], text=True))
            self.assertEqual(result["accepted_count"], 0 if empty else 1)
            self.assertEqual(result["scanned_count"], 0 if empty else 1)
            self.assertEqual(result["cursor"], '2026-09-05T12:01:00Z')
            self.assertNotIn("messages", result)
            if not empty:
                self.assertEqual(result["events"][0]["source_event_id"], "disposable-w02-rak-amazon-1:0")
                self.assertEqual(result["events"][0]["bucket_code"], "RAK_STANDARD")
                self.assertTrue(result["events"][0]["decision_trace"])

    def test_runner_substitution_is_explicit_and_compact_http_gates_remain_real(self):
        fixture = HARNESS.derive_workflow(ROOT, datetime(2026, 9, 5, 12, tzinfo=timezone.utc))
        self.assertIn("Native Python task-runner protocol and image packaging", fixture["meta"]["unprovenBoundaries"])
        paths = {n["parameters"]["url"] for n in fixture["nodes"] if n["type"] == "n8n-nodes-base.httpRequest"}
        self.assertIn("http://127.0.0.1:5010/api/ingest/transaction", paths)
        self.assertIn("http://127.0.0.1:5010/api/ingest/receipt", paths)
        self.assertNotIn("http://127.0.0.1:5010/api/outlook/messages", paths)


if __name__ == "__main__":
    unittest.main()
