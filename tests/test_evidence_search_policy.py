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
        self.assertNotIn("Dining Out", policy["always_search_categories"])
        self.assertGreaterEqual(policy["matching"]["minimum_strong_facts"], 2)
        self.assertFalse(policy["matching"]["vendor_only_match_allowed"])
