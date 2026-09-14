from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
N8N = ROOT / "integrations" / "n8n"
WORKFLOWS = N8N / "workflows"


def workflow(name: str) -> dict:
    return json.loads((WORKFLOWS / name).read_text(encoding="utf-8"))


def nodes(name: str) -> dict[str, dict]:
    return {node["name"]: node for node in workflow(name)["nodes"]}


def next_node(document: dict, source: str, output: int = 0) -> str:
    return document["connections"][source]["main"][output][0]["node"]


def run_code_node(
    name: str, code: str, payload: dict, references: dict[str, dict]
) -> dict:
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("Node.js is required for rendered workflow code")
    script = f"""
const code = {json.dumps(code)};
const input = {json.dumps(payload)};
const references = {json.dumps(references)};
const lookup = name => ({{ first: () => references[name], item: () => references[name] }});
try {{
  const output = new Function('$json', '$', 'require', code)(input, lookup, require);
  process.stdout.write(JSON.stringify({{ok: true, output}}));
}} catch (error) {{
  process.stdout.write(JSON.stringify({{ok: false, error: String(error.message || error)}}));
}}
"""
    result = subprocess.run(
        [node, "-e", script], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class ActualRecoveryWorkflowTests(unittest.TestCase):
    def test_w17_recovers_incomplete_and_committed_actual_outbox_states(self) -> None:
        document = workflow("17-actual-outbox-recovery.json")
        read = nodes("17-actual-outbox-recovery.json")["Read Nonterminal Actual Outbox"]
        states = {
            condition["keyValue"]
            for condition in read["parameters"]["filters"]["conditions"]
        }
        self.assertEqual(
            states, {"PREPARED", "ACTUAL_OBSERVED", "VERIFIED", "COMMITTED"}
        )
        self.assertEqual(document["meta"]["releasedStateExcluded"], "RELEASED")
        self.assertNotIn("RELEASED", states)
        self.assertEqual(read["typeVersion"], 1.1)
        self.assertEqual(
            next_node(document, "Every 10 Minutes"), "Read Nonterminal Actual Outbox"
        )
        self.assertEqual(
            next_node(document, "Read Nonterminal Actual Outbox"),
            "Has Nonterminal Actual Outbox Rows",
        )
        self.assertEqual(
            next_node(document, "Has Nonterminal Actual Outbox Rows"),
            "Apply Nonterminal Outbox Safely",
        )
        self.assertEqual(
            document["meta"]["financeWorkflowCode"], "ACTUAL_OUTBOX_RECOVERY"
        )
        self.assertFalse(document["active"])

    def test_w18_lease_contract_preserves_terminal_and_fence_admission_rules(
        self,
    ) -> None:
        document = workflow("18-finance-writer-lease.json")
        lease_nodes = nodes("18-finance-writer-lease.json")
        metadata = document["meta"]
        self.assertEqual(
            metadata["durableStateTable"], "finance_ops.actual_writer_effects"
        )
        self.assertIn("PREPARED attempt zero", metadata["admissionPolicy"])
        self.assertIn("exact ACTUAL_OBSERVED over ISSUED", metadata["admissionPolicy"])
        self.assertIn("VERIFIED/RECONCILED/COMMITTED", metadata["admissionPolicy"])
        acquire_sql = lease_nodes["Atomic Acquire Writer Lease"]["parameters"]["query"]
        release_sql = lease_nodes["Release Exact Writer Fence"]["parameters"]["query"]
        validator_code = lease_nodes["Validate Fixed Lease Operation"]["parameters"][
            "jsCode"
        ]
        self.assertIn(
            "state IN ('PREPARED', 'ISSUED', 'ACTUAL_OBSERVED', 'OUTCOME_UNKNOWN')",
            acquire_sql,
        )
        self.assertIn("terminal.present OR resumable.present", acquire_sql)
        self.assertIn("actual_writer_effects.state = 'ISSUED'", acquire_sql)
        self.assertIn("$11::text = 'ACTUAL_OBSERVED'", acquire_sql)
        self.assertIn("$13::text = 'MAINTENANCE'", acquire_sql)
        self.assertIn("$5::text = 'SUCCESSOR'", acquire_sql)
        self.assertIn("$11::text = 'PREPARED'", acquire_sql)
        self.assertIn("$12::integer = 0", acquire_sql)
        self.assertIn("payload_sha256 = $7::text", acquire_sql)
        self.assertIn("verified_payload_sha256 IS NOT NULL", acquire_sql)
        self.assertNotIn("verified_payload_sha256 = $7::text", acquire_sql)
        self.assertIn(
            "state = CASE WHEN actual_writer_effects.state = 'PREPARED' "
            "THEN 'ISSUED' ELSE actual_writer_effects.state END",
            acquire_sql,
        )
        for assignment in (
            "lease_id = EXCLUDED.lease_id",
            "lease_owner = EXCLUDED.lease_owner",
            "fencing_token = EXCLUDED.fencing_token",
        ):
            self.assertIn(assignment, acquire_sql)
        self.assertIn("release_maintenance_lease", release_sql)
        self.assertIn("release_writer_lease", release_sql)
        self.assertIn(
            "$json.lease_class",
            lease_nodes["Release Exact Writer Fence"]["parameters"]["options"][
                "queryReplacement"
            ],
        )
        self.assertIn("finance_ops.acquire_writer_lease", acquire_sql)
        migration = (N8N / "postgres" / "001-finance-writer-lease.sql").read_text()
        self.assertIn("actual_writer_releases", migration)
        release_body = migration[
            migration.index(
                "CREATE OR REPLACE FUNCTION finance_ops.release_writer_lease"
            ) :
        ]
        self.assertLess(
            release_body.index("actual_writer_releases"),
            release_body.index("actual_writer_effects"),
        )
        writer_release_body = release_body[
            : release_body.index(
                "CREATE OR REPLACE FUNCTION finance_ops.release_maintenance_lease"
            )
        ]
        self.assertNotIn("IF changed", writer_release_body)
        self.assertIn(
            "INSERT INTO finance_ops.actual_writer_releases", writer_release_body
        )
        self.assertIn("UPDATE finance_ops.writer_leases", writer_release_body)
        accepted = run_code_node(
            "Validate Fixed Lease Operation",
            validator_code,
            {
                "operation": "ACQUIRE",
                "resource_key": "actual:budget-1",
                "lease_owner": "n8n:recovery:outbox-1",
                "ttl_seconds": 120,
                "lease_class": "ACTUAL_OUTBOX",
                "outbox_id": "outbox-1",
                "account_id": "account-1",
                "budget_id": "budget-1",
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
                "outbox_state": "ACTUAL_OBSERVED",
                "attempt_count": 1,
                "admission": "SUCCESSOR",
                "payload_sha256": "a" * 64,
            },
            {},
        )
        self.assertTrue(accepted["ok"], accepted)
        rejected_initial = run_code_node(
            "Validate Fixed Lease Operation",
            validator_code,
            {
                "operation": "ACQUIRE",
                "resource_key": "actual:budget-1",
                "lease_owner": "n8n:recovery:outbox-1",
                "ttl_seconds": 120,
                "outbox_id": "outbox-1",
                "lease_class": "ACTUAL_OUTBOX",
                "outbox_state": "ACTUAL_OBSERVED",
                "attempt_count": 0,
                "admission": "INITIAL",
                "payload_sha256": "a" * 64,
            },
            {},
        )
        self.assertFalse(rejected_initial["ok"])
        accepted_maintenance = run_code_node(
            "Validate Fixed Lease Operation",
            validator_code,
            {
                "operation": "ACQUIRE",
                "lease_class": "MAINTENANCE",
                "resource_key": "actual:budget-1",
                "lease_owner": "n8n:maintenance:fixture",
                "ttl_seconds": 600,
            },
            {},
        )
        self.assertTrue(accepted_maintenance["ok"], accepted_maintenance)
        maintenance_request = accepted_maintenance["output"][0]["json"]
        self.assertEqual(maintenance_request["lease_class"], "MAINTENANCE")
        self.assertNotIn("outbox_id", maintenance_request)
        self.assertEqual(
            next_node(document, "Trusted Lease Request"),
            "Validate Fixed Lease Operation",
        )
        self.assertEqual(
            next_node(document, "Validate Fixed Lease Operation"), "Lease Operation"
        )
        self.assertEqual(
            metadata["fenceReleasePolicy"],
            "ACTUAL_OUTBOX requires durable COMMITTED readback; MAINTENANCE uses exact generic release",
        )
        self.assertEqual(metadata["leaseClasses"], ["ACTUAL_OUTBOX", "MAINTENANCE"])

    def test_actual_observed_recovery_request_is_terminal_backed(self) -> None:
        actual_nodes = nodes("20-actual-outbox-apply.json")
        request_code = actual_nodes["Build Recovery Lease Acquire Request"][
            "parameters"
        ]["jsCode"]
        payload_sha256 = "a" * 64
        result = run_code_node(
            "Build Recovery Lease Acquire Request",
            request_code,
            {},
            {
                "Verify Recovery Contract": {
                    "json": {
                        "payload_sha256": payload_sha256,
                        "outbox_row": {
                            "outbox_id": "outbox-verified",
                            "batch_id": "batch-verified",
                            "actual_file_id": "budget-1",
                            "account_id": "account-1",
                            "state": "ACTUAL_OBSERVED",
                            "attempt_count": 1,
                        },
                        "manifest": {"account_id": "account-1"},
                    }
                }
            },
        )
        self.assertTrue(result["ok"], result)
        request = result["output"][0]["json"]
        self.assertEqual(request["outbox_state"], "ACTUAL_OBSERVED")
        self.assertEqual(request["admission"], "SUCCESSOR")
        self.assertEqual(request["payload_sha256"], payload_sha256)
        self.assertEqual(request["lease_class"], "ACTUAL_OUTBOX")

    def test_w20_checks_release_history_then_recovers_committed_exact_lease(
        self,
    ) -> None:
        document = workflow("20-actual-outbox-apply.json")
        actual_nodes = nodes("20-actual-outbox-apply.json")
        self.assertEqual(document["meta"]["financeWorkflowCode"], "ACTUAL_OUTBOX_APPLY")
        self.assertTrue(document["meta"]["singleActualWriter"])
        self.assertEqual(
            next_node(document, "Route Recovery State", 3),
            "Read Back COMMITTED Recovery Replay",
        )
        self.assertEqual(
            next_node(document, "Route Recovery State", 4),
            "Read Back COMMITTED Recovery Replay",
        )
        replay_state_filter = next(
            condition
            for condition in actual_nodes["Read Back COMMITTED Recovery Replay"][
                "parameters"
            ]["filters"]["conditions"]
            if condition["keyName"] == "state"
        )
        self.assertIn("outbox_row.state", replay_state_filter["keyValue"])
        self.assertEqual(
            next_node(document, "Read Back COMMITTED Recovery Replay"),
            "Read Back Exact Actual Verification Receipt Replay",
        )
        self.assertEqual(
            next_node(document, "Read Back Exact Actual Verification Receipt Replay"),
            "Read Back Released Recovery Writer Fence Replay",
        )
        self.assertEqual(
            next_node(document, "Read Back Released Recovery Writer Fence Replay"),
            "Historical Recovery Release Exists",
        )
        self.assertEqual(
            next_node(document, "Historical Recovery Release Exists"),
            "Return Verified Commit Receipt Replay",
        )
        self.assertEqual(
            next_node(document, "Historical Recovery Release Exists", 1),
            "Read Back COMMITTED Durable Writer State Replay",
        )
        self.assertEqual(
            next_node(document, "Read Back COMMITTED Durable Writer State Replay"),
            "Build COMMITTED Recovery Fence Release",
        )
        self.assertEqual(
            next_node(document, "Build COMMITTED Recovery Fence Release"),
            "Release COMMITTED Recovery Writer Fence",
        )
        self.assertEqual(
            next_node(document, "Release COMMITTED Recovery Writer Fence"),
            "Read Back Recovered COMMITTED Writer Fence",
        )
        self.assertEqual(
            next_node(document, "Read Back Recovered COMMITTED Writer Fence"),
            "Return Recovered COMMITTED Release Receipt",
        )
        self.assertEqual(
            next_node(document, "Recovery Import PREPARED"),
            "Build Post-Import Fence Assert",
        )
        self.assertEqual(
            next_node(document, "Assert Recovery Fence After Import"),
            "Restore Post-Import Result",
        )
        self.assertEqual(
            next_node(document, "Assert Recovery Fence Before Commit"),
            "Record COMMITTED in Durable Writer State",
        )
        self.assertEqual(
            next_node(document, "Record COMMITTED in Durable Writer State"),
            "Read Back COMMITTED Durable Writer State",
        )
        self.assertEqual(
            next_node(document, "Read Back COMMITTED Durable Writer State"),
            "Validate Durable COMMITTED Readback",
        )
        self.assertEqual(
            next_node(document, "Validate Durable COMMITTED Readback"),
            "Upsert COMMITTED Recovery",
        )
        self.assertEqual(
            next_node(document, "Read Back COMMITTED Recovery"),
            "Build Recovery Fence Release",
        )
        self.assertEqual(
            next_node(document, "Release Recovery Writer Fence"),
            "Read Back Released Recovery Writer Fence",
        )
        self.assertEqual(
            next_node(document, "Read Back Released Recovery Writer Fence"),
            "Return Verified Commit Receipt",
        )
        for validated_receipt in (
            "Return Verified Commit Receipt",
            "Return Verified Commit Receipt Replay",
            "Return Recovered COMMITTED Release Receipt",
        ):
            self.assertEqual(
                next_node(document, validated_receipt), "Mark Recovery RELEASED"
            )
        self.assertEqual(
            next_node(document, "Mark Recovery RELEASED"),
            "Return RELEASED Recovery Receipt",
        )
        released_values = actual_nodes["Mark Recovery RELEASED"]["parameters"][
            "columns"
        ]["value"]
        self.assertEqual(released_values["state"], "RELEASED")
        self.assertEqual(document["meta"]["releasedState"], "RELEASED")
        release_code = actual_nodes["Build Recovery Fence Release"]["parameters"][
            "jsCode"
        ]
        self.assertIn("Validate Stored Verification Receipt for Commit", release_code)
        self.assertNotIn("Compare Exact Actual Verification Receipt", release_code)
        self.assertIn("Validate Durable COMMITTED Readback", release_code)
        durable_commit_sql = actual_nodes["Record COMMITTED in Durable Writer State"][
            "parameters"
        ]["query"]
        self.assertIn("SET state = 'COMMITTED'", durable_commit_sql)
        self.assertIn("lease_id = $9::uuid", durable_commit_sql)
        self.assertIn("fencing_token = $11::bigint", durable_commit_sql)
        replay_code = actual_nodes["Return Verified Commit Receipt Replay"][
            "parameters"
        ]["jsCode"]
        self.assertIn("replay_readback_only", replay_code)
        self.assertNotIn("Build Recovery Fence Release", replay_code)
        self.assertIn(
            "idempotency_key",
            actual_nodes["Upsert Exact Actual Verification Receipt"]["parameters"][
                "filters"
            ]["conditions"][0]["keyValue"],
        )
        replay_query = actual_nodes["Read Back Released Recovery Writer Fence Replay"][
            "parameters"
        ]["query"]
        self.assertIn("actual_writer_releases", replay_query)
        self.assertIn("outbox_id = $2", replay_query)
        self.assertIn("fencing_token = $5", replay_query)
        self.assertNotIn("writer_leases", replay_query)

        committed = {
            "batch_id": "outbox:historical:1",
            "outbox_id": "outbox:historical:1",
            "actual_file_id": "actual-file:historical:1",
            "account_id": "actual-account:ADCB_CASHBACK",
            "payload_sha256": "a" * 64,
            "state": "COMMITTED",
            "lease_owner": "n8n:recovery:historical:1",
            "lease_fence": 9,
            "idempotency_key": "outbox:historical:1",
        }
        receipt = {
            "batch_id": "outbox:historical:1",
            "idempotency_key": "outbox:historical:1",
            "actual_file_id": "actual-file:historical:1",
            "account_id": "actual-account:ADCB_CASHBACK",
            "card_code": "ADCB_CASHBACK",
            "verification_version": 1,
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
            "expected_payload_sha256": "a" * 64,
            "observed_payload_sha256": "a" * 64,
            "expected_count": 1,
            "observed_count": 1,
            "expected_amount_sum_minor": 100,
            "observed_amount_sum_minor": 100,
            "expected_account_balance": -100,
            "observed_account_balance": -100,
            "invariants_passed": True,
            "verified_at": "2026-08-20T00:00:00+00:00",
        }
        references = {
            "Read Back COMMITTED Recovery Replay": {"json": committed},
            "Read Back Exact Actual Verification Receipt Replay": {"json": receipt},
            "Read Back Released Recovery Writer Fence Replay": {
                "json": {
                    "resource_key": "actual:actual-file:historical:1",
                    "outbox_id": committed["outbox_id"],
                    "account_id": committed["account_id"],
                    "payload_sha256": committed["payload_sha256"],
                    "verified_payload_sha256": committed["payload_sha256"],
                    "lease_owner": committed["lease_owner"],
                    "fencing_token": committed["lease_fence"],
                    "released": True,
                }
            },
            "Verify Recovery Contract": {
                "json": {
                    "manifest": {
                        "actual_file_id": receipt["actual_file_id"],
                        "account_id": receipt["account_id"],
                        "card_code": receipt["card_code"],
                        "period_start": receipt["period_start"],
                        "period_end": receipt["period_end"],
                        "expected_statement_balance_minor": receipt[
                            "expected_account_balance"
                        ],
                    },
                    "payload_sha256": receipt["expected_payload_sha256"],
                }
            },
        }
        replay = run_code_node(
            "Return Verified Commit Receipt Replay",
            replay_code,
            receipt,
            references,
        )
        self.assertTrue(replay["ok"], replay)
        self.assertTrue(replay["output"][0]["json"]["replay_readback_only"])
        self.assertEqual(replay["output"][0]["json"]["state"], "COMMITTED")
        released_references = {
            **references,
            "Read Back COMMITTED Recovery Replay": {
                "json": {**committed, "state": "RELEASED"}
            },
        }
        released_replay = run_code_node(
            "Return Verified Commit Receipt Replay",
            replay_code,
            receipt,
            released_references,
        )
        self.assertTrue(released_replay["ok"], released_replay)
        self.assertTrue(released_replay["output"][0]["json"]["replay_readback_only"])

        mismatched = dict(receipt, card_code="OTHER_CARD")
        mismatched_references = {
            **references,
            "Read Back Exact Actual Verification Receipt Replay": {"json": mismatched},
        }
        rejected = run_code_node(
            "Return Verified Commit Receipt Replay",
            replay_code,
            mismatched,
            mismatched_references,
        )
        self.assertFalse(rejected["ok"])
        unreleased_references = {
            **references,
            "Read Back Released Recovery Writer Fence Replay": {
                "json": {
                    "resource_key": "actual:actual-file:historical:1",
                    "outbox_id": committed["outbox_id"],
                    "account_id": committed["account_id"],
                    "payload_sha256": committed["payload_sha256"],
                    "verified_payload_sha256": committed["payload_sha256"],
                    "lease_owner": committed["lease_owner"],
                    "fencing_token": committed["lease_fence"],
                    "released": False,
                }
            },
        }
        unreleased = run_code_node(
            "Return Verified Commit Receipt Replay",
            replay_code,
            receipt,
            unreleased_references,
        )
        self.assertFalse(unreleased["ok"])

    def test_committed_before_release_crash_recovers_exact_durable_lease(self) -> None:
        actual_nodes = nodes("20-actual-outbox-apply.json")
        build_code = actual_nodes["Build COMMITTED Recovery Fence Release"][
            "parameters"
        ]["jsCode"]
        payload_sha256 = "a" * 64
        verified_payload_sha256 = "b" * 64
        committed = {
            "batch_id": "outbox:crash:1",
            "outbox_id": "outbox:crash:1",
            "actual_file_id": "actual-file:crash",
            "account_id": "account:crash",
            "payload_sha256": payload_sha256,
            "state": "COMMITTED",
            "lease_owner": "n8n:recovery:outbox:crash:1",
            "lease_fence": 11,
        }
        receipt = {
            "batch_id": committed["batch_id"],
            "actual_file_id": committed["actual_file_id"],
            "account_id": committed["account_id"],
            "card_code": "CRASH_CARD",
            "verification_version": 1,
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
            "expected_payload_sha256": verified_payload_sha256,
            "observed_payload_sha256": verified_payload_sha256,
            "expected_count": 1,
            "observed_count": 1,
            "expected_amount_sum_minor": -100,
            "observed_amount_sum_minor": -100,
            "expected_account_balance": -100,
            "observed_account_balance": -100,
            "invariants_passed": True,
            "verified_at": "2026-08-20T00:00:00+00:00",
        }
        effect = {
            "resource_key": "actual:actual-file:crash",
            "outbox_id": committed["outbox_id"],
            "account_id": committed["account_id"],
            "budget_id": committed["actual_file_id"],
            "payload_sha256": payload_sha256,
            "verified_payload_sha256": verified_payload_sha256,
            "state": "COMMITTED",
            "lease_id": "11111111-1111-4111-8111-111111111111",
            "lease_owner": committed["lease_owner"],
            "fencing_token": committed["lease_fence"],
        }
        recovery = {
            "manifest": {
                "actual_file_id": committed["actual_file_id"],
                "account_id": committed["account_id"],
                "card_code": receipt["card_code"],
                "expected_statement_balance_minor": -100,
            },
            "payload_sha256": payload_sha256,
        }
        references = {
            "Read Back COMMITTED Recovery Replay": {"json": committed},
            "Read Back Exact Actual Verification Receipt Replay": {"json": receipt},
            "Verify Recovery Contract": {"json": recovery},
        }
        built = run_code_node(
            "Build COMMITTED Recovery Fence Release",
            build_code,
            effect,
            references,
        )
        self.assertTrue(built["ok"], built)
        request = built["output"][0]["json"]
        self.assertEqual(request["operation"], "RELEASE")
        self.assertEqual(request["lease_class"], "ACTUAL_OUTBOX")
        self.assertEqual(request["lease_id"], effect["lease_id"])
        self.assertEqual(request["fencing_token"], committed["lease_fence"])

        stale = run_code_node(
            "Build COMMITTED Recovery Fence Release",
            build_code,
            {**effect, "lease_owner": "n8n:recovery:other"},
            references,
        )
        self.assertFalse(stale["ok"], stale)

        return_code = actual_nodes["Return Recovered COMMITTED Release Receipt"][
            "parameters"
        ]["jsCode"]
        recovered = run_code_node(
            "Return Recovered COMMITTED Release Receipt",
            return_code,
            {**request, "released": True},
            {
                **references,
                "Build COMMITTED Recovery Fence Release": {"json": request},
                "Read Back Recovered COMMITTED Writer Fence": {
                    "json": {**request, "released": True}
                },
            },
        )
        self.assertTrue(recovered["ok"], recovered)
        output = recovered["output"][0]["json"]
        self.assertTrue(output["committed_release_recovered"])
        self.assertFalse(output["replay_readback_only"])
        self.assertTrue(output["writer_release_verified"])
        final_code = actual_nodes["Return RELEASED Recovery Receipt"]["parameters"][
            "jsCode"
        ]
        finalized = run_code_node(
            "Return RELEASED Recovery Receipt",
            final_code,
            {"batch_id": output["batch_id"], "state": "RELEASED"},
            {"Return Recovered COMMITTED Release Receipt": {"json": output}},
        )
        self.assertTrue(finalized["ok"], finalized)
        self.assertEqual(
            finalized["output"][0]["json"]["durable_outbox_state"], "RELEASED"
        )


if __name__ == "__main__":
    unittest.main()
