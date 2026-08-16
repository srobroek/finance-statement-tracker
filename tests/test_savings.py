from decimal import Decimal
from unittest import TestCase

from finance_tracker.savings import SavingsAllocation, account_liquidity


class SavingsTests(TestCase):
    def test_reserved_goals_reduce_safe_cash_without_moving_money(self) -> None:
        liquidity = account_liquidity(
            "WIO-4113",
            Decimal("25000"),
            [
                SavingsAllocation("Emergency", "WIO-4113", Decimal("9000")),
                SavingsAllocation("Travel", "WIO-4113", Decimal("2500")),
                SavingsAllocation("Paused", "WIO-4113", Decimal("1000"), active=False),
                SavingsAllocation("Other bank", "ADCB", Decimal("500")),
            ],
        )
        self.assertEqual(liquidity.gross_balance_aed, Decimal("25000"))
        self.assertEqual(liquidity.reserved_savings_aed, Decimal("11500"))
        self.assertEqual(liquidity.safe_to_spend_aed, Decimal("13500"))

    def test_negative_reserve_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SavingsAllocation("Invalid", "WIO", Decimal("-1"))
