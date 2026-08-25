from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from finance_tracker.wealth import (
    FXSnapshot,
    SarwaProvider,
    build_actual_wealth_proposal,
    parse_registered_wealth_capture,
)


ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "tests" / "fixtures" / "sarwa-holdings.sample.json"
CONFIG = ROOT / "config" / "wealth-sources.json"


class WealthSnapshotTests(unittest.TestCase):
    def test_existing_sarwa_capture_parses_and_reconciles(self) -> None:
        snapshot = parse_registered_wealth_capture(
            "sarwa",
            "holdings",
            CAPTURE,
            CONFIG,
            adapters_root=ROOT / "browser_adapters",
        )

        self.assertEqual(snapshot.schema_version, 1)
        self.assertEqual(snapshot.provider_id, "sarwa")
        self.assertEqual(snapshot.currency, "USD")
        self.assertEqual(snapshot.total_value, Decimal("1571611.22"))
        self.assertEqual(len(snapshot.portfolios), 5)
        self.assertEqual(snapshot.reconciliation.status, "RECONCILED")
        self.assertEqual(snapshot.reconciliation.difference, Decimal("0.00"))

        personal = snapshot.portfolio("sarwa:invest:personal")
        self.assertEqual(personal.total_value, Decimal("1249330.82"))
        self.assertEqual(personal.cash_value, Decimal("17646.51"))
        self.assertEqual(len(personal.positions), 5)
        self.assertEqual(personal.reconciliation.status, "RECONCILED_WITH_ROUNDING")
        self.assertEqual(personal.reconciliation.difference, Decimal("0.04"))
        self.assertEqual(personal.activity_status, "NO_STABLE_ACTIVITY_ROWS")
        self.assertEqual(personal.cash_flows, ())

        trade = snapshot.portfolio("sarwa:trade:primary")
        self.assertEqual(trade.total_value, Decimal("207588.60"))
        self.assertEqual(trade.reconciliation.status, "COMPONENTS_UNAVAILABLE")

    def test_insurance_coverage_is_explicitly_excluded(self) -> None:
        snapshot = parse_registered_wealth_capture(
            "sarwa", "holdings", CAPTURE, CONFIG,
            adapters_root=ROOT / "browser_adapters",
        )

        self.assertEqual(len(snapshot.exclusions), 1)
        exclusion = snapshot.exclusions[0]
        self.assertEqual(exclusion.kind, "INSURANCE_COVERAGE")
        self.assertEqual(exclusion.amount, Decimal("1300000"))
        self.assertEqual(exclusion.reason, "INSURANCE_COVERAGE_IS_NOT_AN_ASSET")
        self.assertEqual(
            sum((row.total_value for row in snapshot.portfolios), Decimal("0")),
            snapshot.total_value,
        )

    def test_stable_identity_is_registry_backed_not_display_name_backed(self) -> None:
        raw = json.loads(CAPTURE.read_text(encoding="utf-8"))
        raw["invest_accounts"][0]["label"] = "Renamed by user"
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        config["providers"]["sarwa"]["accounts"][0]["capture_labels"].append(
            "Renamed by user"
        )

        snapshot = SarwaProvider(config["providers"]["sarwa"]).parse_capture(raw)

        self.assertEqual(snapshot.portfolios[0].provider_account_id, "sarwa:invest:personal")
        self.assertEqual(snapshot.portfolios[0].display_name, "Renamed by user")

    def test_sensitive_browser_state_is_rejected(self) -> None:
        raw = json.loads(CAPTURE.read_text(encoding="utf-8"))
        raw["source"]["cookies"] = "must-not-persist"
        config = json.loads(CONFIG.read_text(encoding="utf-8"))

        with self.assertRaisesRegex(ValueError, "forbidden sensitive field"):
            SarwaProvider(config["providers"]["sarwa"]).parse_capture(raw)

    def test_position_mismatch_blocks_actual_proposal(self) -> None:
        raw = json.loads(CAPTURE.read_text(encoding="utf-8"))
        raw["invest_accounts"][0]["positions"][0]["market_value_usd"] = "567968.88"
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        snapshot = SarwaProvider(config["providers"]["sarwa"]).parse_capture(raw)

        proposal = build_actual_wealth_proposal(snapshot)

        self.assertEqual(snapshot.portfolios[0].reconciliation.status, "MISMATCH")
        self.assertIn("POSITION_TOTAL_MISMATCH", proposal["blockers"])

    def test_duplicate_instrument_rows_are_rejected(self) -> None:
        raw = json.loads(CAPTURE.read_text(encoding="utf-8"))
        raw["invest_accounts"][0]["positions"].append(
            dict(raw["invest_accounts"][0]["positions"][0])
        )
        config = json.loads(CONFIG.read_text(encoding="utf-8"))

        with self.assertRaisesRegex(ValueError, "Duplicate Sarwa instrument"):
            SarwaProvider(config["providers"]["sarwa"]).parse_capture(raw)

    def test_capability_result_requires_user_assisted_capture_without_sessions(self) -> None:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        capability = SarwaProvider(config["providers"]["sarwa"]).capability()

        self.assertEqual(capability.status, "USER_ASSISTED_REQUIRED")
        self.assertEqual(capability.acquisition_mode, "USER_ASSISTED_BROWSER_CAPTURE")
        self.assertFalse(capability.unattended_refresh_supported)
        self.assertFalse(capability.persist_browser_cookies)
        self.assertFalse(capability.persist_credentials)

    def test_interactive_snapshot_exposes_freshness(self) -> None:
        snapshot = parse_registered_wealth_capture(
            "sarwa", "holdings", CAPTURE, CONFIG,
            adapters_root=ROOT / "browser_adapters",
        )

        self.assertEqual(
            snapshot.freshness_status(datetime(2026, 8, 19, tzinfo=timezone.utc)),
            "FRESH",
        )
        self.assertEqual(
            snapshot.freshness_status(datetime(2026, 8, 25, 0, 0, 1, tzinfo=timezone.utc)),
            "STALE",
        )
        proposal = build_actual_wealth_proposal(
            snapshot,
            evaluated_at=datetime(2026, 8, 25, 0, 0, 1, tzinfo=timezone.utc),
        )
        self.assertIn("WEALTH_SNAPSHOT_STALE", proposal["blockers"])

    def test_actual_proposal_is_blocked_without_an_fx_snapshot(self) -> None:
        snapshot = parse_registered_wealth_capture(
            "sarwa", "holdings", CAPTURE, CONFIG,
            adapters_root=ROOT / "browser_adapters",
        )

        proposal = build_actual_wealth_proposal(snapshot)

        self.assertEqual(proposal["mode"], "PROPOSAL_ONLY")
        self.assertFalse(proposal["actual_writes_allowed"])
        self.assertEqual(proposal["status"], "BLOCKED")
        self.assertIn("FX_SNAPSHOT_REQUIRED", proposal["blockers"])
        self.assertEqual(len(proposal["accounts"]), 4)
        self.assertEqual(len(proposal["excluded_accounts"]), 1)
        self.assertTrue(all(row["offbudget"] for row in proposal["accounts"]))
        self.assertTrue(all(row["initial_balance_minor"] is None for row in proposal["accounts"]))

    def test_fx_snapshot_produces_deterministic_aed_valuation_proposal(self) -> None:
        snapshot = parse_registered_wealth_capture(
            "sarwa", "holdings", CAPTURE, CONFIG,
            adapters_root=ROOT / "browser_adapters",
        )
        fx = FXSnapshot(
            schema_version=1,
            snapshot_id="fx:usd-aed:2026-08-17",
            provider="test-fixture",
            base_currency="USD",
            quote_currency="AED",
            observed_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
            rate=Decimal("3.6725"),
            precision=4,
            max_age_seconds=172800,
            source_identity="fixture:usd-aed:2026-08-17",
        )

        proposal = build_actual_wealth_proposal(snapshot, fx)

        self.assertEqual(proposal["status"], "READY_FOR_REVIEW")
        self.assertEqual(proposal["blockers"], [])
        personal = next(
            row for row in proposal["accounts"]
            if row["provider_account_id"] == "sarwa:invest:personal"
        )
        self.assertEqual(personal["initial_balance_minor"], 458816744)
        self.assertEqual(personal["valuation_strategy"], "AGGREGATE_BALANCE_ADJUSTMENT")
        self.assertTrue(personal["valuation_imported_id"].startswith("wealth:sarwa:"))


if __name__ == "__main__":
    unittest.main()
