from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from finance_tracker.actual_pipeline import load_compiled_rules
from finance_tracker.models import Transaction
from finance_tracker.rules import RuleEngine


ROOT = Path(__file__).resolve().parent.parent


class RuleSeedTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = load_compiled_rules(ROOT / "config" / "static-rules.seed.json")
        cls.engine = RuleEngine(cls.rules)

    def transaction(self, merchant: str, *, card: str = "RAK_WORLD") -> Transaction:
        return Transaction(
            transaction_id="seed-test",
            transaction_at=datetime(2026, 8, 16),
            card=card,
            merchant_raw=merchant,
            amount_aed="100",
        )

    def test_seed_is_broad_and_every_rule_validates(self) -> None:
        self.assertGreaterEqual(len(self.rules), 40)

    def test_live_cashback_rule_set_is_small_and_contains_all_bucket_rules(self) -> None:
        live = [rule for rule in self.rules if "LIVE_CASHBACK" in rule.rule_sets]
        self.assertLess(len(live), len(self.rules) / 2)
        self.assertTrue(live)
        self.assertTrue(
            all("LIVE_CASHBACK" in rule.rule_sets for rule in live if rule.stage == "CASHBACK")
        )
        self.assertEqual(
            {rule.rule_id for rule in self.rules if rule.stage == "CASHBACK"},
            {rule.rule_id for rule in live if rule.stage == "CASHBACK"},
        )

    def test_utility_rule_normalizes_classifies_tags_and_requests_evidence(self) -> None:
        transaction = self.transaction("DEWA ONLINE PAYMENT 9382")

        self.engine.apply(transaction)

        self.assertEqual(transaction.vendor, "DEWA")
        self.assertEqual(transaction.category, "Electricity & Water")
        self.assertIn("utility", transaction.tags)
        self.assertEqual(transaction.evidence_policy, "MATCH_AMOUNT_AND_PERIOD")
        self.assertEqual(transaction.evidence_status, "REQUESTED")

    def test_wio_foreign_exchange_fee_is_categorized(self) -> None:
        transaction = Transaction(
            transaction_id="wio-fee",
            transaction_at=datetime(2026, 7, 4),
            card="WIO_CREDIT",
            merchant_raw="Foreign Exchange Fee [P1106333977]",
            amount_aed=Decimal("16.59"),
            transaction_type="FEE",
        )

        RuleEngine(self.rules).apply_stages(transaction, ("CLASSIFICATION",))

        self.assertEqual(transaction.category, "Foreign Fees")
        self.assertEqual(transaction.transaction_type, "FEE")
        self.assertTrue({"fee", "foreign"}.issubset(transaction.tags))

    def test_amazon_purchase_gets_ei_bucket_and_receipt_search(self) -> None:
        transaction = self.transaction("AMAZON.AE*AB12CD", card="EI_AMAZON")
        transaction.channel = "ONLINE"

        self.engine.apply(transaction)

        self.assertEqual(transaction.vendor, "Amazon")
        self.assertEqual(transaction.category, "Online Shopping")
        self.assertEqual(transaction.reward_bucket, "EI_AMAZON")
        self.assertEqual(transaction.evidence_policy, "SEARCH_RECEIPT_BILL_WARRANTY")

    def test_sc_wallet_rule_is_distinct_from_online(self) -> None:
        transaction = self.transaction("LOCAL MERCHANT", card="SC_PLATINUM_X")
        transaction.channel = "APPLE_PAY_POS"

        self.engine.apply(transaction)

        self.assertEqual(transaction.reward_bucket, "SC_WALLET")

    def test_rak_wallet_purchase_retains_enhanced_merchant_category_bucket(self) -> None:
        transaction = self.transaction("CARREFOUR MIRDIF", card="RAK_WORLD")
        transaction.channel = "APPLE_PAY_POS"

        self.engine.apply(transaction)

        self.assertEqual(transaction.category, "Groceries")
        self.assertEqual(transaction.reward_bucket, "RAK_GROCERY")

    def test_sc_physical_spend_is_tracked_as_tier_filler(self) -> None:
        transaction = self.transaction("LOCAL MERCHANT", card="SC_PLATINUM_X")
        transaction.channel = "PHYSICAL_POS"

        self.engine.apply(transaction)

        self.assertEqual(transaction.reward_bucket, "SC_FILLER")

    def test_manual_category_lock_survives_seed_rules(self) -> None:
        transaction = self.transaction("CARREFOUR MIRDIF")
        transaction.category = "Manual Household"
        transaction.metadata["locked_fields"] = ["category"]

        self.engine.apply(transaction)

        self.assertEqual(transaction.vendor, "Carrefour")
        self.assertEqual(transaction.category, "Manual Household")
