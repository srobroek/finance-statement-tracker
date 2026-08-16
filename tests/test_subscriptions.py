from datetime import datetime
from decimal import Decimal
from unittest import TestCase

from finance_tracker.models import Transaction
from finance_tracker.subscriptions import detect_recurring_subscriptions


def txn(identifier: str, when: str, vendor: str, amount: str, *, category: str = "Subscriptions") -> Transaction:
    return Transaction(
        transaction_id=identifier,
        transaction_at=datetime.fromisoformat(when),
        card="TEST",
        merchant_raw=vendor,
        vendor=vendor,
        amount_aed=Decimal(amount),
        category=category,
        is_subscription=True,
    )


class SubscriptionTests(TestCase):
    def test_monthly_non_utility_subscription_is_detected(self) -> None:
        result = detect_recurring_subscriptions(
            [
                txn("1", "2026-05-02", "Cloud Service", "49"),
                txn("2", "2026-06-02", "Cloud Service", "49"),
                txn("3", "2026-07-02", "Cloud Service", "49"),
            ]
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].frequency, "Monthly")
        self.assertEqual(result[0].expected_amount_aed, Decimal("49.00"))

    def test_utilities_are_not_promoted_to_subscriptions(self) -> None:
        result = detect_recurring_subscriptions(
            [
                txn("1", "2026-05-02", "DEWA", "600", category="Utilities"),
                txn("2", "2026-06-02", "DEWA", "610", category="Utilities"),
                txn("3", "2026-07-02", "DEWA", "590", category="Utilities"),
            ]
        )
        self.assertEqual(result, [])
