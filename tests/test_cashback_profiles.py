from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from finance_tracker.ai_rules import load_ai_policies
from finance_tracker.actual_snapshot import cashback_dashboard
from finance_tracker.cashback import (
    configured_reward_bucket,
    payment_intents_from_config,
    programs_from_config,
    validate_program_configuration,
)
from finance_tracker.models import Transaction


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


if __name__ == "__main__":
    import unittest

    unittest.main()
