from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from finance_tracker.actual_snapshot import cashback_dashboard
from finance_tracker.cashback import PaymentIntent, configured_programs, statement_period
from finance_tracker.models import Transaction, money


ROOT = Path(__file__).resolve().parent.parent


class CashbackRoutingMatrixTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads((ROOT / "config" / "cashback-programs.json").read_text(encoding="utf-8"))

    def dashboard(
        self,
        rows: list[Transaction],
        as_of: date = date(2026, 8, 16),
        *,
        card_periods: bool = False,
    ) -> dict[str, object]:
        programs = configured_programs(as_of)
        return cashback_dashboard(
            programs,
            rows,
            as_of,
            [PaymentIntent("GROCERY", money("100"), "AED", "PHYSICAL_POS")],
            periods_by_card=(
                {
                    program.card: statement_period(as_of, program.statement_close_day)
                    for program in programs
                }
                if card_periods
                else None
            ),
            routing_profiles=self.config["routing_profiles"],
            route_policies=self.config["route_policies"],
        )

    @staticmethod
    def preferred_by_code(dashboard: dict[str, object]) -> dict[str, tuple[str, str]]:
        result = {}
        for graph in dashboard["routing_graphs"]:
            if not graph["active"]:
                continue
            preferred = graph["ranked_cards"][0]
            result[graph["code"]] = (preferred["card"], preferred["bucket"])
        return result

    def test_confirmed_program_cycles_and_sc_fx_cost(self) -> None:
        programs = {program.card: program for program in configured_programs(date(2026, 8, 16))}

        self.assertEqual(self.config["status"], "USER_CONFIRMED")
        self.assertEqual(programs["RAK_WORLD"].statement_close_day, 5)
        self.assertEqual(programs["SC_PLATINUM_X"].statement_close_day, 5)
        self.assertEqual(programs["EI_AMAZON"].statement_close_day, "LAST_DAY")
        self.assertEqual(programs["SC_PLATINUM_X"].fx_cost_rate, Decimal("0.0299"))
        self.assertEqual(programs["EI_AMAZON"].safety_target, None)
        self.assertEqual(programs["EI_AMAZON"].buckets[0].cap_aed, None)
        self.assertEqual(programs["EI_AMAZON"].tracking_mode, "STATEMENT_ONLY")
        self.assertEqual(programs["EI_AMAZON"].position_mode, "UNLIMITED")
        self.assertGreaterEqual(len(self.config["programs"][0]["source_references"]), 4)

    def test_ei_is_unlimited_in_card_position_and_routing(self) -> None:
        dashboard = self.dashboard(self.rak_near_target() + self.sc_reward_buckets(wallet=False))
        ei_card = next(card for card in dashboard["cards"] if card["card"] == "EI_AMAZON")
        amazon = next(graph for graph in dashboard["routing_graphs"] if graph["code"] == "AMAZON")
        ei_route = next(route for route in amazon["ranked_cards"] if route["card"] == "EI_AMAZON")

        self.assertEqual(ei_card["position_mode"], "UNLIMITED")
        self.assertEqual(ei_card["tracking_mode"], "STATEMENT_ONLY")
        self.assertEqual(ei_card["position_headline"], "Unlimited")
        self.assertEqual(ei_route["position_mode"], "UNLIMITED")
        self.assertIsNone(ei_route["bucket_cap_aed"])
        self.assertEqual(ei_route["tier_threshold_aed"], "0")

    @staticmethod
    def rak_near_target() -> list[Transaction]:
        return [
            Transaction("rak-grocery-cap", datetime(2026, 8, 10), "RAK_WORLD", "Groceries", "3000", category="GROCERY", channel="PHYSICAL_POS", reward_bucket="RAK_GROCERY"),
            Transaction("rak-dining-cap", datetime(2026, 8, 11), "RAK_WORLD", "Dining", "3000", category="DINING", channel="PHYSICAL_POS", reward_bucket="RAK_DINING"),
            Transaction("rak-travel-near-cap", datetime(2026, 8, 12), "RAK_WORLD", "Travel", "3950", category="TRAVEL", channel="PHYSICAL_POS", reward_bucket="RAK_TRAVEL"),
        ]

    @staticmethod
    def sc_reward_buckets(*, online: bool = True, wallet: bool = True) -> list[Transaction]:
        rows = []
        if online:
            rows.append(Transaction("sc-online-cap", datetime(2026, 8, 13), "SC_PLATINUM_X", "Online", "4000", category="GENERAL", channel="ONLINE", reward_bucket="SC_ONLINE"))
        if wallet:
            rows.append(Transaction("sc-wallet-cap", datetime(2026, 8, 14), "SC_PLATINUM_X", "Wallet", "2000", category="GENERAL", channel="APPLE_PAY_POS", reward_bucket="SC_WALLET"))
        return rows

    def test_empty_cycle_defaults_preserve_card_roles(self) -> None:
        preferred = self.preferred_by_code(self.dashboard([]))
        expected = {
            "GROCERY": ("RAK_WORLD", "RAK_GROCERY"),
            "DINING": ("RAK_WORLD", "RAK_DINING"),
            "TRAVEL": ("RAK_WORLD", "RAK_TRAVEL"),
            "AMAZON": ("SC_PLATINUM_X", "SC_ONLINE"),
            "ONLINE": ("SC_PLATINUM_X", "SC_ONLINE"),
            "APPLE_PAY": ("SC_PLATINUM_X", "SC_WALLET"),
            "PHYSICAL": ("RAK_WORLD", "RAK_STANDARD"),
            "FOREIGN": ("SC_PLATINUM_X", "SC_FOREIGN"),
            "FILLER": ("RAK_WORLD", "RAK_STANDARD"),
        }
        self.assertEqual(preferred, expected)

    def test_amazon_keeps_unmet_thresholds_on_close_day_and_new_cycle(self) -> None:
        for as_of in (date(2026, 9, 5), date(2026, 9, 6)):
            with self.subTest(as_of=as_of):
                dashboard = self.dashboard([], as_of, card_periods=True)
                amazon = next(
                    graph
                    for graph in dashboard["routing_graphs"]
                    if graph["code"] == "AMAZON"
                )
                self.assertEqual(
                    [candidate["card"] for candidate in amazon["ranked_cards"]],
                    ["SC_PLATINUM_X", "RAK_WORLD"],
                )
                self.assertEqual(
                    [candidate["purpose"] for candidate in amazon["ranked_cards"][:2]],
                    ["THRESHOLD_FILLER", "THRESHOLD_FILLER"],
                )
                self.assertEqual(
                    amazon["ranked_cards"][0]["estimate_basis"],
                    "CONDITIONAL_TARGET_TIER",
                )
                self.assertEqual(
                    amazon["ranked_cards"][0]["current_state_marginal_reward_aed"],
                    "0.00",
                )
                self.assertEqual(
                    amazon["ranked_cards"][0]["conditional_target_reward_aed"],
                    "0.10",
                )
                self.assertEqual(
                    amazon["ranked_cards"][0]["current_state_marginal_return_percent"],
                    "0.00",
                )
                self.assertEqual(
                    amazon["ranked_cards"][0]["current_tier_rate_percent"],
                    "0",
                )
                self.assertEqual(
                    amazon["ranked_cards"][0]["conditional_target_rate_percent"],
                    "10.00",
                )

    def test_amazon_threshold_routing_retains_partial_headroom(self) -> None:
        rows = [
            Transaction(
                "sc-online-near-cap",
                datetime(2026, 9, 4),
                "SC_PLATINUM_X",
                "Online",
                "3950",
                category="GENERAL",
                channel="ONLINE",
                reward_bucket="SC_ONLINE",
            )
        ]
        amazon = next(
            graph
            for graph in self.dashboard(rows, date(2026, 9, 5), card_periods=True)["routing_graphs"]
            if graph["code"] == "AMAZON"
        )
        self.assertEqual(
            [candidate["card"] for candidate in amazon["ranked_cards"]],
            ["SC_PLATINUM_X", "RAK_WORLD"],
        )

    def test_amazon_withholds_unverified_specialist_after_targets_and_caps_are_secured(self) -> None:
        rows = [
            Transaction("sc-online-full", datetime(2026, 9, 2), "SC_PLATINUM_X", "Online", "4000", category="GENERAL", channel="ONLINE", reward_bucket="SC_ONLINE"),
            Transaction("sc-target-secured", datetime(2026, 9, 3), "SC_PLATINUM_X", "Filler", "11300", category="FILLER", channel="PHYSICAL_POS", reward_bucket="SC_FILLER"),
            Transaction("rak-target-secured", datetime(2026, 9, 3), "RAK_WORLD", "Filler", "10300", category="FILLER", channel="PHYSICAL_POS", reward_bucket="RAK_STANDARD"),
        ]
        amazon = next(
            graph
            for graph in self.dashboard(rows, date(2026, 9, 5), card_periods=True)["routing_graphs"]
            if graph["code"] == "AMAZON"
        )
        self.assertEqual(
            [(candidate["card"], candidate["purpose"]) for candidate in amazon["ranked_cards"]],
            [],
        )

    def test_rak_over_and_sc_under_moves_discretionary_spend_to_sc(self) -> None:
        preferred = self.preferred_by_code(self.dashboard(self.rak_near_target()))
        for code in ("GROCERY", "DINING", "ONLINE", "APPLE_PAY", "PHYSICAL", "FOREIGN", "FILLER"):
            with self.subTest(code=code):
                self.assertEqual(preferred[code][0], "SC_PLATINUM_X")
        self.assertEqual(preferred["AMAZON"], ("SC_PLATINUM_X", "SC_ONLINE"))

    def test_sc_online_full_keeps_rak_amazon_threshold_ahead_of_specialist(self) -> None:
        rows = self.rak_near_target() + self.sc_reward_buckets(wallet=False)
        preferred = self.preferred_by_code(self.dashboard(rows))
        for code in ("GROCERY", "DINING", "APPLE_PAY", "FILLER"):
            with self.subTest(code=code):
                self.assertEqual(preferred[code], ("SC_PLATINUM_X", "SC_WALLET"))
        self.assertEqual(preferred["ONLINE"], ("SC_PLATINUM_X", "SC_FILLER"))
        self.assertEqual(preferred["PHYSICAL"], ("SC_PLATINUM_X", "SC_FILLER"))
        self.assertEqual(preferred["AMAZON"], ("RAK_WORLD", "RAK_STANDARD"))

    def test_sc_reward_buckets_full_roll_to_tier_filler(self) -> None:
        rows = self.rak_near_target() + self.sc_reward_buckets()
        preferred = self.preferred_by_code(self.dashboard(rows))
        for code in ("GROCERY", "DINING", "ONLINE", "APPLE_PAY", "PHYSICAL", "FILLER"):
            with self.subTest(code=code):
                self.assertEqual(preferred[code], ("SC_PLATINUM_X", "SC_FILLER"))
        self.assertEqual(preferred["FOREIGN"], ("SC_PLATINUM_X", "SC_FOREIGN"))
        self.assertEqual(preferred["AMAZON"], ("RAK_WORLD", "RAK_STANDARD"))

    def test_sc_target_secured_removes_filler_but_keeps_open_reward_buckets(self) -> None:
        rows = self.rak_near_target() + self.sc_reward_buckets() + [
            Transaction("sc-filler", datetime(2026, 8, 15), "SC_PLATINUM_X", "Filler", "9300", category="FILLER", channel="PHYSICAL_POS", reward_bucket="SC_FILLER"),
        ]
        dashboard = self.dashboard(rows)
        preferred = self.preferred_by_code(dashboard)
        self.assertEqual(preferred["GROCERY"], ("RAK_WORLD", "RAK_EWALLET"))
        self.assertEqual(preferred["ONLINE"], ("RAK_WORLD", "RAK_STANDARD"))
        self.assertEqual(preferred["APPLE_PAY"], ("RAK_WORLD", "RAK_EWALLET"))
        self.assertEqual(preferred["FOREIGN"], ("SC_PLATINUM_X", "SC_FOREIGN"))
        self.assertEqual(preferred["AMAZON"], ("RAK_WORLD", "RAK_STANDARD"))
        for graph in dashboard["routing_graphs"]:
            self.assertNotIn(
                "SC_FILLER",
                {candidate["bucket"] for candidate in graph["ranked_cards"]},
                graph["code"],
            )

    def test_all_targets_secured_disables_filler_profile(self) -> None:
        rows = [
            Transaction("rak-secured", datetime(2026, 8, 15), "RAK_WORLD", "Filler", "10300", category="FILLER", channel="PHYSICAL_POS", reward_bucket="RAK_STANDARD"),
            Transaction("sc-secured", datetime(2026, 8, 15), "SC_PLATINUM_X", "Filler", "15300", category="FILLER", channel="PHYSICAL_POS", reward_bucket="SC_FILLER"),
        ]
        dashboard = self.dashboard(rows)
        filler = next(graph for graph in dashboard["routing_graphs"] if graph["code"] == "FILLER")
        self.assertFalse(filler["active"])
        self.assertEqual(filler["ranked_cards"], [])

    def test_every_preferred_capped_bucket_has_positive_capacity(self) -> None:
        states = [
            [],
            self.rak_near_target(),
            self.rak_near_target() + self.sc_reward_buckets(wallet=False),
            self.rak_near_target() + self.sc_reward_buckets(),
        ]
        for state_index, rows in enumerate(states):
            dashboard = self.dashboard(rows)
            for graph in dashboard["routing_graphs"]:
                if not graph["active"]:
                    continue
                preferred = graph["ranked_cards"][0]
                remaining = preferred["bucket_remaining_aed"]
                with self.subTest(state=state_index, route=graph["code"]):
                    self.assertTrue(
                        remaining is None or Decimal(remaining) > 0,
                        preferred,
                    )

    def test_capacity_routing_ignores_configured_purchase_probe_and_keeps_small_headroom(self) -> None:
        from finance_tracker.actual_snapshot import _build_card_state, _build_routing_graphs
        rows = self.rak_near_target()
        cards, programs, _ = _build_card_state(configured_programs(), rows, date(2026, 8, 16), None, "AED")
        snapshots = []
        for amount in ("0.01", "100", "100000"):
            profiles = [{**profile, "decision_amount_aed": amount} for profile in self.config["routing_profiles"]]
            snapshots.append(_build_routing_graphs(programs, cards, rows, profiles, self.config["route_policies"]))
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertEqual(snapshots[1], snapshots[2])
        travel = next(graph for graph in snapshots[0] if graph["code"] == "TRAVEL")
        route = next(route for route in travel["ranked_cards"] if route["bucket"] == "RAK_TRAVEL")
        self.assertEqual(route["bucket_remaining_aed"], "50")
        self.assertEqual(route["tier_before"], route["tier_after"])
        self.assertEqual(travel["routing_basis"], "AVAILABLE_CAPACITY")

    def test_avoid_cards_never_include_the_preferred_card(self) -> None:
        for rows in (self.rak_near_target(), self.rak_near_target() + self.sc_reward_buckets()):
            dashboard = self.dashboard(rows)
            for graph in dashboard["routing_graphs"]:
                if not graph["active"]:
                    continue
                preferred_card = graph["ranked_cards"][0]["card"]
                with self.subTest(route=graph["code"], preferred=preferred_card):
                    self.assertNotIn(preferred_card, graph["avoid_cards"])
