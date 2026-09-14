from datetime import UTC, datetime
from decimal import Decimal
from unittest import TestCase

from finance_tracker.ai_rules import AIEnrichmentEngine, AIPolicy
from finance_tracker.classification_audit import (
    build_classification_exception_report,
    enforce_transaction_invariants,
)
from finance_tracker.models import Transaction


class ClassificationAuditRegressionTests(TestCase):
    def transaction(self) -> Transaction:
        return Transaction(
            "tx",
            datetime(2026, 8, 1, tzinfo=UTC),
            "CARD",
            "UNKNOWN",
            Decimal(10),
        )

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
        self.assertIn(
            "UNCATEGORIZED",
            transaction.metadata["classification_review_reasons"],
        )

    def test_locked_tags_do_not_suppress_unresolved_review_queue(self) -> None:
        transaction = self.transaction()
        transaction.tags = {"Manual"}
        transaction.metadata["locked_fields"] = ["tags"]

        reasons = enforce_transaction_invariants(transaction)

        self.assertTrue(transaction.review_required)
        self.assertEqual(transaction.tags, {"Manual"})
        self.assertIn("UNCATEGORIZED", reasons)

    def test_resolution_locks_do_not_suppress_source_review_queue(self) -> None:
        transaction = self.transaction()
        transaction.vendor = "Merchant"
        transaction.category = "Shopping"
        transaction.metadata.update(
            {
                "browser_review_reasons": ["SOURCE_REVIEW_REQUIRED"],
                "category_resolution": "MANUAL_CATEGORY_STATE",
                "payee_resolution": "MANUAL_PAYEE_STATE",
                "locked_fields": ["category_resolution", "payee_resolution"],
            }
        )

        reasons = enforce_transaction_invariants(transaction)

        self.assertEqual(
            transaction.metadata["category_resolution"],
            "MANUAL_CATEGORY_STATE",
        )
        self.assertEqual(
            transaction.metadata["payee_resolution"],
            "MANUAL_PAYEE_STATE",
        )
        self.assertTrue(transaction.review_required)
        self.assertIn("needs-review", transaction.tags)
        self.assertEqual(reasons, ("SOURCE_REVIEW_REQUIRED",))
        self.assertEqual(
            transaction.metadata["classification_review_reasons"],
            ["SOURCE_REVIEW_REQUIRED"],
        )

    def test_ai_resolution_preserves_independent_source_review_reasons(self) -> None:
        transaction = Transaction(
            "source-review",
            datetime(2026, 8, 16, tzinfo=UTC),
            "SC_PLATINUM_X",
            "UNKNOWN MERCHANT",
            Decimal(100),
            vendor="unknown",
            category="Needs Review",
            tags={"category-review", "needs-review", "reimbursement"},
            review_required=True,
            metadata={
                "browser_review_reasons": [
                    "SOURCE_REVIEW_REQUIRED",
                    "MISSING_AED_EQUIVALENT",
                    "VISIBLE_ROWS_REQUIRE_REVIEW",
                ],
                "reimbursement_match_status": "UNMATCHED",
            },
        )
        policy = AIPolicy(
            policy_id="resolve-classification",
            name="Resolve classification",
            priority=1,
            instruction="Resolve category and vendor",
            target_fields=("category", "vendor"),
        )

        traces = AIEnrichmentEngine([policy]).enrich(
            transaction,
            lambda _request: {
                "proposals": [
                    {
                        "field": "category",
                        "value": "Online Shopping",
                        "confidence": 0.99,
                    },
                    {
                        "field": "vendor",
                        "value": "Amazon",
                        "confidence": 0.99,
                    },
                ]
            },
        )

        self.assertTrue(all(trace.accepted for trace in traces))
        self.assertEqual(transaction.category, "Online Shopping")
        self.assertEqual(transaction.vendor, "Amazon")
        self.assertEqual(
            transaction.metadata["browser_review_reasons"],
            [
                "SOURCE_REVIEW_REQUIRED",
                "MISSING_AED_EQUIVALENT",
                "VISIBLE_ROWS_REQUIRE_REVIEW",
            ],
        )
        self.assertEqual(
            transaction.metadata["classification_review_reasons"],
            [
                "MISSING_AED_EQUIVALENT",
                "SOURCE_REVIEW_REQUIRED",
                "UNMATCHED_REIMBURSEMENT",
                "VISIBLE_ROWS_REQUIRE_REVIEW",
            ],
        )
        self.assertTrue(transaction.review_required)
        self.assertIn("needs-review", transaction.tags)
        self.assertIn("reimbursement", transaction.tags)
