from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from finance_tracker.actual_pipeline import load_compiled_rules
from finance_tracker.classification_audit import (
    build_classification_exception_report,
    enforce_transaction_invariants,
)
from finance_tracker.models import Transaction
from finance_tracker.platforms import ActualBudgetAdapter
from finance_tracker.rules import RuleAction, RuleCondition, RuleEngine, StaticRule
from finance_tracker.transaction_semantics import finalize_transaction_topic


ROOT = Path(__file__).resolve().parent.parent


class TransactionTopicTests(TestCase):
    @staticmethod
    def transaction(
        description: str,
        *,
        direction: str,
        transaction_type: str = "PURCHASE",
        card: str = "EI_AMAZON",
    ) -> Transaction:
        return Transaction(
            transaction_id="topic-test",
            transaction_at=datetime(2026, 7, 12),
            card=card,
            merchant_raw=description,
            amount_aed=Decimal("3.55"),
            source_direction=direction,
            transaction_type=transaction_type,
        )

    def test_generic_positive_merchant_credit_defaults_to_refund(self) -> None:
        transaction = self.transaction("AMAZON.AE DUBAI ARE", direction="CREDIT")

        finalize_transaction_topic(transaction)

        self.assertEqual(transaction.transaction_type, "REFUND")
        self.assertTrue(transaction.is_refund)
        self.assertIn("refund", transaction.tags)
        self.assertIn("transaction_type", transaction.metadata["locked_fields"])

    def test_explicit_reward_credit_is_not_downgraded_to_refund(self) -> None:
        transaction = self.transaction(
            "MONTHLY CASHBACK REWARD CREDIT",
            direction="CREDIT",
            transaction_type="REWARD_CREDIT",
        )

        finalize_transaction_topic(transaction)

        self.assertEqual(transaction.transaction_type, "REWARD_CREDIT")
        self.assertFalse(transaction.is_refund)

    def test_reversal_is_distinct_but_has_refund_economics(self) -> None:
        transaction = self.transaction(
            "PURCHASE REVERSED",
            direction="CREDIT",
            transaction_type="CREDIT",
        )

        finalize_transaction_topic(transaction)

        self.assertEqual(transaction.transaction_type, "REVERSAL")
        self.assertTrue(transaction.is_refund)

    def test_explicit_investment_credit_is_not_downgraded_to_refund(self) -> None:
        transaction = self.transaction(
            "INVESTMENT DISTRIBUTION",
            direction="CREDIT",
            transaction_type="INVESTMENT",
        )

        finalize_transaction_topic(transaction)

        self.assertEqual(transaction.transaction_type, "INVESTMENT")
        self.assertEqual(transaction.spend_aed, Decimal("0"))

    def test_source_direction_is_required_to_agree_with_adapter_metadata(self) -> None:
        transaction = self.transaction("MERCHANT", direction="CREDIT")
        transaction.metadata["statement_direction"] = "DEBIT"

        with self.assertRaisesRegex(ValueError, "Conflicting source directions"):
            finalize_transaction_topic(transaction)

    def test_negative_canonical_amount_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-negative magnitude"):
            self.transaction("MERCHANT", direction="DEBIT").set_value(
                "amount_aed", Decimal("-1")
            )

    def test_later_static_rules_cannot_overwrite_locked_topic(self) -> None:
        transaction = self.transaction("AMAZON.AE", direction="CREDIT")
        finalize_transaction_topic(transaction)
        rule = StaticRule(
            "late-topic",
            "Unsafe later topic",
            "CLASSIFICATION",
            10,
            [RuleCondition("merchant_raw", "contains", "AMAZON")],
            [RuleAction("set", "transaction_type", "REWARD_CREDIT")],
        )

        trace = RuleEngine([rule]).apply(transaction)[0]

        self.assertEqual(transaction.transaction_type, "REFUND")
        self.assertIn("skipped_locked:transaction_type", trace.actions_applied)

    def test_adcb_closing_payment_is_one_transfer_not_spend_or_reward(self) -> None:
        transaction = self.transaction(
            "TRANSFER PAYMENT RECEIVED THANK YOU",
            direction="CREDIT",
            transaction_type="PAYMENT",
            card="ADCB_8833",
        )
        transaction.account = "ADCB Credit Card · 8833"
        transaction.metadata["account_balance_convention"] = "LIABILITY"
        engine = RuleEngine(
            load_compiled_rules(ROOT / "config" / "static-rules.seed.json")
        )

        engine.apply_stages(transaction, ("TRANSACTION_NORMALIZATION",))
        finalize_transaction_topic(transaction)
        envelope = ActualBudgetAdapter().serialize_import([transaction])[0]

        self.assertEqual(transaction.transaction_type, "TRANSFER")
        self.assertFalse(transaction.is_refund)
        self.assertIsNone(transaction.reward_bucket)
        self.assertNotIn("reward", transaction.tags)
        self.assertEqual(len(envelope.records), 1)
        self.assertEqual(envelope.records[0]["amount"], 355)
        self.assertNotIn("#cashback-", envelope.records[0]["notes"])


class ClassificationInvariantTests(TestCase):
    @staticmethod
    def transaction() -> Transaction:
        return Transaction(
            transaction_id="classification-test",
            transaction_at=datetime(2026, 8, 1),
            card="ADCB",
            merchant_raw="UNKNOWN MERCHANT",
            amount_aed=Decimal("10"),
            source_direction="DEBIT",
        )

    def test_uncategorized_row_is_always_queued_for_review(self) -> None:
        transaction = self.transaction()

        enforce_transaction_invariants(transaction)

        self.assertTrue(transaction.review_required)
        self.assertIn("needs-review", transaction.tags)
        self.assertIn(
            "UNCATEGORIZED", transaction.metadata["classification_review_reasons"]
        )

    def test_needs_review_category_and_tag_are_mutually_reinforcing(self) -> None:
        by_category = self.transaction()
        by_category.category = "Needs Review"
        by_tag = self.transaction()
        by_tag.category = "Groceries"
        by_tag.tags.add("needs-review")

        enforce_transaction_invariants(by_category)
        enforce_transaction_invariants(by_tag)

        self.assertTrue(by_category.review_required)
        self.assertIn("needs-review", by_category.tags)
        self.assertTrue(by_tag.review_required)

    def test_rental_tags_remove_home_and_require_exactly_one_unit(self) -> None:
        valid = self.transaction()
        valid.category = "District Cooling"
        valid.tags.update({"home", "rental", "rental:lt713"})
        invalid = self.transaction()
        invalid.category = "District Cooling"
        invalid.tags.update({"rental", "rental:lt713", "rental:indigo1414"})

        enforce_transaction_invariants(valid)
        enforce_transaction_invariants(invalid)

        self.assertNotIn("home", valid.tags)
        self.assertFalse(valid.review_required)
        self.assertTrue(invalid.review_required)
        self.assertIn(
            "RENTAL_UNIT_TAG_COUNT", invalid.metadata["classification_review_reasons"]
        )

    def test_exception_report_accounts_for_every_transaction(self) -> None:
        resolved = self.transaction()
        resolved.transaction_id = "resolved"
        resolved.category = "Groceries"
        unresolved = self.transaction()
        unresolved.transaction_id = "unresolved"
        enforce_transaction_invariants(resolved)
        enforce_transaction_invariants(unresolved)

        report = build_classification_exception_report([resolved, unresolved])

        self.assertEqual(report["transaction_count"], 2)
        self.assertEqual(report["resolved_count"], 1)
        self.assertEqual(report["exception_count"], 1)
        self.assertEqual(report["unaccounted_count"], 0)
        self.assertEqual(report["exceptions"][0]["transaction_id"], "unresolved")
