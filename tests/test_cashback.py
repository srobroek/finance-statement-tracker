from datetime import date, datetime
from decimal import Decimal
from unittest import TestCase

from finance_tracker.cashback import PaymentIntent, pace_status, poc_programs, recommend, reward_total
from finance_tracker.models import Transaction


class CashbackTests(TestCase):
    def test_sc_tier_jump_values_existing_spend(self) -> None:
        transactions = [
            Transaction("o", datetime(2026, 8, 1), "SC_PLATINUM_X", "Online", "4000", channel="ONLINE", category="GENERAL", reward_bucket="SC_ONLINE"),
            Transaction("w", datetime(2026, 8, 2), "SC_PLATINUM_X", "Wallet", "2000", channel="APPLE_PAY_POS", category="GENERAL", reward_bucket="SC_WALLET"),
            Transaction("f", datetime(2026, 8, 3), "SC_PLATINUM_X", "Foreign", "4000", currency="USD", category="GENERAL", reward_bucket="SC_FOREIGN"),
            Transaction("x", datetime(2026, 8, 4), "SC_PLATINUM_X", "Other online", "3000", channel="ONLINE", category="GENERAL", reward_bucket="SC_ONLINE"),
        ]
        result = recommend(poc_programs(), transactions, PaymentIntent("GENERAL", Decimal("2000"), "AED", "ONLINE"))
        sc = next(value for value in result.ranked if value.card == "SC_PLATINUM_X")
        self.assertEqual(sc.tier_before, "TIER_5")
        self.assertEqual(sc.tier_after, "TIER_10")
        self.assertGreater(sc.marginal_reward_aed, Decimal("300"))

    def test_refund_reduces_reward(self) -> None:
        program = next(program for program in poc_programs() if program.card == "SC_PLATINUM_X")
        before = reward_total(program, Decimal("15000"), {"SC_ONLINE": Decimal("4000")})
        after = reward_total(program, Decimal("14500"), {"SC_ONLINE": Decimal("3500")})
        self.assertLess(after, before)

    def test_pace_status(self) -> None:
        status = pace_status(Decimal("3000"), Decimal("10300"), date(2026, 8, 21))
        self.assertEqual(status.status, "UNDER")

