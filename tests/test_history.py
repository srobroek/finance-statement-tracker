from datetime import datetime
from unittest import TestCase

from finance_tracker.history import apply_history_match, build_history_index, merchant_fingerprint
from finance_tracker.models import Transaction


class HistoryMatchingTests(TestCase):
    def test_consistent_reviewed_history_enriches_only_unresolved_fields(self) -> None:
        history_rows = [
            Transaction(
                str(index),
                datetime(2026, 7, index),
                "CARD",
                f"CARREFOUR DUBAI POS {index}",
                "10",
                vendor="Carrefour",
                category="Groceries",
                channel="PHYSICAL_POS",
                tags={"grocery"},
            )
            for index in (1, 2, 3)
        ]
        target = Transaction(
            "new",
            datetime(2026, 8, 1),
            "CARD",
            "CARREFOUR UAE POS 9911",
            "20",
            category="Manual Category",
        )
        target.metadata["locked_fields"] = ["category"]

        trace = apply_history_match(target, build_history_index(history_rows),)

        self.assertEqual(merchant_fingerprint(target.merchant_raw), "CARREFOUR")
        self.assertEqual(target.vendor, "Carrefour")
        self.assertEqual(target.category, "Manual Category")
        self.assertEqual(target.channel, "PHYSICAL_POS")
        self.assertIn("grocery", target.tags)
        self.assertEqual(target.metadata["history_count"], 3)
        self.assertIsNotNone(trace)

    def test_ambiguous_history_does_not_select_a_category(self) -> None:
        rows = [
            Transaction("1", datetime(2026, 7, 1), "CARD", "EXAMPLE 1", "10", category="Dining Out"),
            Transaction("2", datetime(2026, 7, 2), "CARD", "EXAMPLE 2", "10", category="Groceries"),
        ]
        self.assertNotIn("EXAMPLE", build_history_index(rows))
