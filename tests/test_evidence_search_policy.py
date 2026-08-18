from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest import TestCase


class EvidenceSearchPolicyTests(TestCase):
    def test_policy_is_selective_and_rejects_vendor_only_matching(self) -> None:
        policy = json.loads(
            Path("config/evidence-search-policy.json").read_text(encoding="utf-8")
        )

        self.assertEqual(policy["schema_version"], 1)
        self.assertGreaterEqual(Decimal(policy["minimum_general_purchase_amount"]), 500)
        self.assertIn("Electronics", policy["always_search_categories"])
        self.assertIn("Service Charges", policy["always_search_categories"])
        self.assertIn("Dining Out", policy["never_search_categories"])
        self.assertIn("Groceries", policy["never_search_categories"])
        self.assertIn("Mortgage Payments", policy["never_search_categories"])
        self.assertIn("Investment Contributions", policy["never_search_categories"])
        self.assertNotIn("Dining Out", policy["always_search_categories"])
        self.assertGreaterEqual(policy["matching"]["minimum_strong_facts"], 2)
        self.assertFalse(policy["matching"]["vendor_only_match_allowed"])
        self.assertTrue(any("Laptop" in item for item in policy["store_examples"]))
        self.assertTrue(any("Supermarket" in item for item in policy["do_not_store_examples"]))
        self.assertIn(
            "product_or_service_description",
            policy["search_strategy"]["extract_from_matched_evidence"],
        )

    def test_ai_policy_uses_the_same_selective_category_sets(self) -> None:
        evidence = json.loads(
            Path("config/evidence-search-policy.json").read_text(encoding="utf-8")
        )
        ai = json.loads(Path("config/ai-policies.json").read_text(encoding="utf-8"))
        purchase_policy = next(
            item for item in ai["policies"] if item["policy_id"] == "find-purchase-evidence"
        )
        always = next(
            condition["value"]
            for condition in purchase_policy["conditions"]
            if condition.get("group") == 1 and condition["field"] == "category"
        )
        never = next(
            condition["value"]
            for condition in purchase_policy["conditions"]
            if condition.get("group") == 2 and condition["field"] == "category"
        )

        self.assertEqual(always, evidence["always_search_categories"])
        self.assertEqual(never, evidence["never_search_categories"])
