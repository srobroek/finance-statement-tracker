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
    def test_w17_reclaims_only_nonterminal_actual_outbox_states(self) -> None:
        document = workflow("17-actual-outbox-recovery.json")
        read = nodes("17-actual-outbox-recovery.json")["Read Nonterminal Actual Outbox"]
        states = {
            condition["keyValue"]
            for condition in read["parameters"]["filters"]["conditions"]
        }
        self.assertEqual(states, {"PREPARED", "ACTUAL_OBSERVED", "VERIFIED"})
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
        self.assertIn("PREPARED attempt zero only", metadata["admissionPolicy"])
        self.assertIn(
            "ISSUED/OUTCOME_UNKNOWN never reclaimed", metadata["admissionPolicy"]
        )
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
        self.assertIn("terminal.present", acquire_sql)
        self.assertIn("$5::text = 'SUCCESSOR'", acquire_sql)
        self.assertIn("$11::text = 'PREPARED'", acquire_sql)
        self.assertIn("$12::integer = 0", acquire_sql)
        self.assertIn("state IN ('VERIFIED', 'RECONCILED', 'COMMITTED')", release_sql)
        self.assertIn("finance_ops.acquire_writer_lease", acquire_sql)
        self.assertIn("finance_ops.release_writer_lease", release_sql)
        self.assertIn(
            "actual_writer_releases",
            (N8N / "postgres" / "001-finance-writer-lease.sql").read_text(),
        )
        accepted = run_code_node(
            "Validate Fixed Lease Operation",
            validator_code,
            {
                "operation": "ACQUIRE",
                "resource_key": "actual:budget-1",
                "lease_owner": "n8n:recovery:outbox-1",
                "ttl_seconds": 120,
                "outbox_id": "outbox-1",
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
                "outbox_state": "ACTUAL_OBSERVED",
                "attempt_count": 0,
                "admission": "INITIAL",
                "payload_sha256": "a" * 64,
            },
            {},
        )
        self.assertFalse(rejected_initial["ok"])
        self.assertEqual(
            next_node(document, "Trusted Lease Request"),
            "Validate Fixed Lease Operation",
        )
        self.assertEqual(
            next_node(document, "Validate Fixed Lease Operation"), "Lease Operation"
        )
        self.assertEqual(
            metadata["fenceReleasePolicy"], "W20 terminal verified readback only"
        )

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

    def test_w20_replay_is_readback_only_and_fences_surround_actual_mutation(
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
            next_node(document, "Read Back COMMITTED Recovery Replay"),
            "Read Back Exact Actual Verification Receipt Replay",
        )
        self.assertEqual(
            next_node(document, "Read Back Exact Actual Verification Receipt Replay"),
            "Read Back Released Recovery Writer Fence Replay",
        )
        self.assertEqual(
            next_node(document, "Read Back Released Recovery Writer Fence Replay"),
            "Return Verified Commit Receipt Replay",
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
            "Build Recovery Fence Release",
        )
        self.assertEqual(
            next_node(document, "Release Recovery Writer Fence"),
            "Read Back Released Recovery Writer Fence",
        )
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


if __name__ == "__main__":
    unittest.main()
