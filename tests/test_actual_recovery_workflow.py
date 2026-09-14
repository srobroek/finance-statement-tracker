from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = (
    ROOT / "integrations" / "n8n" / "workflows" / "17-actual-outbox-recovery.json"
)

APPLY_WORKFLOW_PATH = (
    ROOT / "integrations" / "n8n" / "workflows" / "20-actual-outbox-apply.json"
)

SQL_PATH = ROOT / "integrations" / "n8n" / "postgres" / "001-finance-writer-lease.sql"
LEASE_WORKFLOW_PATH = (
    ROOT / "integrations" / "n8n" / "workflows" / "18-finance-writer-lease.json"
)


class ActualRecoveryWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow: dict[str, Any] = json.loads(
            WORKFLOW_PATH.read_text(encoding="utf-8")
        )
        cls.nodes = {node["name"]: node for node in cls.workflow["nodes"]}
        cls.connections = cls.workflow["connections"]
        cls.apply_workflow: dict[str, Any] = json.loads(
            APPLY_WORKFLOW_PATH.read_text(encoding="utf-8")
        )
        cls.apply_nodes = {node["name"]: node for node in cls.apply_workflow["nodes"]}
        cls.apply_connections = cls.apply_workflow["connections"]
        cls.lease_workflow: dict[str, Any] = json.loads(
            LEASE_WORKFLOW_PATH.read_text(encoding="utf-8")
        )

    def dispatch_poll(
        self, rows: list[dict[str, Any]]
    ) -> list[tuple[str, dict[str, Any]]]:
        """Model the guarded output contract without running n8n."""
        # Data Table's empty-output normalization supplies one empty item to the
        # guard. Every real row has a batch_id and is passed through unchanged.
        inputs = rows or [{}]
        return [
            ("writer", row)
            if row.get("batch_id")
            else (
                "noop",
                {"status": "NOOP", "reason": "NO_NONTERMINAL_ACTUAL_OUTBOX_ROWS"},
            )
            for row in inputs
        ]

    def test_empty_scheduled_poll_is_explicit_noop_success(self) -> None:
        reader = self.nodes["Read Nonterminal Actual Outbox"]
        guard = self.nodes["Has Nonterminal Actual Outbox Rows"]
        noop = self.nodes["No Nonterminal Actual Outbox Rows"]
        writer_name = "Apply Nonterminal Outbox Safely"

        self.assertTrue(reader["alwaysOutputData"])
        self.assertEqual(
            self.connections[reader["name"]]["main"][0][0]["node"],
            guard["name"],
        )
        self.assertEqual(
            self.connections[guard["name"]]["main"][0][0]["node"],
            writer_name,
        )
        self.assertEqual(
            self.connections[guard["name"]]["main"][1][0]["node"],
            noop["name"],
        )
        self.assertNotIn(
            "delta_artifact_item_id",
            guard["parameters"]["conditions"]["conditions"][0]["leftValue"],
        )
        self.assertNotIn("delta_artifact_item_id", noop["parameters"]["jsCode"])

        outcomes = self.dispatch_poll([])
        self.assertEqual(
            outcomes,
            [
                (
                    "noop",
                    {
                        "status": "NOOP",
                        "reason": "NO_NONTERMINAL_ACTUAL_OUTBOX_ROWS",
                    },
                )
            ],
        )
        self.assertEqual([kind for kind, _ in outcomes].count("writer"), 0)

    def test_nonempty_poll_invokes_writer_once_with_complete_ids(self) -> None:
        row = {
            "batch_id": "statement:delta-1",
            "run_id": "run-1",
            "actual_file_id": "actual-file-1",
            "delta_sha256": "a" * 64,
            "delta_artifact_item_id": "onedrive-item-1",
            "delta_artifact_etag": "etag-1",
            "delta_schema_version": "statement-delta-v1",
            "config_version": "config-v1",
            "parser_version": "parser-v1",
            "state": "PREPARED",
            "attempt_count": 0,
        }

        outcomes = self.dispatch_poll([row])
        writer_calls = [payload for kind, payload in outcomes if kind == "writer"]

        self.assertEqual(len(writer_calls), 1)
        self.assertEqual(writer_calls[0], row)
        required_ids = {
            "batch_id",
            "actual_file_id",
            "delta_sha256",
            "delta_artifact_item_id",
            "delta_artifact_etag",
            "delta_schema_version",
            "config_version",
        }
        self.assertTrue(required_ids <= set(writer_calls[0]))
        self.assertEqual(
            self.nodes["Apply Nonterminal Outbox Safely"]["parameters"]["workflowId"][
                "value"
            ],
            "10000000-0000-4000-8000-000000000020",
        )

    def test_recovery_workflows_are_explicitly_spec_only(self) -> None:
        self.assertEqual(
            self.workflow["meta"]["financeWorkflowCode"], "ACTUAL_OUTBOX_RECOVERY"
        )
        self.assertEqual(self.workflow["meta"]["migrationStatus"], "SPEC_ONLY")
        self.assertEqual(
            self.apply_workflow["meta"]["financeWorkflowCode"],
            "ACTUAL_OUTBOX_APPLY",
        )
        self.assertEqual(self.apply_workflow["meta"]["migrationStatus"], "SPEC_ONLY")
        self.assertEqual(
            self.lease_workflow["meta"]["financeWorkflowCode"], "FINANCE_WRITER_LEASE"
        )
        self.assertEqual(self.lease_workflow["meta"]["migrationStatus"], "SPEC_ONLY")

        replay = self.apply_nodes["Return Verified Commit Receipt Replay"][
            "parameters"
        ]["jsCode"]
        self.assertIn("Read Back COMMITTED Recovery Replay", replay)
        self.assertIn("Read Back Exact Actual Verification Receipt Replay", replay)
        self.assertIn("replay_readback_only: true", replay)

    def test_issuance_preserves_envelope_and_routes_error_output(self) -> None:
        issued = self.apply_nodes["Record ISSUED Before Actual Mutation"]
        preserve = self.apply_nodes["Preserve ISSUED Envelope for Actual Mutation"]
        importer = self.apply_nodes["Recovery Import PREPARED"]
        issued_out = self.apply_connections[issued["name"]]["main"][0][0]["node"]
        preserve_out = self.apply_connections[preserve["name"]]["main"][0][0]["node"]
        import_outputs = self.apply_connections[importer["name"]]["main"]
        self.assertEqual(issued_out, preserve["name"])
        self.assertEqual(preserve_out, importer["name"])
        self.assertEqual(
            import_outputs[1][0]["node"], "Persist OUTCOME_UNKNOWN on Apply Error"
        )
        self.assertNotIn("error", self.apply_connections[importer["name"]])

    def test_acquire_is_atomic_with_issuance_and_ignores_terminal_history(self) -> None:
        lease_sql = self._lease_workflow_sql()
        self.assertIn("WITH blockers AS", lease_sql)
        self.assertIn("budget_id = $8::text", lease_sql)
        self.assertNotIn("bool_and", lease_sql)
        self.assertIn("INSERT INTO finance_ops.actual_writer_effects", lease_sql)
        verified = self.apply_nodes["Record VERIFIED in Durable Writer State"][
            "parameters"
        ]["query"]
        self.assertIn(
            "state = 'VERIFIED' AND verified_payload_sha256 = $5::text", verified
        )

    def test_receipt_dates_accept_native_timestamps_but_reject_malformed_values(
        self,
    ) -> None:
        code = self.apply_nodes["Compare Exact Actual Verification Receipt"][
            "parameters"
        ]["jsCode"]
        self.assertIn("Date.parse", code)
        self.assertIn("Number.isFinite(parsed)", code)
        self.assertIn("toISOString().slice(0, 10)", code)
        self.assertNotIn("observed.period_start !== manifest.period_start", code)

    def test_durable_writer_effects_schema_is_idempotent_and_target_bound(self) -> None:
        sql = SQL_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS finance_ops.actual_writer_effects", sql
        )
        self.assertIn("PRIMARY KEY (resource_key, outbox_id)", sql)
        self.assertIn(
            "payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$')",
            sql,
        )
        self.assertIn("verified_payload_sha256 text CHECK", sql)
        self.assertIn(
            "state IN ('PREPARED', 'ISSUED', 'ACTUAL_OBSERVED', 'OUTCOME_UNKNOWN', 'VERIFIED', 'RECONCILED', 'COMMITTED')",
            sql,
        )
        self.assertIn(
            "CREATE INDEX IF NOT EXISTS actual_writer_effects_admission_idx", sql
        )
        self.assertIn(
            "GRANT SELECT, INSERT, UPDATE ON finance_ops.actual_writer_effects TO n8n",
            sql,
        )

    def test_unresolved_writer_states_are_durable_admission_blockers(self) -> None:
        lease_sql = self._lease_workflow_sql()
        self.assertIn(
            "state IN ('PREPARED', 'ISSUED', 'ACTUAL_OBSERVED', 'OUTCOME_UNKNOWN')",
            lease_sql,
        )
        self.assertIn("budget_id = $8::text", lease_sql)
        self.assertIn(
            "NOT (state = 'PREPARED' AND outbox_id = $4::text AND attempt_count = 0",
            lease_sql,
        )
        self.assertNotIn("bool_and", lease_sql)

    @staticmethod
    def _lease_workflow_sql() -> str:
        lease = (
            ROOT / "integrations" / "n8n" / "workflows" / "18-finance-writer-lease.json"
        )
        workflow = json.loads(lease.read_text(encoding="utf-8"))
        return "\n".join(
            str(node.get("parameters", {}).get("query", ""))
            for node in workflow.get("nodes", [])
            if node.get("type") == "n8n-nodes-base.postgres"
        )


if __name__ == "__main__":
    unittest.main()
