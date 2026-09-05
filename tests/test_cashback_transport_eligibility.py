"""Broad travel budget categories must not imply issuer flight/hotel eligibility."""
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
from unittest import TestCase

from finance_tracker.actual_snapshot import _reward_bucket
from finance_tracker.cashback import (
    PaymentIntent, bucket_spend, configured_reward_bucket, evaluate_card,
    programs_from_config, purchase_type_from_config, reward_total, total_spend,
)
from finance_tracker.models import Transaction


class TransportEligibilityTests(TestCase):
    def setUp(self):
        self.source = json.loads(Path("config/cashback-programs.json").read_text())
        self.programs = programs_from_config(self.source, date(2026, 9, 5))
        self.rak = next(p for p in self.programs if p.card == "RAK_WORLD")

    def test_transport_and_activities_have_no_automatic_rak_reward(self):
        for actual_category in ("Travel Transport", "Travel Activities"):
            category = purchase_type_from_config(self.source, actual_category, "Test merchant")
            self.assertNotIn(category, ("TRAVEL", "GENERAL"))
            for channel in ("ONLINE", "PHYSICAL_POS", "APPLE_PAY_POS"):
                with self.subTest(category=category, channel=channel):
                    self.assertIsNone(configured_reward_bucket(
                        self.programs, "RAK_WORLD", category, channel, "AED"))
                    self.assertIsNone(evaluate_card(
                        self.rak, [], PaymentIntent(category, Decimal("100"), "AED", channel)))
            # This RAK-specific uncertainty must not disable SC online qualification.
            self.assertEqual(configured_reward_bucket(
                self.programs, "SC_PLATINUM_X", category, "ONLINE", "AED"), "SC_ONLINE")

    def test_specific_and_fallback_assignments_enforce_exclusions(self):
        # Both Apple Pay's explicit assignment and standard fallback must reject it.
        for channel in ("APPLE_PAY_POS", "PHYSICAL_POS"):
            self.assertIsNone(configured_reward_bucket(
                self.programs, "RAK_WORLD", "TRANSPORT", channel, "AED"))
        for category in ("AIRLINE", "HOTEL"):
            self.assertEqual(configured_reward_bucket(
                self.programs, "RAK_WORLD", category, "APPLE_PAY_POS", "AED"), "RAK_TRAVEL")
        self.assertEqual(configured_reward_bucket(
            self.programs, "RAK_WORLD", "GENERAL", "APPLE_PAY_POS", "AED"), "RAK_EWALLET")
        self.assertEqual(configured_reward_bucket(
            self.programs, "RAK_WORLD", "GENERAL", "PHYSICAL_POS", "AED"), "RAK_STANDARD")

    def test_purchase_and_refund_preserve_spend_without_inventing_reward(self):
        rows = [
            Transaction("transport-purchase", datetime(2026, 9, 5), "RAK_WORLD",
                        "Transport", "11000", category="TRANSPORT"),
            Transaction("transport-refund", datetime(2026, 9, 5), "RAK_WORLD",
                        "Transport", "1000", category="TRANSPORT", transaction_type="REFUND"),
        ]
        self.assertEqual(len(rows), 2)
        self.assertEqual(total_spend(rows, "RAK_WORLD"), Decimal("10000"))
        self.assertEqual(bucket_spend(rows, "RAK_WORLD"), {})
        self.assertEqual(reward_total(self.rak, total_spend(rows, "RAK_WORLD"),
                                      bucket_spend(rows, "RAK_WORLD")), Decimal("0"))

    def test_explicit_manual_bucket_is_not_rewritten(self):
        # Normalization is not a migration of existing evidence/manual tags.
        self.assertEqual(_reward_bucket(
            self.programs, "RAK_WORLD", "TRANSPORT", "APPLE_PAY_POS", "AED",
            {"cashback-rak-travel"}), "RAK_TRAVEL")
        historical = programs_from_config(self.source, date(2026, 9, 4))
        self.assertEqual(next(p for p in historical if p.card == "RAK_WORLD").programme_version,
                         "confirmed-2026-08-v1")
