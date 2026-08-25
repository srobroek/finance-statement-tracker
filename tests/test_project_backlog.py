import copy
import json
import unittest
from pathlib import Path

from finance_tracker.project_backlog import (
    IMPLEMENTATION_PATH,
    TRANSCRIPT_PATH,
    load_and_build,
    render_markdown,
    validate_backlog,
)


ROOT = Path(__file__).resolve().parents[1]


class ProjectBacklogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.transcript = json.loads(TRANSCRIPT_PATH.read_text(encoding="utf-8"))
        cls.implementation = json.loads(IMPLEMENTATION_PATH.read_text(encoding="utf-8"))
        cls.payload = load_and_build()

    def test_preserves_every_transcript_id_exactly_once(self) -> None:
        source_ids = [row["id"] for row in self.transcript["requirements"]]
        backlog_ids = [row["id"] for row in self.payload["tasks"]]
        self.assertEqual(set(backlog_ids), set(source_ids))
        self.assertEqual(len(backlog_ids), len(set(backlog_ids)))
        self.assertEqual(len(backlog_ids), 74)

    def test_no_task_is_promoted_to_verified_without_fresh_acceptance(self) -> None:
        self.assertEqual(self.payload["summary"]["verified_count"], 0)
        self.assertNotIn("VERIFIED", {row["status"] for row in self.payload["tasks"]})

    def test_latest_n8n_overrides_are_persistent(self) -> None:
        tasks = {row["id"]: row for row in self.payload["tasks"]}
        self.assertIn("Tidy Workflow", " ".join(tasks["N8N-002"]["contradictions"]))
        self.assertIn("From list", " ".join(tasks["N8N-008"]["contradictions"]))
        agent_text = " ".join(tasks["AGENT-005"]["contradictions"])
        self.assertIn("n8n-nodes-prodex@0.5.1", agent_text)
        self.assertNotIn("claude", agent_text.lower())
        self.assertIn("disposable", tasks["AGENT-005"]["next_action"].lower())

    def test_implementation_audit_is_mapped_to_relevant_requirements(self) -> None:
        tasks = {row["id"]: row for row in self.payload["tasks"]}
        self.assertIn("finance_tracker/transaction_semantics.py", tasks["ACTUAL-010"]["evidence_paths"])
        self.assertIn("config/actual-note-contract.json", tasks["ACTUAL-011"]["evidence_paths"])
        self.assertIn(
            "integrations/n8n/workflows/21-subscription-agent-adapter.json",
            tasks["AGENT-003"]["evidence_paths"],
        )
        self.assertNotIn("CURRENT_TEST_FAILURES", tasks["AGENT-003"]["blockers"])
        self.assertIn("CODEX_SUBSCRIPTION_AUTH_COMPATIBILITY_BLOCKED", tasks["AGENT-003"]["blockers"])

    def test_every_open_task_has_owner_validator_and_acceptance_evidence_state(self) -> None:
        for task in self.payload["tasks"]:
            if task["status"] == "SUPERSEDED":
                continue
            self.assertTrue(task["owner_workstream"], task["id"])
            self.assertTrue(task["acceptance_validator"], task["id"])
            self.assertTrue(task["acceptance_criteria"], task["id"])
            self.assertIn("verification_state", task, task["id"])
            self.assertIn("evidence_paths", task, task["id"])
            self.assertIn("tests", task, task["id"])
            self.assertTrue(task["blockers"], task["id"])

    def test_current_snapshot_is_honest_about_runtime_boundaries(self) -> None:
        tasks = {row["id"]: row for row in self.payload["tasks"]}
        commits = self.payload["progress_overlay"]["evidence_commits"]
        self.assertIn("da6b0c128210b2cd44a7a2c5a120b08e942de9ce", commits)
        self.assertIn("3a6acc625abe99d977d2b225eaae63f8ffe02c65", commits)
        self.assertIn("00491aae2ab43c486f3a9b4a62ce3ba5e63032f6", commits)
        self.assertIn("c8d4f7ce984ec0107846b2c7aa398cb1141caf39", commits)
        self.assertIn("From list", render_markdown(self.payload))
        self.assertIn("EMAIL_TO_PDF_RENDERER_REQUIRED", tasks["N8N-003"]["blockers"])
        self.assertIn("ADCB_ISSUER_BALANCE_CONTRADICTION", tasks["ACTUAL-021"]["blockers"])
        self.assertIn("DUPLICATED_FINANCE_EVIDENCE_PATHS", tasks["DOC-001"]["blockers"])
        self.assertNotIn("RUNTIME_COMMIT_DRIFT_RECONCILIATION_REQUIRED", tasks["PLATFORM-006"]["blockers"])
        self.assertIn("OAUTH_TOKEN_REFRESH_PROOF_REQUIRED", tasks["N8N-003"]["blockers"])
        self.assertNotIn(
            "OAUTH_WORKFLOW_BINDING_AND_READBACK_REQUIRED",
            tasks["N8N-003"]["blockers"],
        )
        self.assertIn(
            "ONEDRIVE_FINANCE_EVIDENCE_ROOT_LIVE_RUN_REQUIRED",
            tasks["DOC-001"]["blockers"],
        )
        self.assertIn("21 workflows", tasks["N8N-006"]["live_readback"])
        self.assertIn("zero active", tasks["N8N-006"]["live_readback"])
        self.assertEqual(tasks["AGENT-005"]["status"], "PARTIAL")

    def test_superseded_notion_requirement_is_not_queued(self) -> None:
        tasks = {row["id"]: row for row in self.payload["tasks"]}
        self.assertEqual(tasks["DOCS-004"]["status"], "SUPERSEDED")
        queued = {row["id"] for row in self.payload["ordered_executable_queue"]}
        self.assertNotIn("DOCS-004", queued)

    def test_validator_rejects_unknown_dependency(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["tasks"][0]["dependencies"].append("ACTUAL-999")
        errors = validate_backlog(payload, {row["id"] for row in self.transcript["requirements"]})
        self.assertTrue(any("unknown dependencies" in error for error in errors))

    def test_validator_rejects_unproven_verified_status(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["tasks"][0]["status"] = "VERIFIED"
        payload["tasks"][0]["evidence_paths"] = []
        payload["tasks"][0]["live_readback"] = ""
        payload["tasks"][0]["last_verified"] = None
        errors = validate_backlog(payload, {row["id"] for row in self.transcript["requirements"]})
        self.assertTrue(any("VERIFIED requires" in error for error in errors))

    def test_render_is_deterministic(self) -> None:
        first = render_markdown(self.payload)
        second = render_markdown(load_and_build())
        self.assertEqual(first, second)

    def test_checked_in_backlog_matches_generator(self) -> None:
        checked_in = json.loads((ROOT / "config" / "project-backlog.json").read_text(encoding="utf-8"))
        self.assertEqual(checked_in, self.payload)

    def test_generated_payload_passes_internal_validator(self) -> None:
        errors = validate_backlog(
            self.payload,
            {row["id"] for row in self.transcript["requirements"]},
        )
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
