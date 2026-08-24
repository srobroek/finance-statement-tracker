from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
N8N = ROOT / "integrations" / "n8n"
INTERFACE = N8N / "application-interface"
WORKFLOWS = N8N / "workflows"
TABLE_NAMES = {
    "finance_source_contracts",
    "finance_source_cursors",
    "finance_acquisition_receipts",
    "finance_archive_receipts",
    "finance_document_operations",
    "finance_pipeline_runs",
    "finance_actual_outbox",
    "finance_actual_verifications",
    "finance_reconciliations",
    "finance_config_versions",
    "finance_provider_circuits",
    "finance_execution_failures",
    "finance_mcp_requests",
    "finance_agent_jobs",
    "finance_ai_policy_contracts",
}
TARGET_TABLE_NAMES = {
    "finance_ingestion_state",
    "finance_documents",
    "finance_actual_batches",
    "finance_ai_reviews",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_adapter():
    spec = importlib.util.spec_from_file_location("finance_n8n_application_adapter", INTERFACE / "adapter.py")
    if spec is None or spec.loader is None:
        raise AssertionError("finance application adapter is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class N8nApplicationInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.adapter = load_adapter()
        cls.registry = load_json(N8N / "pipeline-registry.json")
        cls.tables = load_json(N8N / "data-tables.json")
        cls.table_schema = load_json(N8N / "data-tables.schema.json")
        cls.matrix = load_json(N8N / "data-table-migration-matrix.json")
        cls.folders = load_json(N8N / "workflow-folders.json")
        cls.fixture_manifest = load_json(N8N / "disposable" / "fixture-manifest.json")

    def stage(self, temporary: str) -> tuple[Path, dict]:
        destination = Path(temporary) / "application"
        path = self.adapter.stage_application(ROOT, destination, "a" * 40)
        return path, load_json(path)

    def test_finance_adapter_emits_the_strict_generic_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path, manifest = self.stage(temporary)
            self.assertEqual(
                set(manifest),
                {
                    "schema_version",
                    "application",
                    "workflows",
                    "folders",
                    "bootstrap",
                    "fixtures",
                    "credentials",
                    "route",
                },
            )
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(
                manifest["application"],
                {"id": "finance-statement-tracker", "source_commit": "a" * 40},
            )
            self.assertEqual(manifest["workflows"]["directory"], "workflows")
            self.assertEqual(len(manifest["workflows"]["files"]), 19)
            self.assertEqual(manifest["workflows"]["files"], sorted(manifest["workflows"]["files"]))
            self.assertTrue(manifest["workflows"]["inactive"])
            self.assertFalse(manifest["workflows"]["published"])
            self.assertEqual(manifest["bootstrap"]["workflow_id"], "10000000-0000-4000-8000-000000000019")
            self.assertEqual(len(manifest["bootstrap"]["tables"]), 4)
            self.assertEqual(
                {row["name"] for row in manifest["bootstrap"]["tables"]},
                TARGET_TABLE_NAMES,
            )
            self.assertEqual(manifest["fixtures"], {"directory": "fixtures", "manifest": "fixtures/fixture-manifest.json"})
            self.assertEqual(
                manifest["credentials"]["binding_contract"],
                {
                    "path": "credential-bindings.json",
                    "sha256": sha256(N8N / "credential-bindings.json"),
                },
            )
            self.assertFalse(manifest["credentials"]["values_included"])
            self.assertEqual(
                {row["binding"] for row in manifest["credentials"]["placeholders"]},
                {row["placeholder"] for row in load_json(N8N / "credential-bindings.json")["bindings"]},
            )
            self.assertEqual(
                manifest["route"],
                {
                    "path": "/mcp/finance-operations-v1",
                    "edge_auth": "CLOUDFLARE_ACCESS_SERVICE_AUTH",
                    "origin_auth": "APPLICATION_SUPPLIED_BEARER",
                    "enabled": False,
                },
            )
            self.assertEqual(path.parent, Path(temporary) / "application")

    def test_staged_manifest_is_self_contained_and_read_only_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path, manifest = self.stage(temporary)
            root = path.parent.resolve()
            relative_files = [
                *[Path("workflows") / name for name in manifest["workflows"]["files"]],
                Path(manifest["folders"]["manifest"]),
                Path(manifest["folders"]["sql"]),
                Path(manifest["bootstrap"]["directory"]),
                Path(manifest["bootstrap"]["directory"]) / manifest["bootstrap"]["sql"],
                Path(manifest["fixtures"]["manifest"]),
                Path(manifest["credentials"]["binding_contract"]["path"]),
            ]
            for relative in relative_files:
                resolved = (root / relative).resolve()
                self.assertTrue(resolved == root or root in resolved.parents, relative)
                self.assertFalse((root / relative).is_symlink(), relative)
                self.assertTrue(resolved.exists(), relative)
            self.assertTrue((root / "bootstrap").is_dir())
            self.assertTrue((root / "fixtures").is_dir())
            placement = (root / manifest["folders"]["sql"]).read_text(encoding="utf-8")
            self.assertIn("application_project_id", placement)
            self.assertNotIn("finance_project_id", placement)
            self.assertFalse((root / "workflow-organization-cutover.sql").exists())
            self.assertNotIn("finance_commit", manifest)
            self.assertNotIn("extension_image", manifest)

    def test_staged_credential_contract_is_exact_and_stable(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            _, first_manifest = self.stage(first)
            _, second_manifest = self.stage(second)
            first_root = Path(first) / "application"
            second_root = Path(second) / "application"
            self.assertEqual(
                (first_root / "credential-bindings.json").read_bytes(),
                (N8N / "credential-bindings.json").read_bytes(),
            )
            first_files = sorted(path.relative_to(first_root) for path in first_root.rglob("*") if path.is_file())
            second_files = sorted(path.relative_to(second_root) for path in second_root.rglob("*") if path.is_file())
            self.assertEqual(first_files, second_files)
            self.assertEqual(
                [path.read_bytes() for path in sorted((first_root / "workflows").glob("*.json"))],
                [path.read_bytes() for path in sorted((second_root / "workflows").glob("*.json"))],
            )
            self.assertEqual(first_manifest, second_manifest)

    def test_adapter_rejects_stale_credential_contract_declaration(self) -> None:
        original_sha256 = self.adapter._sha256

        def stale_contract(path: Path) -> str:
            if path.name == "credential-bindings.json":
                return "0" * 64
            return original_sha256(path)

        with tempfile.TemporaryDirectory() as temporary, patch.object(self.adapter, "_sha256", side_effect=stale_contract):
            with self.assertRaisesRegex(ValueError, "credential binding declaration is stale"):
                self.adapter.stage_application(ROOT, Path(temporary) / "application", "a" * 40)

    def test_workflow_count_and_folder_contract_are_exact(self) -> None:
        registry_rows = self.registry["workflows"]
        self.assertEqual(len(registry_rows), 19)
        registry_codes = {row["code"] for row in registry_rows}
        folder_rows = self.folders["workflows"]
        mapped_codes = [row["code"] for row in folder_rows]
        self.assertEqual(len(mapped_codes), 19)
        self.assertEqual(len(mapped_codes), len(set(mapped_codes)))
        self.assertEqual(set(mapped_codes), registry_codes)
        folders_by_id = {folder["id"]: folder for folder in self.folders["folders"]}
        by_code = {row["code"]: folders_by_id[row["folder_id"]] for row in folder_rows}
        for row in registry_rows:
            workflow = load_json(WORKFLOWS / row["file"])
            code = workflow["meta"]["financeWorkflowCode"]
            self.assertEqual(code, row["code"])
            self.assertEqual(workflow["active"], False)
            self.assertEqual(by_code[code]["id"], workflow["meta"]["workflowFolder"]["id"])
        placement = (N8N / "workflow-folder-placement.sql").read_text(encoding="utf-8")
        for marker in (
            "application_project_id",
            "shared_workflow",
            "WORKFLOW_FOLDER_MAP_COUNT_MISMATCH",
            "WORKFLOW_FOLDER_READBACK_MISMATCH",
            "WORKFLOW_ACTIVATION_VERSION_CHANGED",
        ):
            self.assertIn(marker, placement)
        self.assertNotIn("finance_project_id", placement)
        self.assertNotIn("finance_commit", placement)

    def test_finance_application_manifest_binds_the_generic_input_corpus(self) -> None:
        manifest = load_json(N8N / "application-manifest.json")
        self.assertEqual(manifest["application"], {"name": "finance-statement-tracker", "repository": "srobroek/finance-statement-tracker"})
        self.assertEqual(manifest["contract_status"], "SPEC_ONLY")
        self.assertIsNone(manifest["finance_commit"])
        self.assertEqual(manifest["inactive_corpus"]["file_count"], 19)
        self.assertEqual(manifest["workflow_manifest"]["path"], "integrations/n8n/pipeline-registry.json")
        self.assertEqual(manifest["fixture_manifest"]["path"], "integrations/n8n/disposable/fixture-manifest.json")
        self.assertEqual(manifest["workflow_manifest"]["sha256"], sha256(N8N / "pipeline-registry.json"))
        self.assertEqual(manifest["fixture_manifest"]["sha256"], sha256(N8N / "disposable/fixture-manifest.json"))
        with tempfile.TemporaryDirectory() as temporary:
            _, staged = self.stage(temporary)
            self.assertEqual(len(staged["workflows"]["files"]), manifest["inactive_corpus"]["file_count"])
            self.assertEqual(load_json(Path(temporary) / "application" / "fixtures/fixture-manifest.json"), self.fixture_manifest)

    def test_data_table_schema_and_adapter_projection_are_exact(self) -> None:
        errors = sorted(
            Draft202012Validator(self.table_schema, format_checker=FormatChecker()).iter_errors(self.tables),
            key=str,
        )
        self.assertEqual(errors, [], "; ".join(error.message for error in errors))
        self.assertEqual(self.tables["storage"], "n8n-data-tables-on-postgres")
        self.assertEqual(len(self.tables["tables"]), 15)
        self.assertEqual({row["name"] for row in self.tables["tables"]}, TABLE_NAMES)
        self.assertEqual(
            {row["name"] for row in self.tables["tables"]},
            {row["name"] for row in self.tables["tables"] if row["name"].startswith("finance_")},
        )
        with tempfile.TemporaryDirectory() as temporary:
            _, manifest = self.stage(temporary)
            self.assertEqual(
                {row["name"] for row in manifest["bootstrap"]["tables"]},
                TARGET_TABLE_NAMES,
            )

    def test_bootstrap_targets_are_derived_from_the_canonical_matrix(self) -> None:
        self.assertEqual(set(self.matrix["targets"]), TARGET_TABLE_NAMES)
        self.assertEqual(set(self.matrix["target_schemas"]), TARGET_TABLE_NAMES)
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            _, first_manifest = self.stage(first)
            _, second_manifest = self.stage(second)
            self.assertEqual(first_manifest["bootstrap"], second_manifest["bootstrap"])
            self.assertEqual(
                first_manifest["bootstrap"]["tables"],
                [{"name": name} for name in self.matrix["targets"]],
            )

    def test_onedrive_root_and_folder_fixture_contract_stays_finance_owned(self) -> None:
        setup_root = N8N / "setup-workflows"
        workflow = load_json(setup_root / "22-onedrive-finance-evidence-root-setup.json")
        setup_manifest = load_json(setup_root / "manifest.json")
        regular_codes = {row["code"] for row in self.registry["workflows"]}
        self.assertNotIn("ONEDRIVE_FINANCE_EVIDENCE_ROOT_SETUP", regular_codes)
        self.assertFalse(workflow["active"])
        self.assertTrue(workflow["meta"]["manualOnly"])
        self.assertTrue(workflow["meta"]["setupOnly"])
        self.assertTrue(workflow["meta"]["activationForbidden"])
        self.assertEqual(set(setup_manifest), {"schema_version", "n8n_version", "contract_status", "import_policy", "activation_forbidden", "workflows"})
        assignments = {
            row["name"]: row["value"]
            for row in next(node for node in workflow["nodes"] if node["name"] == "Setup Parameters")["parameters"]["assignments"]["assignments"]
        }
        self.assertEqual(assignments["root_folder_name"], "Finance Evidence")
        self.assertEqual(assignments["expected_parent_scope"], "root")
        self.assertTrue(assignments["create_if_absent"])
        node_types = {node["type"] for node in workflow["nodes"]}
        self.assertNotIn("n8n-nodes-base.httpRequest", node_types)
        self.assertNotIn("n8n-nodes-base.executeCommand", node_types)
        self.assertFalse(any(node_type.startswith("n8n-nodes-finance.") for node_type in node_types))
        code = "\n".join(node["parameters"].get("jsCode", "") for node in workflow["nodes"])
        for marker in (
            "ONEDRIVE_ROOT_NAME_OCCUPIED_BY_NON_FOLDER",
            "ONEDRIVE_ROOT_FOLDER_CASE_MISMATCH",
            "ONEDRIVE_ROOT_READBACK_EXACT_COUNT_MISMATCH",
            "ONEDRIVE_NESTED_FINANCE_EVIDENCE_DUPLICATION_DETECTED",
            "folder_id_redacted: true",
            "production_workflows_activated: false",
        ):
            self.assertIn(marker, code)

    def test_disposable_fixture_manifest_binds_exact_integration_inputs(self) -> None:
        fixture = self.fixture_manifest
        self.assertEqual(fixture["contract_status"], "DISPOSABLE_ONLY")
        self.assertTrue(fixture["production_import_forbidden"])
        self.assertEqual(fixture["required_acknowledgement"], "DISPOSABLE_ONLY")
        self.assertEqual(len(fixture["workflows"]), 18)
        for filename, digest in fixture["source_workflow_sha256"].items():
            self.assertEqual(digest, sha256(WORKFLOWS / filename))
        self.assertEqual(fixture["scenario_contract"]["sweep_zero"]["expected"], {"scanned_count": 0, "heartbeat": True})
        self.assertEqual(
            fixture["scenario_contract"]["sweep_101"]["expected"],
            {"scanned_count": 101, "matched_count": 101, "attachment_identity_keys": []},
        )
        with tempfile.TemporaryDirectory() as temporary:
            _, manifest = self.stage(temporary)
            staged_root = Path(temporary) / "application"
            staged_fixture = load_json(staged_root / manifest["fixtures"]["manifest"])
            self.assertEqual(staged_fixture, fixture)
            self.assertEqual(
                sha256(staged_root / manifest["fixtures"]["manifest"]),
                sha256(N8N / "disposable/fixture-manifest.json"),
            )
            for entry in fixture["workflows"]:
                staged_workflow = staged_root / "fixtures" / "generated" / entry["file"]
                self.assertEqual(sha256(staged_workflow), entry["sha256"])
            self.assertEqual(
                sha256(staged_root / "fixtures" / "onedrive-root-setup.json"),
                sha256(N8N / "setup-workflows/22-onedrive-finance-evidence-root-setup.json"),
            )
            self.assertEqual(
                sha256(staged_root / "bootstrap" / "data-tables.json"),
                sha256(N8N / "data-tables.json"),
            )

    def test_adapter_rejects_unpinned_source_commits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(ValueError, "source commit"):
            self.adapter.stage_application(ROOT, Path(temporary) / "application", "finance")

    def test_adapter_rejects_fixture_hash_drift(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(self.adapter, "_sha256", return_value="0" * 64),
            self.assertRaisesRegex(ValueError, "fixture workflow hash mismatch"),
        ):
            self.adapter.stage_application(ROOT, Path(temporary) / "application", "a" * 40)


if __name__ == "__main__":
    unittest.main()
