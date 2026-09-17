from __future__ import annotations

import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from finance_tracker.account_completeness import (
    _parse_account_row,
    _parse_provider_inventory,
    _validate_account_lifecycle_and_balance,
    load_account_completeness_manifest,
    validate_account_completeness,
)
from finance_tracker.account_proposals import (
    build_adcb_closed_zero_assertion,
    build_fab_inventory_proposal,
    build_sarwa_position_sidecar,
)
from finance_tracker.wealth import parse_registered_wealth_capture


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "account-completeness.json"
WEALTH_CONFIG = ROOT / "config" / "wealth-sources.json"
SARWA_CAPTURE = ROOT / "tests" / "fixtures" / "sarwa-holdings.sample.json"
FAB_INVENTORY = ROOT / "config" / "evidence" / "browser-captures" / "fab-non-credit-inventory-2026-08-19.json"
ADCB_STATUS = ROOT / "config" / "evidence" / "adcb-closed-card-status-2026-08-19.json"
RECONCILIATION_RECEIPT = ROOT / "config" / "evidence" / "production-account-reconciliation-receipt-2026-08-19.json"


class AccountCompletenessTests(unittest.TestCase):
    def test_private_parsers_preserve_manifest_projection_and_errors(self) -> None:
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
        identities: set[str] = set()
        parsed_accounts = []
        for raw in payload["accounts"]:
            account = _parse_account_row(raw, identities)
            _validate_account_lifecycle_and_balance(account)
            parsed_accounts.append(account)

        provider_ids: set[str] = set()
        parsed_providers = [
            _parse_provider_inventory(raw, provider_ids)
            for raw in payload["providers"]
        ]
        manifest = load_account_completeness_manifest(payload)

        self.assertEqual(tuple(parsed_accounts), manifest.accounts)
        self.assertEqual(tuple(parsed_providers), manifest.providers)
        self.assertEqual(len(parsed_accounts), 18)
        self.assertEqual(len(identities), 12)
        self.assertEqual(len(parsed_accounts) - len(identities), 6)

        invalid_account = json.loads(json.dumps(payload))
        invalid_account["accounts"][0]["active"] = True
        with self.assertRaises(ValueError) as public_error:
            load_account_completeness_manifest(invalid_account)
        with self.assertRaises(ValueError) as parser_error:
            account = _parse_account_row(invalid_account["accounts"][0], set())
            _validate_account_lifecycle_and_balance(account)
        self.assertEqual(str(parser_error.exception), str(public_error.exception))

        invalid_provider = json.loads(json.dumps(payload))
        invalid_provider["providers"][0]["inventory_status"] = "UNKNOWN"
        with self.assertRaises(ValueError) as public_error:
            load_account_completeness_manifest(invalid_provider)
        with self.assertRaises(ValueError) as parser_error:
            _parse_provider_inventory(invalid_provider["providers"][0], set())
        self.assertEqual(str(parser_error.exception), str(public_error.exception))

    def test_manifest_rejects_truthy_boolean_values(self) -> None:
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
        account_fields = (
            "include_in_actual",
            "actual_offbudget",
            "include_in_net_worth",
            "active",
            "retain_history",
            "include_in_active_routing",
            "balance_reconciliation_required",
        )
        for field in account_fields:
            with self.subTest(field=field):
                candidate = json.loads(json.dumps(payload))
                candidate["accounts"][0][field] = "false"
                with self.assertRaisesRegex(ValueError, field):
                    load_account_completeness_manifest(candidate)

        candidate = json.loads(json.dumps(payload))
        candidate["providers"][0]["discovery_required"] = 0
        with self.assertRaisesRegex(ValueError, "discovery_required"):
            load_account_completeness_manifest(candidate)

    def test_production_reconciliation_receipt_is_redacted_and_hash_bound(self) -> None:
        receipt = json.loads(RECONCILIATION_RECEIPT.read_text(encoding="utf-8"))
        rendered = json.dumps(receipt)

        self.assertEqual(receipt["verification"]["api_readback"], "PASS")
        self.assertEqual(receipt["verification"]["transaction_readback"], "PASS")
        self.assertEqual(receipt["summary"]["target_account_count"], 7)
        self.assertEqual(receipt["summary"]["created_account_count"], 5)
        self.assertEqual(receipt["summary"]["reconciliation_adjustment_count"], 3)
        self.assertEqual(receipt["summary"]["unrelated_account_mutation_count"], 0)
        self.assertNotRegex(
            rendered,
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        )
        self.assertNotIn("balance_minor", rendered)
        self.assertNotIn("amount_minor", rendered)
        self.assertNotIn("actual_account_id", rendered)
        for digest in receipt["runtime_artifacts"].values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_manifest_uses_unique_safe_stable_identities(self) -> None:
        manifest = load_account_completeness_manifest(MANIFEST)

        identities = [row.provider_account_id for row in manifest.accounts if row.provider_account_id]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertEqual(manifest.account_count, 18)
        self.assertEqual(len(manifest.provider_identity_candidates()), 12)
        self.assertIn("fab:current:2001", identities)
        self.assertIn("fab:loan:mortgage-0203", identities)
        self.assertIn("sarwa:invest:personal", identities)
        self.assertTrue(all(row.provider_account_id != row.display_name for row in manifest.accounts))
        self.assertTrue(all(not row.last4 or len(row.last4) == 4 for row in manifest.accounts))

    def test_fab_inventory_is_complete_and_balances_match_portal_capture(self) -> None:
        manifest = load_account_completeness_manifest(MANIFEST)
        capture = json.loads(FAB_INVENTORY.read_text(encoding="utf-8"))
        balances = {
            row["provider_account_id"]: int(
                row.get("actual_signed_balance_minor", row.get("observed_balance_minor"))
            )
            for row in capture["accounts"]
        }
        report = validate_account_completeness(
            manifest,
            observed_provider_account_ids=set(balances),
            observed_balances_minor=balances,
            provider_id="fab",
        )

        self.assertEqual(report["status"], "COMPLETE")
        self.assertTrue(report["production_write_allowed"])
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["blockers"], [])

    def test_fab_capture_is_redacted_and_retains_only_last_four(self) -> None:
        capture = json.loads(FAB_INVENTORY.read_text(encoding="utf-8"))
        rendered = json.dumps(capture)

        self.assertTrue(capture["inventory_complete"])
        self.assertEqual(len(capture["accounts"]), 6)
        self.assertIsNone(re.search(r"\b[0-9]{12,}\b", rendered))
        self.assertIsNone(re.search(r"\b[A-Z]{2}[0-9]{10}\b", rendered))
        self.assertTrue(all(len(row["last4"]) == 4 for row in capture["accounts"]))

    def test_sarwa_capture_matches_expected_account_inventory(self) -> None:
        manifest = load_account_completeness_manifest(MANIFEST)
        snapshot = parse_registered_wealth_capture(
            "sarwa", "holdings", SARWA_CAPTURE, WEALTH_CONFIG,
            adapters_root=ROOT / "browser_adapters",
        )
        report = validate_account_completeness(
            manifest,
            observed_provider_account_ids={
                row.provider_account_id for row in snapshot.portfolios
            },
            provider_id="sarwa",
        )

        self.assertEqual(report["status"], "COMPLETE")
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["unexpected"], [])
        self.assertTrue(report["production_write_allowed"])

    def test_sarwa_owners_are_only_carried_when_capture_evidences_them(self) -> None:
        manifest = load_account_completeness_manifest(MANIFEST)
        snapshot = parse_registered_wealth_capture(
            "sarwa", "holdings", SARWA_CAPTURE, WEALTH_CONFIG,
            adapters_root=ROOT / "browser_adapters",
        )
        manifest_owners = {
            row.provider_account_id: row.owner
            for row in manifest.accounts if row.provider_id == "sarwa"
        }
        snapshot_owners = {
            row.provider_account_id: row.ownership for row in snapshot.portfolios
        }

        self.assertEqual(manifest_owners, snapshot_owners)
        self.assertIsNone(manifest_owners["sarwa:trade:primary"])

    def test_closed_adcb_is_historical_only_and_requires_exact_zero_balance(self) -> None:
        manifest = load_account_completeness_manifest(MANIFEST)
        account = next(
            row for row in manifest.accounts
            if row.provider_account_id == "adcb:credit:8833-6838"
        )

        self.assertEqual(account.lifecycle_status, "CLOSED")
        self.assertFalse(account.active)
        self.assertTrue(account.retain_history)
        self.assertFalse(account.include_in_active_routing)
        self.assertEqual(account.expected_balance_minor, 0)
        self.assertTrue(account.balance_reconciliation_required)

        failed = validate_account_completeness(
            manifest,
            observed_provider_account_ids={account.provider_account_id},
            observed_balances_minor={account.provider_account_id: -1},
            provider_id="adcb",
        )
        self.assertEqual(failed["status"], "ACCOUNT_BALANCE_MISMATCH")
        self.assertIn("ACCOUNT_BALANCE_RECONCILIATION_FAILED", failed["blockers"])

        passed = validate_account_completeness(
            manifest,
            observed_provider_account_ids={account.provider_account_id},
            observed_balances_minor={account.provider_account_id: 0},
            provider_id="adcb",
        )
        self.assertEqual(passed["status"], "COMPLETE")
        self.assertEqual(passed["balance_mismatches"], [])

    def test_display_name_change_does_not_change_identity_comparison(self) -> None:
        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
        account = next(
            row for row in raw["accounts"]
            if row["provider_account_id"] == "sarwa:invest:personal"
        )
        account["display_name"] = "Another display name"
        manifest = load_account_completeness_manifest(raw)
        report = validate_account_completeness(
            manifest,
            observed_provider_account_ids={
                "sarwa:invest:personal",
                "sarwa:invest:classic",
                "sarwa:invest:parents",
                "sarwa:invest:shared-airbnb",
                "sarwa:trade:primary",
            },
            provider_id="sarwa",
        )

        self.assertEqual(report["status"], "COMPLETE")

    def test_actual_account_proposal_cannot_write_and_retains_all_blockers(self) -> None:
        proposal = json.loads(
            (ROOT / "config" / "proposals" / "actual-accounts-fab-sarwa.json")
            .read_text(encoding="utf-8")
        )

        self.assertEqual(proposal["mode"], "PROPOSAL_ONLY")
        self.assertFalse(proposal["actual_writes_allowed"])
        self.assertEqual(proposal["status"], "BLOCKED")
        fab = proposal["fab"]
        self.assertFalse(fab["actual_writes_allowed"])
        self.assertEqual(fab["inventory_status"], "COMPLETE")
        self.assertEqual(fab["status"], "READY_FOR_REVIEW")
        self.assertEqual(len(fab["accounts"]), 6)
        fab_2001 = next(
            row for row in fab["accounts"]
            if row["provider_account_id"] == "fab:current:2001"
        )
        self.assertEqual(
            fab_2001["opening_balance_anchor"]["balance_minor"],
            22501145,
        )
        self.assertFalse(fab_2001["synthetic_balancing_row_allowed"])
        self.assertTrue(fab_2001["reconciliation_adjustment_allowed"])
        self.assertEqual(
            fab_2001["reconciliation_method"],
            "ACTUAL_NATIVE_RECONCILIATION_ADJUSTMENT",
        )
        self.assertEqual(
            fab_2001["history_policy"],
            "NO_HISTORY_REQUIRED_FOR_CURRENT_BALANCE",
        )
        mortgage = next(
            row for row in fab["accounts"]
            if row["provider_account_id"] == "fab:loan:mortgage-0203"
        )
        self.assertTrue(mortgage["offbudget"])
        self.assertEqual(mortgage["opening_balance_anchor"]["balance_minor"], -260595200)
        self.assertEqual(
            mortgage["history_policy"],
            "NO_HISTORY_REQUIRED_FOR_CURRENT_BALANCE",
        )
        self.assertFalse(mortgage["synthetic_balancing_row_allowed"])
        zero_accounts = [
            row for row in fab["accounts"]
            if row["provider_account_id"] not in {"fab:current:2001", "fab:loan:mortgage-0203"}
        ]
        self.assertEqual(len(zero_accounts), 4)
        self.assertTrue(all(row["opening_balance_anchor"]["balance_minor"] == 0 for row in zero_accounts))
        self.assertTrue(
            all(row["initial_balance_minor"] is None for row in proposal["sarwa"]["accounts"])
        )
        snapshot = parse_registered_wealth_capture(
            "sarwa", "holdings", SARWA_CAPTURE, WEALTH_CONFIG,
            adapters_root=ROOT / "browser_adapters",
        )
        proposed_values = {
            row["provider_account_id"]: row["source_value"]
            for row in proposal["sarwa"]["accounts"]
        }
        expected_values = {
            row.provider_account_id: str(row.total_value)
            for row in snapshot.portfolios
            if row.include_in_net_worth and not row.closed
        }
        self.assertEqual(proposal["sarwa"]["wealth_snapshot_id"], snapshot.snapshot_id)
        self.assertEqual(proposed_values, expected_values)

    def test_fab_inventory_proposal_fails_closed_for_stale_or_future_capture(self) -> None:
        manifest = load_account_completeness_manifest(MANIFEST)
        inventory = json.loads(FAB_INVENTORY.read_text(encoding="utf-8"))
        evaluated_at = datetime(2026, 8, 19, tzinfo=timezone.utc)
        fresh = build_fab_inventory_proposal(
            inventory,
            manifest,
            evaluated_at=evaluated_at,
            stale_after_seconds=86400,
        )
        self.assertEqual(fresh["status"], "READY_FOR_REVIEW")
        self.assertEqual(fresh["blockers"], [])
        self.assertFalse(fresh["actual_writes_allowed"])

        identities = [
            "fab:current:2001",
            "fab:current:2008",
            "fab:loan:mortgage-0203",
            "fab:savings:isave-2002",
            "fab:savings:shared-property-aed-2006",
            "fab:savings:shared-property-eur-2007",
        ]
        stale = build_fab_inventory_proposal(
            inventory,
            manifest,
            evaluated_at=evaluated_at + timedelta(days=2),
            stale_after_seconds=86400,
        )
        self.assertEqual(
            stale["blockers"],
            [f"FAB_BALANCE_SNAPSHOT_STALE:{identity}" for identity in identities],
        )
        self.assertEqual(stale["status"], "BLOCKED")
        self.assertFalse(stale["actual_writes_allowed"])
        self.assertTrue(
            all(row["opening_balance_anchor"]["freshness"] == "STALE" for row in stale["accounts"])
        )

        future = build_fab_inventory_proposal(
            inventory,
            manifest,
            evaluated_at=evaluated_at - timedelta(seconds=1),
            stale_after_seconds=86400,
        )
        self.assertEqual(
            future["blockers"],
            [f"FAB_BALANCE_AS_OF_IN_FUTURE:{identity}" for identity in identities],
        )
        self.assertEqual(future["status"], "BLOCKED")
        self.assertFalse(future["actual_writes_allowed"])
        self.assertTrue(
            all(
                row["opening_balance_anchor"]["freshness"] == "AS_OF_IN_FUTURE"
                for row in future["accounts"]
            )
        )

    def test_complete_fab_inventory_proposal_rejects_set_or_sign_drift(self) -> None:
        manifest = load_account_completeness_manifest(MANIFEST)
        inventory = json.loads(FAB_INVENTORY.read_text(encoding="utf-8"))
        proposal = build_fab_inventory_proposal(
            inventory,
            manifest,
            evaluated_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
        )
        self.assertEqual(proposal["status"], "READY_FOR_REVIEW")
        self.assertEqual(proposal["blockers"], [])
        self.assertFalse(proposal["actual_writes_allowed"])

        missing = json.loads(json.dumps(inventory))
        missing["accounts"].pop()
        with self.assertRaisesRegex(ValueError, "account sets differ"):
            build_fab_inventory_proposal(
                missing, manifest,
                evaluated_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
            )
        wrong_sign = json.loads(json.dumps(inventory))
        mortgage = next(
            row for row in wrong_sign["accounts"]
            if row["provider_account_id"] == "fab:loan:mortgage-0203"
        )
        mortgage["actual_signed_balance_minor"] = 260595200
        with self.assertRaisesRegex(ValueError, "liability sign"):
            build_fab_inventory_proposal(
                wrong_sign, manifest,
                evaluated_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
            )

    def test_sarwa_position_sidecar_is_read_only_and_not_ledger_activity(self) -> None:
        snapshot = parse_registered_wealth_capture(
            "sarwa", "holdings", SARWA_CAPTURE, WEALTH_CONFIG,
            adapters_root=ROOT / "browser_adapters",
        )
        sidecar = build_sarwa_position_sidecar(
            snapshot,
            evaluated_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
        )

        self.assertEqual(sidecar["status"], "READY_FOR_REVIEW")
        self.assertFalse(sidecar["actual_writes_allowed"])
        self.assertFalse(sidecar["positions_are_ledger_transactions"])
        personal = next(
            row for row in sidecar["portfolios"]
            if row["provider_account_id"] == "sarwa:invest:personal"
        )
        trade = next(
            row for row in sidecar["portfolios"]
            if row["provider_account_id"] == "sarwa:trade:primary"
        )
        self.assertEqual(len(personal["positions"]), 5)
        self.assertEqual(personal["position_feed_status"], "AVAILABLE")
        self.assertEqual(trade["position_feed_status"], "COMPONENTS_UNAVAILABLE")

    def test_adcb_zero_assertion_never_proposes_a_balancing_row(self) -> None:
        manifest = load_account_completeness_manifest(MANIFEST)
        account = next(
            row for row in manifest.accounts
            if row.provider_account_id == "adcb:credit:8833-6838"
        )
        blocked = build_adcb_closed_zero_assertion(account)
        latest = json.loads(ADCB_STATUS.read_text(encoding="utf-8"))
        observed = build_adcb_closed_zero_assertion(
            account,
            issuer_closing_balance_minor=latest["issuer_statement"]["closing_balance_minor"],
            issuer_evidence_id=latest["issuer_statement"]["evidence_id"],
        )
        passed = build_adcb_closed_zero_assertion(
            account,
            issuer_closing_balance_minor=0,
            issuer_evidence_id="sha256:final-zero-statement",
            actual_balance_minor=0,
            actual_closed=True,
        )

        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertIn("EVIDENCED_CLOSING_PAYMENT_REQUIRED", blocked["blockers"])
        self.assertIn("ISSUER_CLOSING_BALANCE_NOT_ZERO", observed["blockers"])
        self.assertEqual(observed["issuer_closing_balance_minor"], -23832)
        self.assertEqual(
            observed["issuer_evidence_id"],
            "sha256:edf889f3dd86d1bb278605b72ea7906c48e572689fb79afeabf1297b22b7a4e1",
        )
        self.assertEqual(passed["status"], "PASS")
        self.assertEqual(passed["blockers"], [])
        self.assertFalse(passed["synthetic_balancing_row_allowed"])
        self.assertTrue(passed["reconciliation_adjustment_allowed"])
        self.assertEqual(
            passed["reconciliation_policy"],
            "ACTUAL_NATIVE_RECONCILIATION_ADJUSTMENT",
        )


if __name__ == "__main__":
    unittest.main()
