from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
N8N = ROOT / "integrations" / "n8n"
SOURCE = N8N / "organize-workflows.py"
SQL_GENERATOR_SOURCE = N8N / "generate_workflow_folder_sql.py"


def load_organizer():
    spec = importlib.util.spec_from_file_location("finance_workflow_organizer", SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {SOURCE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_sql_generator():
    spec = importlib.util.spec_from_file_location("finance_workflow_sql_generator", SQL_GENERATOR_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {SQL_GENERATOR_SOURCE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_state(organizer):
    workflows = []
    edges = []
    for spec in organizer.WORKFLOW_MAP:
        active = spec["id"] == organizer.W15_ID
        workflow_id = (
            organizer.ORPHAN_WORKFLOW_ID
            if spec["id"] == organizer.CANONICAL_REPLACEMENT_ID
            else spec["id"]
        )
        workflow_name = (
            organizer.ORPHAN_WORKFLOW_NAME
            if workflow_id == organizer.ORPHAN_WORKFLOW_ID
            else spec["current_name"]
        )
        workflows.append(
            {
                "id": workflow_id,
                "name": workflow_name,
                "active": active,
                "activeVersionId": organizer.W15_ACTIVE_VERSION if active else None,
                "parentFolderId": "f1000000-0000-4000-8000-000000000001",
                "nodes": [
                    {"id": f"node-{spec['id']}", "credentials": {"opaque": "preserve"}}
                ],
                "connections": {"main": []},
                "settings": {"executionOrder": "v1"},
            }
        )
        edges.extend(
            {"workflowId": workflow_id, "tagId": organizer.TAG_IDS[tag]}
            for tag in ("finance", "setup-required", "inactive")
        )
    return {
        "projectId": "project-fixture",
        "workflows": workflows,
        "folders": [
            {
                "id": folder_id,
                "name": f"Legacy {index}",
                "parentFolderId": None,
                "projectId": "project-fixture",
            }
            for index, folder_id in enumerate(sorted(organizer.LEGACY_FOLDER_IDS))
        ],
        "tags": [
            {"id": tag_id, "name": name}
            for name, tag_id in organizer.TAG_IDS.items()
            if name != "active"
        ],
        "workflow_tags": edges,
    }


class WorkflowOrganizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.organizer = load_organizer()

    def test_contract_has_exact_two_roots_four_children_and_19_rows(self):
        o = self.organizer
        self.assertEqual(len(o.WORKFLOW_MAP), 19)
        self.assertEqual(len({row["id"] for row in o.WORKFLOW_MAP}), 19)
        self.assertIn(o.CANONICAL_REPLACEMENT_ID, {row["id"] for row in o.WORKFLOW_MAP})
        self.assertNotIn(o.ORPHAN_WORKFLOW_ID, {row["id"] for row in o.WORKFLOW_MAP})
        self.assertEqual(len(o.FOLDER_SPECS), 6)
        self.assertEqual(sum(row["root"] for row in o.FOLDER_SPECS), 2)
        self.assertEqual(sum(not row["root"] for row in o.FOLDER_SPECS), 4)
        self.assertEqual(
            o.WORKFLOW_BY_ID["10000000-0000-4000-8000-000000000019"]["folder_id"],
            "f1000000-0000-4000-8000-000000000103",
        )
        self.assertEqual(
            o.WORKFLOW_BY_ID["10000000-0000-4000-8000-000000000024"]["target_name"],
            "Shared Monthly Statement Cycle",
        )
        for row in o.WORKFLOW_MAP:
            lowered = row["target_name"].casefold()
            self.assertFalse(
                any(marker in lowered for marker in o.STATUS_MARKERS), row["id"]
            )
        canonical = o.WORKFLOW_BY_ID[o.CANONICAL_REPLACEMENT_ID]
        self.assertEqual(
            canonical["source"],
            "integrations/n8n/workflows/22-shared-monthly-statement-cycle.json",
        )
        self.assertEqual(canonical["code"], "SHARED_MONTHLY_STATEMENT_CYCLE")

    def test_owned_contracts_use_canonical_ai_review_table_name(self):
        root = N8N
        owned = [
            root / "organize-workflows.py",
            root / "workflow-folder-placement.sql",
            root / "workflow-folders.json",
            root / "application-manifest.json",
            root / "workflows" / "22-shared-monthly-statement-cycle.json",
            *sorted((root / "generated").glob("*.json")),
        ]
        for path in owned:
            self.assertNotIn("finance_ai_review_queue", path.read_text(encoding="utf-8"), path)

    def test_apply_preserves_opaque_rows_and_active_published_tuple(self):
        o = self.organizer
        before = fixture_state(o)
        after = o.apply_plan(before)
        self.assertEqual(len(after["workflows"]), 19)
        self.assertNotIn(o.ORPHAN_WORKFLOW_ID, {row["id"] for row in after["workflows"]})
        self.assertIn(o.CANONICAL_REPLACEMENT_ID, {row["id"] for row in after["workflows"]})
        self.assertEqual(sum(row["active"] for row in after["workflows"]), 1)
        w15 = next(row for row in after["workflows"] if row["id"] == o.W15_ID)
        self.assertEqual(w15["activeVersionId"], o.W15_ACTIVE_VERSION)
        before_w15 = next(
            row for row in before["workflows"] if row["id"] == o.W15_ID
        )
        self.assertEqual(w15["nodes"], before_w15["nodes"])
        self.assertEqual(w15["connections"], before_w15["connections"])
        self.assertTrue(
            all(
                row["name"] == o.WORKFLOW_BY_ID[row["id"]]["target_name"]
                for row in after["workflows"]
            )
        )
        canonical = next(
            row for row in after["workflows"] if row["id"] == o.CANONICAL_REPLACEMENT_ID
        )
        source = o.canonical_workflow_export()
        self.assertEqual(canonical["nodes"], source["nodes"])
        self.assertEqual(canonical["connections"], source["connections"])
        self.assertEqual(len(canonical["nodes"]), 16)
        self.assertNotEqual(
            canonical["nodes"][0]["id"],
            "node-10000000-0000-4000-8000-000000000024",
        )
        self.assertEqual(
            {row["id"] for row in after["folders"]},
            {row["id"] for row in o.FOLDER_SPECS},
        )

    def test_canonical_export_identity_is_checked_and_reported(self):
        o = self.organizer
        source = o.canonical_workflow_export()
        self.assertEqual(source["id"], o.CANONICAL_REPLACEMENT_ID)
        self.assertEqual(
            source["meta"]["financeWorkflowCode"], "SHARED_MONTHLY_STATEMENT_CYCLE"
        )
        self.assertEqual(len(source["nodes"]), 16)
        self.assertEqual(
            hashlib.sha256(o.CANONICAL_EXPORT_PATH.read_bytes()).hexdigest(),
            o.CANONICAL_EXPORT_SHA256,
        )
        self.assertEqual(
            o.persisted_workflow_body_md5(source), o.CANONICAL_PERSISTED_BODY_MD5
        )
        renamed = copy.deepcopy(source)
        renamed["name"] = "Shared Monthly Statement Cycle"
        self.assertEqual(
            o.persisted_workflow_body_md5(renamed), o.CANONICAL_PERSISTED_BODY_MD5
        )

    def test_tag_transition_is_exactly_18_inactive_and_one_active(self):
        o = self.organizer
        after = o.apply_plan(fixture_state(o))
        edges = after["workflow_tags"]
        inactive = [edge for edge in edges if edge["tagId"] == o.TAG_IDS["inactive"]]
        active = [edge for edge in edges if edge["tagId"] == o.TAG_IDS["active"]]
        self.assertEqual(len(inactive), 18)
        self.assertEqual(len(active), 1)
        self.assertEqual(
            active[0], {"workflowId": o.W15_ID, "tagId": o.TAG_IDS["active"]}
        )
        self.assertNotIn(
            {"workflowId": o.W15_ID, "tagId": o.TAG_IDS["inactive"]}, edges
        )
        self.assertEqual(
            next(tag for tag in after["tags"] if tag["id"] == o.TAG_IDS["inactive"])[
                "name"
            ],
            "inactive",
        )
        self.assertEqual(
            sum(edge["tagId"] == o.TAG_IDS["finance"] for edge in edges), 19
        )
        self.assertEqual(
            sum(edge["tagId"] == o.TAG_IDS["setup-required"] for edge in edges), 19
        )

    def test_missing_required_edges_are_repaired_without_touching_other_tags(self):
        o = self.organizer
        state = fixture_state(o)
        state["workflow_tags"] = [
            edge
            for edge in state["workflow_tags"]
            if edge["tagId"] not in {o.TAG_IDS["finance"], o.TAG_IDS["setup-required"]}
        ]
        state["tags"].append({"id": "other", "name": "operator-note"})
        state["workflow_tags"].append({"workflowId": o.W15_ID, "tagId": "other"})
        after = o.apply_plan(state)
        edges = after["workflow_tags"]
        self.assertEqual(
            sum(edge["tagId"] == o.TAG_IDS["finance"] for edge in edges), 19
        )
        self.assertEqual(
            sum(edge["tagId"] == o.TAG_IDS["setup-required"] for edge in edges), 19
        )
        self.assertIn({"workflowId": o.W15_ID, "tagId": "other"}, edges)

    def test_plan_is_idempotent_and_rollback_restores_exact_full_state(self):
        o = self.organizer
        before = fixture_state(o)
        plan = o.plan_organization(before)
        self.assertTrue(plan["changed"])
        self.assertTrue(plan["idempotent"])
        self.assertEqual(plan["prestate_roster"], "orphaned")
        self.assertTrue(plan["retirement"]["backup_captured"])
        self.assertEqual(
            plan["retirement"]["legacy_workflow_id"], o.ORPHAN_WORKFLOW_ID
        )
        self.assertEqual(
            plan["retirement"]["replacement_workflow_id"], o.CANONICAL_REPLACEMENT_ID
        )
        self.assertEqual(
            plan["retirement"]["canonical_source_path"],
            o.CANONICAL_EXPORT_RELATIVE_PATH,
        )
        self.assertEqual(
            plan["retirement"]["canonical_source_sha256"],
            o.CANONICAL_EXPORT_SHA256,
        )
        self.assertEqual(
            plan["retirement"]["canonical_body_md5"],
            o.CANONICAL_PERSISTED_BODY_MD5,
        )
        self.assertEqual(plan["retirement"]["canonical_node_count"], 16)
        self.assertTrue(plan["retirement"]["canonical_source_bound"])
        self.assertTrue(plan["retirement"]["rollback_restores_exact_prestate"])
        second = o.plan_organization(plan["after_state"])
        self.assertFalse(second["changed"])
        self.assertEqual(second["prestate_roster"], "canonical")
        self.assertFalse(second["retirement"]["backup_captured"])
        self.assertEqual(second["before"], second["after"])
        restored = o.rollback_state(plan["after_state"], plan["rollback_state"])
        self.assertEqual(restored, before)
        self.assertEqual(o.snapshot_summary(restored), plan["before"])
        self.assertNotEqual(
            plan["before"]["full_row_md5"], plan["after"]["full_row_md5"]
        )

    def test_snapshot_summary_exposes_version_and_digest_receipts_without_payloads(
        self,
    ):
        o = self.organizer
        summary = o.snapshot_summary(fixture_state(o))
        self.assertEqual(summary["workflow_count"], 19)
        self.assertEqual(summary["active_count"], 1)
        self.assertEqual(summary["published_count"], 1)
        self.assertEqual(summary["inactive_edge_count"], 19)
        self.assertEqual(summary["active_edge_count"], 0)
        self.assertRegex(summary["full_row_md5"], r"^[0-9a-f]{32}$")
        self.assertRegex(summary["logical_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("opaque", json.dumps(summary))

    def test_sql_is_guarded_rehearsal_and_contains_exact_target_contract(self):
        sql = (N8N / "workflow-folder-placement.sql").read_text(encoding="utf-8")
        self.assertIn("\\set finance_commit false", sql)
        self.assertIn("\\if :finance_commit", sql)
        self.assertIn("ROLLBACK", sql)
        self.assertIn("ORGANIZATION_REHEARSAL_ROLLED_BACK", sql)
        self.assertIn("WORKFLOW_CONTRACT_COUNT_MISMATCH", sql)
        self.assertIn("TAG_EDGE_READBACK_MISMATCH", sql)
        self.assertIn("VERSION_TUPLE_READBACK_MISMATCH", sql)
        self.assertIn("LEGACY_FOLDER_REMAINS", sql)
        self.assertIn("000000000024", sql)
        self.assertIn("000000000115", sql)
        self.assertIn("ORPHAN_WORKFLOW_REMAINS", sql)
        self.assertIn("RETIREMENT_BACKUP", sql)
        self.assertIn("^[A-Za-z0-9_-]{8,64}$", sql)
        for row in self.organizer.WORKFLOW_MAP:
            self.assertIn(row["id"], sql)
            self.assertIn(row["target_name"], sql)
            self.assertIn(row["folder_id"], sql)

    def test_sql_is_byte_identical_to_canonical_contract_renderer(self):
        generator = load_sql_generator()
        sql = (N8N / "workflow-folder-placement.sql").read_text(encoding="utf-8")
        self.assertEqual(generator.render(), sql)
        completed = subprocess.run(
            [sys.executable, str(SQL_GENERATOR_SOURCE), "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("{{", sql)

    def test_sql_do_blocks_read_context_instead_of_using_psql_variables(self):
        sql = (N8N / "workflow-folder-placement.sql").read_text(encoding="utf-8")
        blocks = re.findall(r"DO \$\$.*?END \$\$;", sql, flags=re.DOTALL)
        self.assertGreaterEqual(len(blocks), 4)
        for block in blocks:
            self.assertNotIn(":'finance_project_id'", block)
        self.assertGreaterEqual(
            sum("finance_organization_context" in block for block in blocks), 3
        )

    def test_sql_cutover_contract_has_transactional_guards(self):
        o = self.organizer
        sql = (N8N / "workflow-folder-placement.sql").read_text(encoding="utf-8")
        for marker in (
            "FOR UPDATE",
            "RETURNING",
            "finance_canonical_source_contract",
            o.CANONICAL_EXPORT_SHA256,
            o.CANONICAL_PERSISTED_BODY_MD5,
            "expected_body_md5",
            "CANONICAL_EXPORT_BODY_DIGEST_MISMATCH",
            "CANONICAL_EXPORT_PRECONDITION_FAILED",
            "ORPHAN_TAG_DELETE_COUNT_MISMATCH",
            "ORPHAN_SHARED_DELETE_COUNT_MISMATCH",
            "ORPHAN_WORKFLOW_DELETE_COUNT_MISMATCH",
        ):
            self.assertIn(marker, sql)
    def test_sql_rehearsal_runs_against_postgres_when_configured(self):
        dsn = os.environ.get("FINANCE_WORKFLOW_SQL_DSN")
        project_id = os.environ.get("FINANCE_WORKFLOW_PROJECT_ID")
        if not dsn or not project_id or shutil.which("psql") is None:
            self.skipTest("set FINANCE_WORKFLOW_SQL_DSN and FINANCE_WORKFLOW_PROJECT_ID for integration")
        completed = subprocess.run(
            [
                "psql",
                dsn,
                "--set",
                f"finance_project_id={project_id}",
                "--set",
                "finance_commit=false",
                "--file",
                str(N8N / "workflow-folder-placement.sql"),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("ORGANIZATION_REHEARSAL_ROLLED_BACK", completed.stdout)

    def test_cli_contract_and_rehearsal_are_side_effect_free(self):
        contract = subprocess.run(
            [sys.executable, str(SOURCE)], text=True, capture_output=True, check=True
        )
        payload = json.loads(contract.stdout)
        self.assertEqual(len(payload["workflows"]), 19)
        with self.subTest("rehearsal"):
            import tempfile

            with tempfile.TemporaryDirectory() as temporary:
                state_path = Path(temporary) / "state.json"
                state_path.write_text(
                    json.dumps(fixture_state(self.organizer)), encoding="utf-8"
                )
                result = subprocess.run(
                    [sys.executable, str(SOURCE), "--state", str(state_path)],
                    text=True,
                    capture_output=True,
                    check=True,
                )
                report = json.loads(result.stdout)
                self.assertTrue(report["changed"])
                self.assertTrue(report["idempotent"])
                self.assertFalse(report["production_mutation"])
                self.assertEqual(report["prestate_roster"], "orphaned")
                self.assertEqual(
                    report["retirement"]["replacement_workflow_id"],
                    self.organizer.CANONICAL_REPLACEMENT_ID,
                )
                self.assertEqual(
                    state_path.read_text(encoding="utf-8"),
                    json.dumps(fixture_state(self.organizer), indent=None),
                )


if __name__ == "__main__":
    unittest.main()
