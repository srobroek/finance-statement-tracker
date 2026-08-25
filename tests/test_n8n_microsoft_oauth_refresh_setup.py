from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "integrations" / "n8n" / "setup-workflows"
WORKFLOW_PATH = SETUP / "23-microsoft-oauth-refresh-proof.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class MicrosoftOAuthRefreshProofWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = load_json(WORKFLOW_PATH)
        cls.nodes = {node["name"]: node for node in cls.workflow["nodes"]}
        cls.manifest = load_json(SETUP / "manifest.json")

    def test_is_setup_only_inactive_manual_and_outside_regular_registry(self) -> None:
        regular = load_json(ROOT / "integrations" / "n8n" / "pipeline-registry.json")
        self.assertNotIn(WORKFLOW_PATH.name, {row["file"] for row in regular["workflows"]})
        self.assertFalse(self.workflow["active"])
        self.assertEqual(self.workflow["nodes"][1]["type"], "n8n-nodes-base.manualTrigger")
        self.assertFalse(any(node["type"] == "n8n-nodes-base.scheduleTrigger" for node in self.workflow["nodes"]))
        meta = self.workflow["meta"]
        self.assertTrue(meta["manualOnly"])
        self.assertTrue(meta["setupOnly"])
        self.assertTrue(meta["activationForbidden"])
        self.assertTrue(meta["scheduleForbidden"])
        self.assertEqual(meta["providerMutationScope"], "NONE")
        self.assertEqual(meta["workflowFolder"], {
            "id": "f1000000-0000-4000-8000-000000000191",
            "name": "Shared",
            "project": "Global",
            "placement": "EXPLICIT_SETUP_IMPORT_ONLY",
        })

    def test_outlook_read_is_server_filtered_date_bounded_and_limited_to_one(self) -> None:
        node = self.nodes["Read One Bounded Outlook Message"]
        self.assertEqual(node["type"], "n8n-nodes-base.microsoftOutlook")
        self.assertEqual(node["parameters"]["resource"], "folderMessage")
        self.assertEqual(node["parameters"]["operation"], "getAll")
        self.assertFalse(node["parameters"]["returnAll"])
        self.assertEqual(node["parameters"]["limit"], "={{ $('Freeze Bounded Probe').first().json.outlook_max_messages }}")
        self.assertEqual(node["parameters"]["output"], "fields")
        self.assertEqual(node["parameters"]["fields"], ["id"])
        filters = node["parameters"]["filtersUI"]["values"]["filters"]
        self.assertIn("outlook_window_start", filters["receivedAfter"])
        self.assertIn("outlook_window_end", filters["receivedBefore"])
        self.assertIn("outlook_server_filter", filters["custom"])
        self.assertFalse(node["parameters"]["options"]["downloadAttachments"])
        freeze = self.nodes["Freeze Bounded Probe"]["parameters"]["jsCode"]
        self.assertIn("outlook_server_filter: 'isDraft eq false'", freeze)
        self.assertIn("outlook_max_messages: 1", freeze)
        self.assertIn("7 * 24 * 60 * 60 * 1000", freeze)

    def test_exact_existing_oauth_placeholders_are_used_once_each(self) -> None:
        outlook = self.nodes["Read One Bounded Outlook Message"]
        drive = self.nodes["List OneDrive Root Read Only"]
        self.assertEqual(outlook["credentials"], {
            "microsoftOutlookOAuth2Api": {"id": "BIND_OUTLOOK", "name": "Finance Outlook"}
        })
        self.assertEqual(drive["credentials"], {
            "microsoftOneDriveOAuth2Api": {"id": "BIND_ONEDRIVE", "name": "Finance OneDrive"}
        })
        self.assertEqual(drive["parameters"], {
            "resource": "folder",
            "operation": "getChildren",
            "folderId": "={{ $('Freeze Bounded Probe').first().json.onedrive_folder_id }}",
        })

    def test_no_provider_or_finance_write_surface_exists(self) -> None:
        allowed_provider_operations = {
            ("n8n-nodes-base.microsoftOutlook", "getAll"),
            ("n8n-nodes-base.microsoftOneDrive", "getChildren"),
        }
        observed = {
            (node["type"], node["parameters"]["operation"])
            for node in self.workflow["nodes"]
            if node["type"] in {"n8n-nodes-base.microsoftOutlook", "n8n-nodes-base.microsoftOneDrive"}
        }
        self.assertEqual(observed, allowed_provider_operations)
        node_types = {node["type"] for node in self.workflow["nodes"]}
        self.assertNotIn("n8n-nodes-base.httpRequest", node_types)
        self.assertNotIn("n8n-nodes-base.executeCommand", node_types)
        self.assertNotIn("n8n-nodes-base.ssh", node_types)
        self.assertFalse(any(node_type.startswith("n8n-nodes-finance.") for node_type in node_types))

    def test_terminal_receipt_is_redacted_and_manifest_requires_restart_proof(self) -> None:
        code = self.nodes["Emit Redacted OAuth Proof Receipt"]["parameters"]["jsCode"]
        for marker in (
            "provider_writes: false",
            "message_fields_recorded: false",
            "file_fields_recorded: false",
            "credential_values_recorded: false",
            "token_values_recorded: false",
            "production_workflows_activated: false",
            "actual_writes: false",
            "cashback_writes: false",
        ):
            self.assertIn(marker, code)
        for forbidden in ("subject:", "body:", "webUrl:", "access_token:", "refresh_token:"):
            self.assertNotIn(forbidden, code)

        entry = next(row for row in self.manifest["workflows"] if row["file"] == WORKFLOW_PATH.name)
        self.assertEqual(entry["credential_placeholders"], ["BIND_OUTLOOK", "BIND_ONEDRIVE"])
        self.assertEqual(entry["allowed_provider_mutation"], "NONE")
        self.assertTrue(entry["restart_proof_required"])


if __name__ == "__main__":
    unittest.main()
