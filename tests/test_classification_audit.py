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

    def test_locked_reasons_do_not_block_source_queue_recomputation(self) -> None:
        transaction = Transaction(
            "locked-reasons",
            datetime(2026, 8, 16, tzinfo=UTC),
            "SC_PLATINUM_X",
            "KNOWN MERCHANT",
            Decimal(100),
            vendor="Merchant",
            category="Needs Review",
            tags={"category-review", "needs-review"},
            review_required=True,
            metadata={
                "browser_review_reasons": ["SOURCE_REVIEW_REQUIRED"],
                "classification_review_reasons": ["MANUAL_REASON"],
                "locked_fields": ["classification_review_reasons"],
            },
        )
        policy = AIPolicy(
            policy_id="resolve-category",
            name="Resolve category",
            priority=1,
            instruction="Resolve category",
            target_fields=("category",),
        )

        traces = AIEnrichmentEngine([policy]).enrich(
            transaction,
            lambda _request: {
                "proposals": [
                    {
                        "field": "category",
                        "value": "Online Shopping",
                        "confidence": 0.99,
                    }
                ]
            },
        )

        self.assertEqual(len(traces), 1)
        self.assertTrue(traces[0].accepted)
        self.assertEqual(
            transaction.metadata["classification_review_reasons"],
            ["MANUAL_REASON"],
        )
        self.assertEqual(
            transaction.metadata["browser_review_reasons"],
            ["SOURCE_REVIEW_REQUIRED"],
        )
        self.assertTrue(transaction.review_required)
        self.assertEqual(transaction.tags, {"needs-review"})

    def test_ai_resolution_cleanup_applies_each_lock_independently(self) -> None:
        resolution_cases = (
            (
                "category",
                "Online Shopping",
                "category_resolution",
                ("category_recommendations",),
            ),
            (
                "vendor",
                "Amazon",
                "payee_resolution",
                ("payee_recommendations", "vendor_recommendations"),
            ),
        )
        for (
            proposal_field,
            proposal_value,
            resolution_field,
            recommendation_fields,
        ) in resolution_cases:
            for locked_field in (
                resolution_field,
                *recommendation_fields,
                "tags",
                "review_required",
                "classification_review_reasons",
            ):
                with self.subTest(
                    proposal_field=proposal_field,
                    locked_field=locked_field,
                ):
                    transaction = Transaction(
                        f"{proposal_field}-{locked_field}",
                        datetime(2026, 8, 16, tzinfo=UTC),
                        "SC_PLATINUM_X",
                        "KNOWN MERCHANT",
                        Decimal(100),
                        vendor="unknown" if proposal_field == "vendor" else "Merchant",
                        category=(
                            "Needs Review"
                            if proposal_field == "category"
                            else "Shopping"
                        ),
                        tags={"category-review", "needs-review"},
                        review_required=True,
                        metadata={
                            resolution_field: "MANUAL_RESOLUTION",
                            **{
                                recommendation_field: [{"name": "Pending"}]
                                for recommendation_field in recommendation_fields
                            },
                            "classification_review_reasons": ["STALE_REASON"],
                            "locked_fields": [locked_field],
                        },
                    )
                    if locked_field == "tags":
                        transaction.tags = {"Manual"}
                    elif locked_field == "review_required":
                        transaction.review_required = False
                    elif locked_field == "classification_review_reasons":
                        transaction.metadata["classification_review_reasons"] = [
                            "MANUAL_REASON"
                        ]
                    policy = AIPolicy(
                        policy_id=f"resolve-{proposal_field}",
                        name=f"Resolve {proposal_field}",
                        priority=1,
                        instruction=f"Resolve {proposal_field}",
                        target_fields=(proposal_field,),
                    )

                    proposal = {
                        "field": proposal_field,
                        "value": proposal_value,
                        "confidence": 0.99,
                    }
                    traces = AIEnrichmentEngine([policy]).enrich(
                        transaction,
                        lambda _request, proposal=proposal: {"proposals": [proposal]},
                    )

                    self.assertEqual(len(traces), 1)
                    self.assertTrue(traces[0].accepted)
                    self.assertEqual(transaction.value(proposal_field), proposal_value)
                    self.assertEqual(
                        transaction.metadata[resolution_field],
                        "MANUAL_RESOLUTION"
                        if locked_field == resolution_field
                        else "RESOLVED",
                    )
                    for recommendation_field in recommendation_fields:
                        if locked_field == recommendation_field:
                            self.assertIn(recommendation_field, transaction.metadata)
                        else:
                            self.assertNotIn(
                                recommendation_field,
                                transaction.metadata,
                            )
                    self.assertEqual(
                        transaction.tags,
                        {"Manual"} if locked_field == "tags" else set(),
                    )
                    self.assertFalse(transaction.review_required)
                    self.assertEqual(
                        transaction.metadata["classification_review_reasons"],
                        ["MANUAL_REASON"]
                        if locked_field == "classification_review_reasons"
                        else [],
                    )
