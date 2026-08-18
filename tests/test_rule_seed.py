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

    def test_empower_issuer_descriptor_is_normalized_as_district_cooling(self) -> None:
        transaction = self.transaction("EMIRATES CENTRAL COOLING DUBAI")

        self.engine.apply(transaction)

        self.assertEqual(transaction.vendor, "Empower")
        self.assertEqual(transaction.category, "District Cooling")
        self.assertTrue({"utility", "home"}.issubset(transaction.tags))
        self.assertEqual(transaction.evidence_policy, "MATCH_AMOUNT_AND_PERIOD")

    def test_hospitality_does_not_match_hospital(self) -> None:
        transaction = self.transaction("CROISSANCE HOSPITALITY DUBAI")

        self.engine.apply(transaction)

        self.assertIsNone(transaction.category)
        self.assertNotIn("health", transaction.tags)

    def test_concession_at_fuel_site_is_dining_not_fuel(self) -> None:
        transaction = self.transaction("KFC ENOC AL BARSHA DUBAI")

        self.engine.apply(transaction)

        self.assertEqual(transaction.category, "Dining Out")
        self.assertNotIn("transport", transaction.tags)

    def test_gmg_consumer_uses_combined_grocery_rule_and_shared_tag(self) -> None:
        transaction = self.transaction("GMG CONSUMER LLC DUBAI ARE")

        trace = self.engine.apply(transaction)

        self.assertEqual(transaction.vendor, "GMG Consumer")
        self.assertEqual(transaction.category, "Groceries")
        self.assertTrue({"grocery", "shared"}.issubset(transaction.tags))
        self.assertIn("class-groceries", {item.rule_id for item in trace if item.matched})

    def test_known_medical_vendor_is_normalized_before_classification(self) -> None:
        transaction = self.transaction("GERMAN NEUROSCIENCE CENTE DUBAI ARE")

        self.engine.apply(transaction)

        self.assertEqual(transaction.vendor, "German Neuroscience Centre")
        self.assertEqual(transaction.category, "Medical")
        self.assertIn("health", transaction.tags)

    def test_smart_dubai_amount_heuristics_split_parking_tolls_and_government(self) -> None:
        parking = self.transaction("SMART DUBAI GOVERNMENT DUBAI ARE")
        parking.amount_aed = Decimal("12")
        toll = self.transaction("DUBAI SMART GOVERNMENT ES DUBAI ARE")
        toll.amount_aed = Decimal("200")
        government = self.transaction("DUBAI SMART GOVERNMENT ES DUBAI ARE")
        government.amount_aed = Decimal("418.95")

        for transaction in (parking, toll, government):
            self.engine.apply(transaction)

        self.assertEqual(parking.category, "Parking & Tolls")
        self.assertEqual(toll.category, "Parking & Tolls")
        self.assertEqual(government.category, "Taxes & Government")

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

    def test_aws_domain_is_cloud_services_not_amazon_retail(self) -> None:
        transaction = self.transaction("AWS EMEA aws.amazon.co - 0.61 USD")

        self.engine.apply(transaction)

        self.assertEqual(transaction.vendor, "AWS")
        self.assertEqual(transaction.category, "Cloud Services")
        self.assertTrue({"business", "online"}.issubset(transaction.tags))
        self.assertIsNone(transaction.evidence_policy)

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
        self.assertTrue({"grocery", "shared"}.issubset(transaction.tags))
        self.assertEqual(transaction.reward_bucket, "RAK_GROCERY")

    def test_lulu_uses_the_combined_supermarket_rule(self) -> None:
        transaction = self.transaction("LULU HYPERMARKET EFT DUBAI AE")

        trace = self.engine.apply(transaction)

        self.assertEqual(transaction.vendor, "LuLu Hypermarket")
        self.assertEqual(transaction.category, "Groceries")
        self.assertTrue({"grocery", "shared"}.issubset(transaction.tags))
        self.assertIn("class-groceries", {item.rule_id for item in trace})
        self.assertNotIn("class-lulu-groceries", {item.rule_id for item in trace})

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
