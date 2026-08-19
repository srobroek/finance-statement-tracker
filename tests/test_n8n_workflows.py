from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "integrations" / "n8n" / "pipeline-registry.json"
WORKFLOWS = ROOT / "integrations" / "n8n" / "workflows"


class N8nWorkflowTests(unittest.TestCase):
    def registry(self) -> dict[str, object]:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    def test_registry_maps_every_existing_codex_automation(self) -> None:
        registry = self.registry()
        automations = json.loads(
            (ROOT / "config" / "codex-automations.json").read_text(encoding="utf-8")
        )
        mapped = {
            row["replaces_codex_automation"]
            for row in registry["workflows"]
            if row.get("replaces_codex_automation")
        }
        self.assertEqual(mapped, {row["id"] for row in automations["automations"]})

    def test_mcp_allowlist_contains_only_explicit_read_or_bounded_operations(self) -> None:
        registry = self.registry()
        declared = {
            row["code"]
            for row in registry["workflows"]
            if row.get("mcp_exposed") is True
        }
        self.assertEqual(declared, set(registry["mcp"]["allowed_workflow_codes"]))
        self.assertNotIn("SHARED_STATEMENT_PIPELINE", declared)
        self.assertNotIn("AI_PROPOSAL", declared)

    def test_adcb_has_no_recurring_n8n_pipeline(self) -> None:
        registry = self.registry()
        scheduled_sources = {
            row.get("source") for row in registry["workflows"] if row.get("schedule")
        }
        self.assertNotIn("ADCB_CASHBACK", scheduled_sources)
        self.assertEqual(registry["retired_or_not_migrated"][0]["source"], "ADCB_CASHBACK")

    def test_workflow_exports_are_sanitized_inactive_and_registered(self) -> None:
        registry = self.registry()
        expected = {row["file"] for row in registry["workflows"]}
        actual = {path.name for path in WORKFLOWS.glob("*.json")}
        self.assertEqual(actual, expected)
        forbidden_node_types = {
            "n8n-nodes-base.executeCommand",
            "n8n-nodes-base.ssh",
        }
        for filename in sorted(expected):
            with self.subTest(workflow=filename):
                path = WORKFLOWS / filename
                workflow = json.loads(path.read_text(encoding="utf-8"))
                self.assertFalse(workflow.get("active", False))
                self.assertTrue(workflow.get("id"))
                self.assertTrue(workflow.get("name"))
                self.assertTrue(workflow.get("nodes"))
                self.assertEqual(
                    workflow.get("settings", {}).get("timezone"), "Asia/Dubai"
                )
                self.assertEqual(
                    workflow.get("settings", {}).get("saveDataSuccessExecution"),
                    "none",
                )
                node_types = {node["type"] for node in workflow["nodes"]}
                self.assertFalse(node_types & forbidden_node_types)
                raw = path.read_text(encoding="utf-8").casefold()
                for secret_marker in (
                    "13393666",
                    "sjor2908",
                    "actual_password",
                    "cashback_ingest_token",
                    "172.20.10.20",
                    "notion",
                ):
                    self.assertNotIn(secret_marker, raw)

    def test_document_tool_is_narrow_and_external_pdf_saas_is_off(self) -> None:
        config = json.loads(
            (ROOT / "config" / "document-processing.json").read_text(
                encoding="utf-8"
            )
        )
        tool = config["model_tool"]
        self.assertEqual(
            set(tool["allowed_input_fields"]),
            {
                "document_id",
                "expected_sha256",
                "document_profile",
                "requested_schema_version",
            },
        )
        self.assertIn("password", tool["forbidden_input_fields"])
        providers = {row["code"]: row for row in config["providers"]}
        self.assertTrue(providers["QPDF_UNLOCK"]["enabled"])
        self.assertTrue(providers["N8N_EXTRACT_FROM_FILE"]["enabled"])
        for provider in ("PDF_VECTOR", "PDF4ME", "PDF_CO"):
            self.assertFalse(providers[provider]["enabled"])
            self.assertTrue(providers[provider]["requires_explicit_document_approval"])

    def test_document_state_machine_has_fail_closed_terminal_states(self) -> None:
        tables = json.loads(
            (ROOT / "integrations" / "n8n" / "data-tables.json").read_text(
                encoding="utf-8"
            )
        )
        documents = next(
            row
            for row in tables["tables"]
            if row["name"] == "finance_document_operations"
        )
        for state in (
            "RECEIVED",
            "VALIDATED",
            "DECRYPTED",
            "EXTRACTED",
            "SCHEMA_VALIDATED",
            "READY_FOR_PARSE",
            "COMMITTED",
            "QUARANTINED",
            "UNSUPPORTED",
            "PASSWORD_FAILED",
        ):
            self.assertIn(state, documents["allowed_states"])
        self.assertEqual(
            documents["idempotency_key"],
            ["source_sha256", "document_profile", "requested_schema_version"],
        )

    def test_workflows_do_not_require_a_postgres_node(self) -> None:
        for path in WORKFLOWS.glob("*.json"):
            workflow = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn(
                "n8n-nodes-base.postgres",
                {node["type"] for node in workflow["nodes"]},
                path.name,
            )


if __name__ == "__main__":
    unittest.main()
