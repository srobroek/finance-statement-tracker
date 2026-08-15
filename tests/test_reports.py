from datetime import date, datetime
from unittest import TestCase

from finance_tracker.models import Transaction
from finance_tracker.reports import evaluate_month_close, month_close_markdown


class ReportTests(TestCase):
    def test_month_close_waits_for_period_end_and_statements(self) -> None:
        cards = ["RAK_WORLD", "SC_PLATINUM_X"]
        before_end = evaluate_month_close(
            date(2026, 8, 31),
            date(2026, 8, 31),
            cards,
            {"RAK_WORLD": "RECEIVED", "SC_PLATINUM_X": "RECEIVED"},
        )
        self.assertEqual(before_end.status, "WAITING_FOR_PERIOD_END")

        missing = evaluate_month_close(
            date(2026, 9, 7),
            date(2026, 8, 31),
            cards,
            {"RAK_WORLD": "RECEIVED", "SC_PLATINUM_X": "EXPECTED"},
        )
        self.assertFalse(missing.eligible)
        self.assertEqual(missing.missing_statements, ("SC_PLATINUM_X",))
        self.assertTrue(missing.grace_period_exceeded)

        ready = evaluate_month_close(
            date(2026, 9, 2),
            date(2026, 8, 31),
            cards,
            {"RAK_WORLD": "RECEIVED", "SC_PLATINUM_X": "NOT_REQUIRED"},
        )
        self.assertTrue(ready.eligible)
        self.assertEqual(ready.status, "READY")

    def test_month_close_contains_static_mermaid_and_table(self) -> None:
        transactions = [
            Transaction("1", datetime(2026, 8, 1), "RAK_WORLD", "Market", "100", category="Groceries"),
            Transaction("2", datetime(2026, 8, 2), "RAK_WORLD", "Cafe", "50", category="Dining"),
        ]
        output = month_close_markdown(transactions, "2026-08")
        self.assertIn("```mermaid", output)
        self.assertIn('"Groceries" : 100.00', output)
        self.assertIn("| Dining | 50.00 |", output)
