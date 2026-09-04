from datetime import datetime
from unittest import TestCase

from finance_tracker.classification_audit import (
    build_classification_exception_report,
    enforce_transaction_invariants,
)
from finance_tracker.models import Transaction


class ClassificationAuditRegressionTests(TestCase):
    def transaction(self) -> Transaction:
        return Transaction("tx", datetime(2026, 8, 1), "CARD", "UNKNOWN", "10")

    def test_plural_ai_category_recommendations_are_reported_as_pending(self) -> None:
        transaction = self.transaction()
        transaction.metadata["category_recommendations"] = [{"name": "Groceries"}]

        report = build_classification_exception_report([transaction])

        self.assertIn(
            "CATEGORY_RECOMMENDATION_PENDING",
            report["exceptions"][0]["reasons"],
        )

    def test_invariants_preserve_manually_locked_review_fields(self) -> None:
        transaction = self.transaction()
        transaction.tags = {"Manual"}
        transaction.metadata["locked_fields"] = ["tags", "review_required"]

        reasons = enforce_transaction_invariants(transaction)

        self.assertIn("UNCATEGORIZED", reasons)
        self.assertEqual(transaction.tags, {"Manual"})
        self.assertFalse(transaction.review_required)

