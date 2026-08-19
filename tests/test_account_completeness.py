from __future__ import annotations

import json
import unittest
from pathlib import Path

from finance_tracker.account_completeness import (
    load_account_completeness_manifest,
    validate_account_completeness,
)
from finance_tracker.wealth import parse_registered_wealth_capture


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "account-completeness.json"
WEALTH_CONFIG = ROOT / "config" / "wealth-sources.json"
SARWA_CAPTURE = ROOT / "tests" / "fixtures" / "sarwa-holdings.sample.json"


class AccountCompletenessTests(unittest.TestCase):
    def test_manifest_uses_unique_safe_stable_identities(self) -> None:
        manifest = load_account_completeness_manifest(MANIFEST)

        identities = [row.provider_account_id for row in manifest.accounts]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertIn("fab:current:2001", identities)
        self.assertIn("sarwa:invest:personal", identities)
        self.assertTrue(all(row.provider_account_id != row.display_name for row in manifest.accounts))
        self.assertTrue(all(not row.last4 or len(row.last4) == 4 for row in manifest.accounts))

    def test_fab_inventory_is_deliberately_incomplete_until_portal_inventory(self) -> None:
        manifest = load_account_completeness_manifest(MANIFEST)
        report = validate_account_completeness(
            manifest,
            observed_provider_account_ids={"fab:current:2001"},
            provider_id="fab",
        )

        self.assertEqual(report["status"], "INCOMPLETE_SOURCE_INVENTORY")
        self.assertFalse(report["production_write_allowed"])
        self.assertEqual(report["missing"], [])
        self.assertIn("FAB_PORTAL_ACCOUNT_INVENTORY_REQUIRED", report["blockers"])

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
        self.assertEqual(
            proposal["fab"]["known_accounts"][0]["derived_adjustment_minor"],
            22501145 - 10995672,
        )
        self.assertTrue(proposal["fab"]["known_accounts"][0]["replaceable"])
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


if __name__ == "__main__":
    unittest.main()
