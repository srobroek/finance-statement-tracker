from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from finance_tracker.planning import SchedulePolicy, recommend_category_budgets, recommend_schedules


class PlanningTests(unittest.TestCase):
    def test_budget_recommendations_ignore_current_month_transfers_and_reviews(self) -> None:
        rows = []
        for month, amount in (("2026-01", -10000), ("2026-02", -12000), ("2026-03", -11000)):
            rows.append({"date": f"{month}-05", "amount": amount, "category_name": "Groceries"})
        rows.extend(
            [
                {"date": "2026-04-05", "amount": -999999, "category_name": "Groceries"},
                {"date": "2026-03-05", "amount": -500000, "category_name": "Card Payments"},
                {"date": "2026-03-06", "amount": -500000, "category_name": "Needs Review"},
            ]
        )

        result = recommend_category_budgets(
            rows,
            as_of=date(2026, 4, 15),
            minimum_months=3,
            buffer_percent=Decimal("10"),
            round_to_minor=5000,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "Groceries")
        self.assertEqual(result[0]["recommended_minor"], 15000)

    def test_schedule_recommendations_are_allowlisted_and_use_ranges(self) -> None:
        rows = [
            {
                "date": f"2026-0{month}-1{month}",
                "amount": -amount,
                "category_name": "Electricity & Water",
                "payee_name": "DEWA",
                "account_name": "Current Account",
            }
            for month, amount in ((1, 50000), (2, 70000), (3, 60000))
        ]
        rows.append(
            {
                "date": "2026-03-12",
                "amount": -10000,
                "category_name": "Dining Out",
                "payee_name": "Restaurant",
                "account_name": "Current Account",
            }
        )

        result = recommend_schedules(
            rows,
            [SchedulePolicy("DEWA", ("DEWA",), ("Electricity & Water",))],
            as_of=date(2026, 3, 20),
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["amount_op"], "isbetween")
        self.assertEqual(result[0]["amount_min_minor"], 50000)
        self.assertEqual(result[0]["amount_max_minor"], 70000)
        self.assertEqual(result[0]["date"]["frequency"], "monthly")


if __name__ == "__main__":
    unittest.main()
