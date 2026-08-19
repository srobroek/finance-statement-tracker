from __future__ import annotations

import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from finance_tracker.account_completeness import (
    load_account_completeness_manifest,
    validate_account_completeness,
)
from finance_tracker.account_proposals import (
    build_adcb_closed_zero_assertion,
    build_fab_inventory_proposal,
    build_fab_opening_anchor_proposal,
    build_sarwa_position_sidecar,
)
from finance_tracker.wealth import parse_registered_wealth_capture


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "account-completeness.json"
WEALTH_CONFIG = ROOT / "config" / "wealth-sources.json"
SARWA_CAPTURE = ROOT / "tests" / "fixtures" / "sarwa-holdings.sample.json"
FAB_INVENTORY = ROOT / "config" / "evidence" / "browser-captures" / "fab-non-credit-inventory-2026-08-19.json"
FAB_ACTIVITY = ROOT / "runtime" / "browser-captures" / "fab-account-2001-2026-08-18.json"


class AccountCompletenessTests(unittest.TestCase):
    def test_manifest_uses_unique_safe_stable_identities(self) -> None:
        manifest = load_account_completeness_manifest(MANIFEST)

        identities = [row.provider_account_id for row in manifest.accounts]
        self.assertEqual(len(identities), len(set(identities)))
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
            11505473,
        )
        self.assertEqual(
            fab_2001["opening_balance_anchor"]["source_as_of_balance_minor"],
            22501145,
        )
        self.assertEqual(
            fab_2001["opening_balance_anchor"]["captured_activity_net_minor"],
            10995672,
        )
        self.assertEqual(
            fab_2001["inventory_balance_verification"]["balance_minor"],
            22501145,
        )
        self.assertEqual(
            fab_2001["inventory_balance_verification"]["freshness"],
            "FRESH",
        )
        self.assertFalse(fab_2001["synthetic_balancing_row_allowed"])
        self.assertIsNone(fab_2001["derived_adjustment"])
        self.assertEqual(
            fab_2001["transaction_boundary"]["captured_rows"],
            "IMPORT_AFTER_OPENING_ANCHOR",
        )
        mortgage = next(
            row for row in fab["accounts"]
            if row["provider_account_id"] == "fab:loan:mortgage-0203"
        )
        self.assertTrue(mortgage["offbudget"])
        self.assertEqual(mortgage["opening_balance_anchor"]["balance_minor"], -260595200)
        self.assertEqual(mortgage["history_policy"], "NO_HISTORY_FABRICATED")
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

    def test_fab_anchor_fails_closed_for_stale_or_future_capture(self) -> None:
        capture = json.loads(
            (ROOT / "runtime" / "browser-captures" / "fab-account-2001-2026-08-18.json")
            .read_text(encoding="utf-8")
        )
        as_of = datetime.fromisoformat(
            capture["account"]["balance_as_of"].replace("Z", "+00:00")
        )
        common = {
            "capture": capture,
            "provider_account_id": "fab:current:2001",
            "account_name": "FAB Elite Gold Current Account · 2001",
            "inventory_complete": False,
            "stale_after_seconds": 86400,
        }
        stale = build_fab_opening_anchor_proposal(
            evaluated_at=as_of + timedelta(days=2), **common
        )
        future = build_fab_opening_anchor_proposal(
            evaluated_at=as_of - timedelta(seconds=1), **common
        )

        self.assertIn("FAB_PORTAL_ACCOUNT_INVENTORY_REQUIRED", stale["blockers"])
        self.assertIn("FAB_BALANCE_SNAPSHOT_STALE", stale["blockers"])
        self.assertIn("FAB_BALANCE_AS_OF_IN_FUTURE", future["blockers"])
        self.assertFalse(stale["actual_writes_allowed"])
        self.assertFalse(future["actual_writes_allowed"])

    def test_complete_fab_inventory_proposal_rejects_set_or_sign_drift(self) -> None:
        manifest = load_account_completeness_manifest(MANIFEST)
        inventory = json.loads(FAB_INVENTORY.read_text(encoding="utf-8"))
        activity = json.loads(FAB_ACTIVITY.read_text(encoding="utf-8"))
        proposal = build_fab_inventory_proposal(
            inventory,
            activity,
            manifest,
            evaluated_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
        )
        self.assertEqual(proposal["status"], "READY_FOR_REVIEW")
        self.assertFalse(proposal["actual_writes_allowed"])

        missing = json.loads(json.dumps(inventory))
        missing["accounts"].pop()
        with self.assertRaisesRegex(ValueError, "account sets differ"):
            build_fab_inventory_proposal(
                missing, activity, manifest,
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
                wrong_sign, activity, manifest,
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
        passed = build_adcb_closed_zero_assertion(
            account,
            issuer_closing_balance_minor=0,
            actual_balance_minor=0,
            actual_closed=True,
        )

        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertIn("EVIDENCED_CLOSING_PAYMENT_REQUIRED", blocked["blockers"])
        self.assertEqual(passed["status"], "PASS")
        self.assertEqual(passed["blockers"], [])
        self.assertFalse(passed["synthetic_balancing_row_allowed"])


if __name__ == "__main__":
    unittest.main()
