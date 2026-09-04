from datetime import datetime
from unittest import TestCase

from finance_tracker.models import Transaction
from finance_tracker.rules import RuleAction, RuleCondition, RuleEngine, StaticRule, condition_matches, validate_rule


class RuleEngineTests(TestCase):
    def transaction(self) -> Transaction:
        return Transaction("t1", datetime(2026, 8, 1), "SC_PLATINUM_X", "Carrefour Mirdif 123", "250")

    def test_or_groups_of_and_conditions(self) -> None:
        rule = StaticRule(
            "r1",
            "Supermarket",
            "CLASSIFICATION",
            10,
            [
                RuleCondition("merchant_raw", "contains", "Carrefour", group=1),
                RuleCondition("amount_aed", "between", "0", "500", group=1),
                RuleCondition("merchant_raw", "contains", "Spinneys", group=2),
            ],
            [RuleAction("set", "category", "GROCERY"), RuleAction("add_tag", value="Household")],
        )
        transaction = self.transaction()
        traces = RuleEngine([rule]).apply(transaction)
        self.assertEqual(transaction.category, "GROCERY")
        self.assertIn("Household", transaction.tags)
        self.assertTrue(traces[0].matched)

    def test_stop_on_match_only_stops_same_stage(self) -> None:
        rules = [
            StaticRule("r1", "Specific", "CLASSIFICATION", 10, [RuleCondition("merchant_raw", "contains", "Carrefour")], [RuleAction("set", "category", "GROCERY")]),
            StaticRule("r2", "Broad", "CLASSIFICATION", 20, [RuleCondition("amount_aed", "gt", "0")], [RuleAction("set", "category", "GENERAL")]),
            StaticRule("r3", "Evidence", "EVIDENCE", 10, [RuleCondition("category", "equals", "GROCERY")], [RuleAction("request_evidence", value="IF_MISSING")]),
        ]
        transaction = self.transaction()
        RuleEngine(rules).apply(transaction)
        self.assertEqual(transaction.category, "GROCERY")
        self.assertEqual(transaction.evidence_status, "REQUESTED")

    def test_manual_locked_field_wins(self) -> None:
        rule = StaticRule("r1", "Any", "CLASSIFICATION", 10, [RuleCondition("amount_aed", "gt", "0")], [RuleAction("set", "category", "GENERAL")])
        transaction = self.transaction()
        transaction.category = "MANUAL"
        transaction.metadata["locked_fields"] = ["category"]
        RuleEngine([rule]).apply(transaction)
        self.assertEqual(transaction.category, "MANUAL")

    def test_manual_locks_cover_tag_evidence_and_review_actions(self) -> None:
        rule = StaticRule(
            "locked-derived",
            "Locked derived fields",
            "EVIDENCE",
            10,
            [RuleCondition("amount_aed", "gt", "0")],
            [
                RuleAction("add_tag", value="automatic"),
                RuleAction("request_evidence", value="IF_MISSING"),
                RuleAction("require_review"),
            ],
        )
        transaction = self.transaction()
        transaction.tags = {"manual"}
        transaction.evidence_policy = "NEVER"
        transaction.metadata["locked_fields"] = [
            "tags", "evidence_policy", "evidence_status", "review_required"
        ]

        RuleEngine([rule]).apply(transaction)

        self.assertEqual(transaction.tags, {"manual"})
        self.assertEqual(transaction.evidence_policy, "NEVER")
        self.assertEqual(transaction.evidence_status, "NOT_REQUESTED")
        self.assertFalse(transaction.review_required)

    def test_missing_numeric_field_does_not_match_zero(self) -> None:
        transaction = self.transaction()
        self.assertFalse(condition_matches(
            transaction, RuleCondition("history_count", "numeric_equals", 0)
        ))
        self.assertFalse(condition_matches(
            transaction, RuleCondition("history_count", "between", -1, 1)
        ))
        self.assertFalse(condition_matches(
            transaction, RuleCondition("history_count", "polarity", "positive")
        ))
        transaction.metadata["history_count"] = 0
        self.assertFalse(condition_matches(
            transaction, RuleCondition("history_count", "polarity", "positive")
        ))
        self.assertTrue(condition_matches(
            transaction, RuleCondition("history_count", "polarity", "zero")
        ))

    def test_polarity_rejects_unknown_direction(self) -> None:
        rule = StaticRule(
            "bad-polarity",
            "Bad polarity",
            "CLASSIFICATION",
            10,
            [RuleCondition("amount_aed", "polarity", "nonnegative")],
            [RuleAction("set", "category", "Bad")],
        )
        with self.assertRaisesRegex(ValueError, "positive, negative, or zero"):
            validate_rule(rule)

    def test_invalid_field_is_rejected_before_evaluation(self) -> None:
        rule = StaticRule(
            "r1",
            "Typo",
            "CLASSIFICATION",
            10,
            [RuleCondition("merhcant_raw", "contains", "Carrefour")],
            [RuleAction("set", "category", "GROCERY")],
        )
        with self.assertRaisesRegex(ValueError, "unsupported field"):
            validate_rule(rule)

    def test_between_requires_upper_bound(self) -> None:
        rule = StaticRule(
            "r1",
            "Broken range",
            "CLASSIFICATION",
            10,
            [RuleCondition("amount_aed", "between", "0")],
            [RuleAction("set", "category", "GROCERY")],
        )
        with self.assertRaisesRegex(ValueError, "second_value"):
            RuleEngine([rule])

    def test_static_rule_cannot_overwrite_protected_amount(self) -> None:
        rule = StaticRule(
            "r1",
            "Unsafe",
            "CLASSIFICATION",
            10,
            [RuleCondition("merchant_raw", "contains", "Carrefour")],
            [RuleAction("set", "amount_aed", "1")],
        )
        with self.assertRaisesRegex(ValueError, "cannot set field"):
            validate_rule(rule)

    def test_tiller_style_account_amount_and_institution_conditions(self) -> None:
        rule = StaticRule(
            "card-payment",
            "ADCB card payment",
            "TRANSACTION_NORMALIZATION",
            10,
            [
                RuleCondition("institution", "equals", "ADCB"),
                RuleCondition("account_last4", "equals", "8833"),
                RuleCondition("amount_aed", "numeric_equals", "250.00"),
                RuleCondition("spend_aed", "polarity", "positive"),
            ],
            [RuleAction("set", "transaction_type", "TRANSFER")],
        )
        transaction = self.transaction()
        transaction.institution = "ADCB"
        transaction.account_last4 = "8833"

        RuleEngine([rule]).apply(transaction)

        self.assertEqual(transaction.transaction_type, "TRANSFER")

    def test_set_if_empty_and_multi_tag_actions_preserve_existing_classification(self) -> None:
        rule = StaticRule(
            "rental",
            "Rental unit",
            "TAGGING",
            10,
            [RuleCondition("merchant_raw", "contains", "Management Company")],
            [
                RuleAction("set_if_empty", "category", "Rental Income"),
                RuleAction("set_if_empty", "property_code", "LT"),
                RuleAction("add_tags", value=["rental", "income"]),
            ],
        )
        transaction = self.transaction()
        transaction.merchant_raw = "Management Company Rent Distribution"
        transaction.category = "Manual Category"

        trace = RuleEngine([rule]).apply(transaction)[0]

        self.assertEqual(transaction.category, "Manual Category")
        self.assertEqual(transaction.property_code, "LT")
        self.assertEqual(transaction.tags, {"rental", "income"})
        self.assertIn("skipped_populated:category", trace.actions_applied)

    def test_date_range_operator(self) -> None:
        condition = RuleCondition(
            "transaction_at",
            "date_between",
            "2026-08-01",
            "2026-08-31",
        )
        rule = StaticRule(
            "august",
            "August transactions",
            "TAGGING",
            10,
            [condition],
            [RuleAction("add_tag", value="august")],
        )
        transaction = self.transaction()

        RuleEngine([rule]).apply(transaction)

        self.assertIn("august", transaction.tags)
