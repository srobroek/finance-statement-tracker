from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "integrations" / "n8n" / "setup-workflows"
WORKFLOW_PATH = SETUP / "22-onedrive-finance-evidence-root-setup.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class OneDriveRootSetupWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = load_json(WORKFLOW_PATH)
        cls.nodes = {node["name"]: node for node in cls.workflow["nodes"]}
        cls.manifest = load_json(SETUP / "manifest.json")

    def test_workflow_is_explicit_setup_only_and_outside_regular_registry(self) -> None:
        regular = load_json(ROOT / "integrations" / "n8n" / "pipeline-registry.json")
        self.assertNotIn(
            WORKFLOW_PATH.name,
            {row["file"] for row in regular["workflows"]},
        )
        self.assertFalse(self.workflow["active"])
        meta = self.workflow["meta"]
        self.assertTrue(meta["manualOnly"])
        self.assertTrue(meta["setupOnly"])
        self.assertTrue(meta["activationForbidden"])
        self.assertTrue(meta["scheduleForbidden"])
        self.assertEqual(meta["workflowFolder"]["name"], "90 Platform & Admin")
        self.assertEqual(self.workflow["nodes"][1]["type"], "n8n-nodes-base.manualTrigger")
        self.assertFalse(
            any(node["type"] == "n8n-nodes-base.scheduleTrigger" for node in self.workflow["nodes"])
        )
        self.assertEqual(self.manifest["import_policy"], "EXPLICIT_SINGLE_FILE_ONLY")
        self.assertTrue(self.manifest["activation_forbidden"])

    def test_onedrive_nodes_use_only_the_bound_oauth_credential(self) -> None:
        provider_nodes = [
            node for node in self.workflow["nodes"]
            if node["type"] == "n8n-nodes-base.microsoftOneDrive"
        ]
        self.assertEqual(len(provider_nodes), 4)
        for node in provider_nodes:
            self.assertEqual(node["typeVersion"], 1.1)
            self.assertEqual(
                node["credentials"],
                {
                    "microsoftOneDriveOAuth2Api": {
                        "id": "BIND_ONEDRIVE",
                        "name": "Finance OneDrive",
                    }
                },
            )
        self.assertEqual(
            {node["parameters"]["operation"] for node in provider_nodes},
            {"getChildren", "create"},
        )

    def test_create_is_single_segment_at_drive_root_after_exact_root_listing(self) -> None:
        setup = self.nodes["Setup Parameters"]
        assignments = {
            row["name"]: row["value"]
            for row in setup["parameters"]["assignments"]["assignments"]
        }
        self.assertEqual(assignments["root_folder_name"], "Finance Evidence")
        self.assertEqual(assignments["expected_parent_scope"], "root")
        self.assertTrue(assignments["create_if_absent"])

        initial = self.nodes["List Drive Root Children"]
        readback = self.nodes["Read Back Drive Root Children"]
        for node in (initial, readback):
            self.assertEqual(node["parameters"]["resource"], "folder")
            self.assertEqual(node["parameters"]["operation"], "getChildren")
            self.assertEqual(node["parameters"]["folderId"], "root")
            self.assertTrue(node["alwaysOutputData"])

        create = self.nodes["Create Finance Evidence at Drive Root"]
        self.assertEqual(create["parameters"]["resource"], "folder")
        self.assertEqual(create["parameters"]["operation"], "create")
        self.assertEqual(create["parameters"]["options"], {})
        self.assertNotIn("parentFolderId", create["parameters"]["options"])
        self.assertNotIn("/", assignments["root_folder_name"])
        self.assertNotIn("\\", assignments["root_folder_name"])

        connections = self.workflow["connections"]
        self.assertEqual(
            connections["Create Required?"]["main"][0][0]["node"],
            "Create Finance Evidence at Drive Root",
        )
        self.assertEqual(
            connections["Create Required?"]["main"][1][0]["node"],
            "Read Back Drive Root Children",
        )

    def test_contract_fails_closed_on_conflicts_drift_and_nested_duplication(self) -> None:
        resolve = self.nodes["Resolve Existing Root State"]["parameters"]["jsCode"]
        verify = self.nodes["Verify Exact Root Readback"]["parameters"]["jsCode"]
        receipt = self.nodes["Emit Redacted Setup Receipt"]["parameters"]["jsCode"]
        for marker in (
            "ONEDRIVE_ROOT_NAME_OCCUPIED_BY_NON_FOLDER",
            "ONEDRIVE_ROOT_FOLDER_CASE_MISMATCH",
            "ONEDRIVE_ROOT_FOLDER_DUPLICATE_EXACT_MATCH",
        ):
            self.assertIn(marker, resolve)
        for marker in (
            "ONEDRIVE_ROOT_READBACK_EXACT_COUNT_MISMATCH",
            "ONEDRIVE_ROOT_REUSE_ID_DRIFT",
            "ONEDRIVE_ROOT_CREATE_READBACK_MISMATCH",
        ):
            self.assertIn(marker, verify)
        self.assertIn("ONEDRIVE_NESTED_FINANCE_EVIDENCE_DUPLICATION_DETECTED", receipt)
        self.assertEqual(
            self.nodes["List Finance Evidence Children"]["parameters"]["folderId"],
            "={{ $json.root_folder_id }}",
        )

    def test_final_receipt_is_redacted_and_has_no_other_mutation_surface(self) -> None:
        final_code = self.nodes["Emit Redacted Setup Receipt"]["parameters"]["jsCode"]
        for marker in (
            "folder_id_redacted: true",
            "credential_values_recorded: false",
            "drive_metadata_recorded: false",
            "file_contents_recorded: false",
            "production_workflows_activated: false",
            "actual_writes: false",
            "cashback_writes: false",
        ):
            self.assertIn(marker, final_code)
        final_object = final_code.split("return [{", 1)[1]
        self.assertNotIn("root_folder_id:", final_object)

        node_types = {node["type"] for node in self.workflow["nodes"]}
        self.assertNotIn("n8n-nodes-base.httpRequest", node_types)
        self.assertNotIn("n8n-nodes-base.executeCommand", node_types)
        self.assertNotIn("n8n-nodes-base.ssh", node_types)
        self.assertFalse(
            any(node["type"].startswith("n8n-nodes-finance.") for node in self.workflow["nodes"])
        )


if __name__ == "__main__":
    unittest.main()
