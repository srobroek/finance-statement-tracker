from datetime import date, datetime
from decimal import Decimal
from unittest import TestCase

from finance_tracker.models import Transaction
from finance_tracker.reporting import ReportFilter, breakdown


class ReportingTests(TestCase):
    def test_shared_owner_report_reuses_canonical_transactions(self) -> None:
        rows = [
            Transaction("1", datetime(2026, 8, 1), "CARD", "SHOP", Decimal("100"), account="Joint", owner="A", vendor="Shop", category="Shopping", tags={"Shared"}),
            Transaction("2", datetime(2026, 8, 2), "CARD", "CAFE", Decimal("25"), account="Joint", owner="A", vendor="Cafe", category="Dining", tags={"Shared"}),
            Transaction("3", datetime(2026, 7, 2), "CARD", "OLD", Decimal("50"), account="Joint", owner="A", vendor="Old", category="Dining", tags={"Shared"}),
            Transaction("4", datetime(2026, 8, 3), "CARD", "PRIVATE", Decimal("10"), account="Joint", owner="A", vendor="Shop", category="Shopping"),
        ]
        report = breakdown(
            rows,
            dimension="category",
            report_filter=ReportFilter(start=date(2026, 8, 1), owners=frozenset({"A"}), tags=frozenset({"Shared"})),
        )
        self.assertEqual([(row.key, row.spend_aed) for row in report], [("Shopping", Decimal("100")), ("Dining", Decimal("25"))])

    def test_refunds_reduce_net_but_not_positive_spend(self) -> None:
        rows = [
            Transaction("1", datetime(2026, 8, 1), "CARD", "SHOP", Decimal("100"), vendor="Shop", category="Shopping"),
            Transaction("2", datetime(2026, 8, 2), "CARD", "REFUND", Decimal("20"), vendor="Shop", category="Shopping", is_refund=True, transaction_type="REFUND"),
        ]
        report = breakdown(rows, dimension="vendor")[0]
        self.assertEqual(report.net_aed, Decimal("80"))
        self.assertEqual(report.spend_aed, Decimal("100"))

    def test_tag_filters_support_any_all_and_none_case_insensitively(self) -> None:
        row = Transaction(
            "tagged",
            datetime(2026, 8, 1),
            "CARD",
            "SHOP",
            Decimal("100"),
            category="Shopping",
            tags={"Shared", "Rental"},
        )

        self.assertTrue(ReportFilter(tags_any=frozenset({"business", "shared"})).matches(row))
        self.assertTrue(ReportFilter(tags_all=frozenset({"shared", "rental"})).matches(row))
        self.assertFalse(ReportFilter(tags_none=frozenset({"rental"})).matches(row))
