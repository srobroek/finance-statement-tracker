from __future__ import annotations

from copy import deepcopy
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from jsonschema import Draft202012Validator

from finance_tracker.ai_rules import load_ai_policies
from finance_tracker.actual_snapshot import cashback_dashboard, transactions_from_actual_snapshot
from finance_tracker.cashback import (
    configured_reward_bucket,
    payment_intents_from_config,
    programs_from_config,
    validate_program_configuration,
)
from finance_tracker.models import Transaction
from finance_tracker.models import CashbackPeriod, period_for_timestamp, validate_cashback_state


ROOT = Path(__file__).resolve().parent.parent
PROFILES = ROOT / "examples" / "cashback-profiles"


def load_profile(name: str) -> dict[str, object]:
    return json.loads((PROFILES / name).read_text(encoding="utf-8"))


def dashboard(
    source: dict[str, object],
    as_of: date,
    rows: list[Transaction] | None = None,
) -> dict[str, object]:
    return cashback_dashboard(
        programs_from_config(source, as_of),
        rows or [],
        as_of,
        payment_intents_from_config(source),
        routing_profiles=source.get("routing_profiles") or (),
        route_policies=source.get("route_policies") or None,
        base_currency=str(source.get("currency") or "AED"),
    )


def cashback_state() -> dict[str, object]:
    return {
        "schema_version": 1,
        "receipts": [{
            "receipt_id": "receipt-1",
            "source_identity": "mail:message-1",
            "card_code": "CARD_A",
            "original_received_at": "2026-09-01T00:00:00Z",
            "period_id": "period-1",
        }],
        "periods": [
            {
                "period_id": "period-1",
                "card_code": "CARD_A",
                "period_start": "2026-08-01T00:00:00Z",
                "period_end": "2026-09-01T00:00:00Z",
                "status": "CLOSED",
                "closed_by_receipt_id": "receipt-1",
            },
            {
                "period_id": "period-2",
                "card_code": "CARD_A",
                "period_start": "2026-09-01T00:00:00Z",
                "period_end": "2026-10-01T00:00:00Z",
                "status": "OPEN",
                "closed_by_receipt_id": None,
            },
        ],
        "memberships": [{"card_code": "CARD_A", "coverage": "UNKNOWN", "sc_held": None}],
        "accounting": [{
            "period_id": "period-1",
            "card_code": "CARD_A",
            "qualifying_spend": "100",
            "refund_deductions": "5",
            "consumed_cap_headroom": "20",
        }],
        "category_assessments": [{
            "transaction_id": "tx-1",
            "status": "UNRESOLVED",
            "category": None,
            "review_required": True,
            "reason": "missing evidence",
        }],
        "fx_snapshots": [{
            "schema_version": 1,
            "snapshot_id": "fx-1",
            "provider": "RAK",
            "base_currency": "AED",
            "quote_currency": "USD",
            "observed_at": "2026-09-01T01:00:00Z",
            "quote_date": "2026-09-01",
            "quote_basis": "BASE_PER_QUOTE",
            "rate": "3.69",
            "precision": 5,
            "max_age_seconds": 3600,
            "source_identity": "rak:2026-09-01",
            "uncertainty": "ESTIMATE",
        }],
    }


class PublicCashbackProfileTests(TestCase):
    def test_all_public_example_profiles_validate(self) -> None:
        for path in sorted(PROFILES.glob("*.json")):
            with self.subTest(profile=path.name):
                validate_program_configuration(json.loads(path.read_text(encoding="utf-8")))

    def test_flat_rate_profile_routes_travel_and_everyday_without_issuer_assumptions(self) -> None:
        result = dashboard(load_profile("flat-rate-usd.json"), date(2026, 5, 12))
        routes = {item["code"]: item for item in result["routing_graphs"]}

        self.assertEqual(result["currency"], "USD")
        self.assertEqual(routes["GENERAL"]["use_card"], "EVERYDAY_2")
        self.assertEqual(routes["TRAVEL"]["use_card"], "TRAVEL_4")
        self.assertEqual({card["short_name"] for card in result["cards"]}, {"Everyday", "Travel"})

    def test_profile_authoring_rejects_runtime_arrays(self) -> None:
        for version in (1, 2):
            schema = json.loads((ROOT / "config" / f"cashback-profile-schema-v{version}.json").read_text())
            invalid = load_profile("flat-rate-usd.json")
            invalid["schema_version"] = version
            invalid["live_ingestion"] = {"receipts": []}
            self.assertTrue(list(Draft202012Validator(schema).iter_errors(invalid)))

    def test_cashback_state_validates_independent_contracts(self) -> None:
        validate_cashback_state(cashback_state())
        periods = (
            CashbackPeriod("period-1", "CARD_A", datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)),
            CashbackPeriod("period-2", "CARD_A", datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC)),
        )
        self.assertEqual(period_for_timestamp(periods, datetime(2026, 9, 1, tzinfo=UTC)), periods[1])

    def test_cashback_state_rejects_closed_period_with_missing_receipt(self) -> None:
        missing_receipt = deepcopy(cashback_state())
        missing_receipt["periods"][0]["closed_by_receipt_id"] = "missing-receipt"
        with self.assertRaisesRegex(ValueError, "unknown receipt"):
            validate_cashback_state(missing_receipt)

    def test_cashback_state_rejects_open_period_closure_receipt(self) -> None:
        open_period = deepcopy(cashback_state())
        open_period["periods"][1]["closed_by_receipt_id"] = "receipt-1"
        with self.assertRaises(ValueError):
            validate_cashback_state(open_period)
        with self.assertRaises(ValueError):
            CashbackPeriod(
                "period-2",
                "CARD_A",
                datetime(2026, 9, 1, tzinfo=UTC),
                datetime(2026, 10, 1, tzinfo=UTC),
                status="OPEN",
                closed_by_receipt_id="receipt-1",
            )

    def test_cashback_state_rejects_ambiguous_accounting_and_fx_dates(self) -> None:
        negative = deepcopy(cashback_state())
        negative["accounting"][0]["qualifying_spend"] = "-1"
        with self.assertRaises(ValueError):
            validate_cashback_state(negative)
        ambiguous = deepcopy(cashback_state())
        ambiguous["accounting"][0]["net_spend"] = "95"
        with self.assertRaises(ValueError):
            validate_cashback_state(ambiguous)
        future_quote = deepcopy(cashback_state())
        future_quote["fx_snapshots"][0]["quote_date"] = "2026-09-02"
        with self.assertRaises(ValueError):
            validate_cashback_state(future_quote)

    def test_tiered_profile_caps_category_then_routes_to_tier_card(self) -> None:
        source = load_profile("tiered-gbp.json")
        open_result = dashboard(source, date(2026, 5, 12))
        open_grocery = next(item for item in open_result["routing_graphs"] if item["code"] == "GROCERY")
        self.assertEqual(open_grocery["use_card"], "GROCERY_5")

        capped_result = dashboard(
            source,
            date(2026, 5, 12),
            [Transaction(
                "fictional-grocery-cap",
                datetime(2026, 5, 10),
                "GROCERY_5",
                "Fictional grocer",
                "400",
                currency="GBP",
                channel="PHYSICAL_POS",
                category="GROCERY",
                reward_bucket="GROCERY",
            )],
        )
        capped_grocery = next(item for item in capped_result["routing_graphs"] if item["code"] == "GROCERY")
        self.assertEqual(capped_grocery["use_card"], "STEP_UP")

    def test_weekly_display_pace_and_cycle_routing_pace_are_independent(self) -> None:
        source = load_profile("tiered-gbp.json")
        result = dashboard(
            source,
            date(2026, 5, 16),
            [Transaction(
                "front-loaded-cycle",
                datetime(2026, 5, 8),
                "STEP_UP",
                "Front-loaded spend",
                "900",
                currency="GBP",
                channel="PHYSICAL_POS",
                category="GENERAL",
                reward_bucket="ALL",
            )],
        )
        step_up = next(card for card in result["cards"] if card["card"] == "STEP_UP")

        self.assertEqual(step_up["pace"]["basis"], "WEEKLY")
        self.assertEqual(step_up["pace"]["week_number"], 3)
        self.assertEqual(step_up["pace"]["status"], "UNDER")
        self.assertEqual(step_up["pace"]["cycle_status"], "OVER")
        self.assertEqual(step_up["pace"]["routing_status"], "OVER")

    def test_rotating_profile_changes_category_without_code_changes(self) -> None:
        source = load_profile("rotating-eur.json")
        first_half = {item["code"]: item for item in dashboard(source, date(2026, 3, 12))["routing_graphs"]}
        second_half = {item["code"]: item for item in dashboard(source, date(2026, 9, 12))["routing_graphs"]}

        self.assertEqual(first_half["FUEL"]["use_card"], "SEASONAL")
        self.assertEqual(first_half["DINING"]["use_card"], "BASELINE")
        self.assertEqual(second_half["FUEL"]["use_card"], "BASELINE")
        self.assertEqual(second_half["DINING"]["use_card"], "SEASONAL")

    def test_bucket_assignment_is_profile_driven(self) -> None:
        source = load_profile("flat-rate-usd.json")
        programs = programs_from_config(source, date(2026, 5, 12))

        self.assertEqual(
            configured_reward_bucket(programs, "TRAVEL_4", "TRAVEL", "ONLINE", "USD"),
            "TRAVEL",
        )
        self.assertEqual(
            configured_reward_bucket(programs, "EVERYDAY_2", "GENERAL", "PHYSICAL_POS", "USD"),
            "ALL",
        )

    def test_tier_can_require_both_total_and_bucket_spend(self) -> None:
        source = load_profile("requirements-cad.json")
        program = next(
            item
            for item in programs_from_config(source, date(2026, 5, 12))
            if item.card == "HYBRID_REQUIREMENTS"
        )

        self.assertEqual(
            program.tier_for(1000, {"DOMESTIC": 900, "FOREIGN": 100}).code,
            "BASE",
        )
        self.assertEqual(
            program.tier_for(1000, {"DOMESTIC": 800, "FOREIGN": 200}).code,
            "PREMIUM",
        )

    def test_invalid_profile_rejects_unknown_route_bucket(self) -> None:
        source = load_profile("flat-rate-usd.json")
        source["routing_profiles"][0]["routes"][0]["bucket"] = "MISSING"

        with self.assertRaisesRegex(ValueError, "unknown bucket"):
            validate_program_configuration(source)

    def test_ai_reward_bucket_allowlist_is_derived_from_deployed_profile(self) -> None:
        profile_path = PROFILES / "flat-rate-usd.json"
        with patch.dict(
            os.environ,
            {"CASHBACK_PROGRAM_CONFIG_PATH": str(profile_path)},
            clear=False,
        ):
            policy = next(
                item
                for item in load_ai_policies(ROOT / "config" / "ai-policies.json")
                if item.policy_id == "enrich-cashback-classification"
            )

        self.assertEqual(set(policy.allowed_values["reward_bucket"]), {"ALL", "TRAVEL"})

    def test_actual_snapshot_defaults_to_profile_currency_not_original_portfolio_currency(self) -> None:
        cashback_profile = load_profile("flat-rate-usd.json")
        rows = transactions_from_actual_snapshot(
            {
                "transactions": [{
                    "id": "actual-fictional-1",
                    "account_name": "Fictional Everyday",
                    "date": "2026-05-12",
                    "amount": -2500,
                    "imported_payee": "Example Store",
                    "category_name": "General",
                    "notes": "",
                    "cleared": True,
                }],
            },
            {
                "currency": "USD",
                "accounts": [{
                    "name": "Fictional Everyday",
                    "card_code": "EVERYDAY_2",
                    "card_last4": [],
                }],
            },
            cashback_profile,
        )

        self.assertEqual(rows[0].currency, "USD")


if __name__ == "__main__":
    import unittest

    unittest.main()
