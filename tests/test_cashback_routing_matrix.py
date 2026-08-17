from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from finance_tracker.actual_snapshot import cashback_dashboard
from finance_tracker.cashback import PaymentIntent, poc_programs
from finance_tracker.models import Transaction, money


ROOT = Path(__file__).resolve().parent.parent


class CashbackRoutingMatrixTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads((ROOT / "config" / "cashback-programs.json").read_text(encoding="utf-8"))

    def dashboard(self, rows: list[Transaction]) -> dict[str, object]:
        return cashback_dashboard(
            poc_programs(),
            rows,
            date(2026, 8, 16),
            [PaymentIntent("GROCERY", money("100"), "AED", "PHYSICAL_POS")],
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
        programs = {program.card: program for program in poc_programs()}

        self.assertEqual(self.config["status"], "USER_CONFIRMED")
        self.assertEqual(programs["RAK_WORLD"].statement_close_day, 5)
        self.assertEqual(programs["SC_PLATINUM_X"].statement_close_day, 5)
        self.assertEqual(programs["EI_AMAZON"].statement_close_day, "LAST_DAY")
        self.assertEqual(programs["SC_PLATINUM_X"].fx_cost_rate, Decimal("0.0299"))
        self.assertEqual(programs["EI_AMAZON"].safety_target, None)
        self.assertEqual(programs["EI_AMAZON"].buckets[0].cap_aed, None)
        self.assertGreaterEqual(len(self.config["programs"][0]["source_references"]), 4)

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

    def test_rak_over_and_sc_under_moves_discretionary_spend_to_sc(self) -> None:
        preferred = self.preferred_by_code(self.dashboard(self.rak_near_target()))
        for code in ("GROCERY", "DINING", "TRAVEL", "ONLINE", "APPLE_PAY", "PHYSICAL", "FOREIGN", "FILLER"):
            with self.subTest(code=code):
                self.assertEqual(preferred[code][0], "SC_PLATINUM_X")
        self.assertEqual(preferred["AMAZON"], ("SC_PLATINUM_X", "SC_ONLINE"))

    def test_sc_online_full_rolls_to_wallet_filler_and_amazon_specialist(self) -> None:
        rows = self.rak_near_target() + self.sc_reward_buckets(wallet=False)
        preferred = self.preferred_by_code(self.dashboard(rows))
        for code in ("GROCERY", "DINING", "TRAVEL", "APPLE_PAY", "FILLER"):
            with self.subTest(code=code):
                self.assertEqual(preferred[code], ("SC_PLATINUM_X", "SC_WALLET"))
        self.assertEqual(preferred["ONLINE"], ("SC_PLATINUM_X", "SC_FILLER"))
        self.assertEqual(preferred["PHYSICAL"], ("SC_PLATINUM_X", "SC_FILLER"))
        self.assertEqual(preferred["AMAZON"], ("EI_AMAZON", "EI_AMAZON"))

    def test_sc_reward_buckets_full_roll_to_tier_filler(self) -> None:
        rows = self.rak_near_target() + self.sc_reward_buckets()
        preferred = self.preferred_by_code(self.dashboard(rows))
        for code in ("GROCERY", "DINING", "TRAVEL", "ONLINE", "APPLE_PAY", "PHYSICAL", "FILLER"):
            with self.subTest(code=code):
                self.assertEqual(preferred[code], ("SC_PLATINUM_X", "SC_FILLER"))
        self.assertEqual(preferred["FOREIGN"], ("SC_PLATINUM_X", "SC_FOREIGN"))
        self.assertEqual(preferred["AMAZON"], ("EI_AMAZON", "EI_AMAZON"))

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
        self.assertEqual(preferred["AMAZON"], ("EI_AMAZON", "EI_AMAZON"))
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

    def test_every_preferred_capped_bucket_fits_the_decision_amount(self) -> None:
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
                        remaining is None or Decimal(remaining) >= Decimal("100"),
                        preferred,
                    )

    def test_avoid_cards_never_include_the_preferred_card(self) -> None:
        for rows in (self.rak_near_target(), self.rak_near_target() + self.sc_reward_buckets()):
            dashboard = self.dashboard(rows)
            for graph in dashboard["routing_graphs"]:
                if not graph["active"]:
                    continue
                preferred_card = graph["ranked_cards"][0]["card"]
                with self.subTest(route=graph["code"], preferred=preferred_card):
                    self.assertNotIn(preferred_card, graph["avoid_cards"])
