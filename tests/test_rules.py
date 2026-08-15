from datetime import datetime
from unittest import TestCase

from finance_tracker.models import Transaction
from finance_tracker.rules import RuleAction, RuleCondition, RuleEngine, StaticRule


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

