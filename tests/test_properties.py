from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest import TestCase

from finance_tracker.history import HistoryDecision, apply_history_match
from finance_tracker.models import Transaction
from finance_tracker.properties import load_property_registry, project_property_tags
from finance_tracker.rules import RuleAction, RuleCondition, RuleEngine, StaticRule


ROOT = Path(__file__).resolve().parent.parent


class PropertyRegistryTests(TestCase):
    def setUp(self) -> None:
        self.registry = load_property_registry(ROOT / "config" / "properties.json")

    @staticmethod
    def transaction() -> Transaction:
        return Transaction(
            "property-1", datetime(2026, 8, 18), "ADCB", "EMPOWER", "100"
        )

    def test_utility_references_map_to_evidenced_properties(self) -> None:
        self.assertEqual(
            self.registry.by_utility_reference("DEWA", "2049280351").property_code,
            "LT713",
        )
        self.assertEqual(
            self.registry.by_utility_reference("DEWA", "393024750").property_code,
            "INDIGO1414",
        )
        self.assertEqual(
            self.registry.by_utility_reference("EMPOWER", "6997139878").property_code,
            "BLUEWATERS_B7_306",
        )

    def test_rental_unit_projects_separate_generic_and_unit_tags(self) -> None:
        transaction = self.transaction()
        transaction.rental_unit = "LT713"

        project_property_tags(transaction, self.registry)

        self.assertEqual(transaction.property_code, "LT713")
        self.assertTrue({"rental", "rental:lt713"}.issubset(transaction.tags))
        self.assertNotIn("rental:indigo1414", transaction.tags)

    def test_owner_occupied_property_does_not_receive_rental_tag(self) -> None:
        transaction = self.transaction()
        transaction.property_code = "BLUEWATERS_B7_306"

        project_property_tags(transaction, self.registry)

        self.assertIn("home", transaction.tags)
        self.assertNotIn("rental", transaction.tags)

    def test_conflicting_property_evidence_requires_review(self) -> None:
        transaction = self.transaction()
        transaction.property_code = "LT713"
        transaction.rental_unit = "Indigo1414"

        self.assertIsNone(project_property_tags(transaction, self.registry))

        self.assertTrue(transaction.review_required)
        self.assertIn(
            "PROPERTY_CODE_RENTAL_UNIT_CONFLICT",
            transaction.metadata["property_review_reasons"],
        )

    def test_property_ownership_is_separate_from_occupancy(self) -> None:
        lake_terrace = self.registry.by_code("LT713")
        indigo = self.registry.by_code("INDIGO1414")
        bluewaters = self.registry.by_code("BLUEWATERS_B7_306")

        self.assertEqual(lake_terrace.ownership, "PERSONAL")
        self.assertEqual(lake_terrace.occupancy, "RENTAL")
        self.assertEqual(indigo.ownership, "JOINT")
        self.assertEqual(indigo.occupancy, "RENTAL")
        self.assertEqual(bluewaters.ownership, "JOINT")
        self.assertEqual(bluewaters.occupancy, "OWNER_OCCUPIED")

    def test_explicit_name_and_utility_evidence_project_properties(self) -> None:
        named = self.transaction()
        named.metadata["property_name"] = "indigo 1414"
        resolved = project_property_tags(named, self.registry)
        self.assertEqual(resolved.property_code, "INDIGO1414")
        self.assertEqual(named.property_code, "INDIGO1414")
        self.assertIn("shared", named.tags)

        utility = self.transaction()
        utility.metadata["utility_provider"] = "DEWA"
        utility.metadata["utility_account_reference"] = "393009041"
        resolved = project_property_tags(utility, self.registry)
        self.assertEqual(resolved.property_code, "LT713")
        self.assertEqual(utility.property_code, "LT713")

    def test_utility_merchant_name_alone_does_not_guess_a_property(self) -> None:
        transaction = self.transaction()
        transaction.merchant_raw = "Lake Terrace 713"

        self.assertIsNone(project_property_tags(transaction, self.registry))
        self.assertFalse(transaction.review_required)

    def test_personal_property_overrides_generic_shared_family_tag(self) -> None:
        transaction = self.transaction()
        transaction.property_code = "LT713"
        transaction.tags = {"shared", "home"}

        resolved = project_property_tags(transaction, self.registry)

        self.assertEqual(resolved.ownership, "PERSONAL")
        self.assertNotIn("shared", transaction.tags)
        self.assertIn("rental", transaction.tags)
        self.assertNotIn("home", transaction.tags)

    def test_manual_tag_correction_survives_rule_history_and_projection(self) -> None:
        transaction = self.transaction()
        transaction.property_code = "LT713"
        transaction.tags = {"manual"}
        transaction.metadata["locked_fields"] = ["tags"]
        rule = StaticRule(
            "generic-shared",
            "Generic shared default",
            "TAGGING",
            10,
            [RuleCondition("merchant_raw", "contains", "EMPOWER")],
            [RuleAction("add_tags", value=["shared", "subscription"])],
        )

        RuleEngine([rule]).apply(transaction)
        apply_history_match(
            transaction,
            {"EMPOWER": HistoryDecision("EMPOWER", 2, {}, ("shared", "subscription"))},
        )
        project_property_tags(transaction, self.registry)

        self.assertEqual(transaction.tags, {"manual"})

    def test_manual_subscription_correction_blocks_rule_and_history_tags(self) -> None:
        transaction = self.transaction()
        transaction.metadata["locked_fields"] = ["is_subscription"]
        rule = StaticRule(
            "generic-subscription",
            "Generic subscription default",
            "TAGGING",
            10,
            [RuleCondition("merchant_raw", "contains", "EMPOWER")],
            [
                RuleAction("set", "is_subscription", True),
                RuleAction("add_tag", value="subscription"),
            ],
        )

        RuleEngine([rule]).apply(transaction)
        apply_history_match(
            transaction,
            {
                "EMPOWER": HistoryDecision(
                    "EMPOWER", 2, {}, ("subscription", "recurring")
                )
            },
        )

        self.assertFalse(transaction.is_subscription)
        self.assertNotIn("subscription", transaction.tags)
        self.assertNotIn("recurring", transaction.tags)

    def test_manual_owner_correction_overrides_joint_property_ownership(self) -> None:
        transaction = self.transaction()
        transaction.property_code = "BLUEWATERS_B7_306"
        transaction.owner = "Personal"
        transaction.tags = {"shared", "home"}
        transaction.metadata["locked_fields"] = ["owner"]

        project_property_tags(transaction, self.registry)

        self.assertEqual(transaction.owner, "Personal")
        self.assertEqual(transaction.metadata["property_ownership"], "PERSONAL")
        self.assertNotIn("shared", transaction.tags)
        self.assertIn("home", transaction.tags)

    def test_locked_owner_beats_stale_derived_ownership_in_rules(self) -> None:
        rule = StaticRule(
            "generic-shared",
            "Generic shared default",
            "TAGGING",
            10,
            [RuleCondition("merchant_raw", "contains", "EMPOWER")],
            [RuleAction("add_tag", value="shared")],
        )
        for owner, stale_ownership, should_share in (
            ("Personal", "JOINT", False),
            ("Joint", "PERSONAL", True),
        ):
            transaction = self.transaction()
            transaction.owner = owner
            transaction.metadata["property_ownership"] = stale_ownership
            transaction.metadata["locked_fields"] = ["owner"]

            RuleEngine([rule]).apply(transaction)

            self.assertEqual("shared" in transaction.tags, should_share)

    def test_locked_owner_beats_stale_derived_ownership_in_history(self) -> None:
        decision = HistoryDecision("EMPOWER", 2, {}, ("shared",))
        for owner, stale_ownership, should_share in (
            ("Personal", "JOINT", False),
            ("Joint", "PERSONAL", True),
        ):
            transaction = self.transaction()
            transaction.owner = owner
            transaction.metadata["property_ownership"] = stale_ownership
            transaction.metadata["locked_fields"] = ["owner"]

            apply_history_match(transaction, {"EMPOWER": decision})

            self.assertEqual("shared" in transaction.tags, should_share)

    def test_approved_family_categories_and_vendors_default_shared(self) -> None:
        categories = (
            "Groceries",
            "Dining Out",
            "Food Delivery",
            "Coffee & Snacks",
            "Electricity & Water",
            "District Cooling",
            "Mobile & Internet",
            "Service Charges",
            "Furniture & Appliances",
            "Maintenance & Repairs",
            "Fuel",
            "Parking & Tolls",
            "Vehicle Maintenance",
        )
        for category in categories:
            with self.subTest(category=category):
                transaction = self.transaction()
                transaction.category = category
                project_property_tags(transaction, self.registry)
                self.assertIn("shared", transaction.tags)

        ikea = self.transaction()
        ikea.vendor = "IKEA"
        project_property_tags(ikea, self.registry)
        self.assertIn("shared", ikea.tags)

    def test_personal_property_and_manual_owner_override_shared_defaults(self) -> None:
        for owner, property_code in (
            ("Personal", "BLUEWATERS_B7_306"),
            (None, "LT713"),
        ):
            with self.subTest(owner=owner, property_code=property_code):
                transaction = self.transaction()
                transaction.category = "Groceries"
                transaction.property_code = property_code
                if owner is not None:
                    transaction.owner = owner
                    transaction.metadata["locked_fields"] = ["owner"]
                project_property_tags(transaction, self.registry)
                self.assertNotIn("shared", transaction.tags)
        stale = self.transaction()
        stale.owner = "Personal"
        stale.tags = {"shared"}
        stale.metadata["locked_fields"] = ["owner"]
        project_property_tags(stale, self.registry)
        self.assertNotIn("shared", stale.tags)

    def test_locked_ownership_conflict_fails_closed_to_review(self) -> None:
        transaction = self.transaction()
        transaction.category = "Groceries"
        transaction.owner = "Personal"
        transaction.metadata["property_ownership"] = "JOINT"
        transaction.tags = {"shared"}
        transaction.metadata["locked_fields"] = ["owner", "property_ownership"]

        self.assertIsNone(project_property_tags(transaction, self.registry))
        self.assertTrue(transaction.review_required)
        self.assertNotIn("shared", transaction.tags)
        self.assertIn(
            "LOCKED_OWNERSHIP_CONFLICT",
            transaction.metadata["property_review_reasons"],
        )

    def test_locked_ownership_conflict_blocks_rule_and_history_defaults(self) -> None:
        rule = StaticRule(
            "generic-shared",
            "Generic shared default",
            "TAGGING",
            10,
            [RuleCondition("merchant_raw", "contains", "EMPOWER")],
            [RuleAction("add_tag", value="shared")],
        )
        ruled = self.transaction()
        ruled.owner = "Personal"
        ruled.metadata["property_ownership"] = "JOINT"
        ruled.metadata["locked_fields"] = ["owner", "property_ownership"]
        RuleEngine([rule]).apply(ruled)
        self.assertTrue(ruled.review_required)
        self.assertNotIn("shared", ruled.tags)
        self.assertIn(
            "LOCKED_OWNERSHIP_CONFLICT",
            ruled.metadata["property_review_reasons"],
        )

        historical = self.transaction()
        historical.owner = "Personal"
        historical.metadata["property_ownership"] = "JOINT"
        historical.metadata["locked_fields"] = ["owner", "property_ownership"]
        apply_history_match(
            historical,
            {"EMPOWER": HistoryDecision("EMPOWER", 2, {}, ("shared",))},
        )
        self.assertTrue(historical.review_required)
        self.assertNotIn("shared", historical.tags)
        self.assertIn(
            "LOCKED_OWNERSHIP_CONFLICT",
            historical.metadata["property_review_reasons"],
        )

    def test_locked_tags_and_unknown_categories_remain_unchanged(self) -> None:
        locked = self.transaction()
        locked.category = "Groceries"
        locked.tags = {"manual"}
        locked.metadata["locked_fields"] = ["tags"]
        project_property_tags(locked, self.registry)
        self.assertEqual(locked.tags, {"manual"})

        unknown = self.transaction()
        unknown.category = "Unknown Category"
        project_property_tags(unknown, self.registry)
        self.assertNotIn("shared", unknown.tags)

    def test_unknown_explicit_property_is_reviewable(self) -> None:
        transaction = self.transaction()
        transaction.metadata["property_name"] = "Unknown Tower"

        self.assertIsNone(project_property_tags(transaction, self.registry))
        self.assertTrue(transaction.review_required)
        self.assertIn(
            "UNKNOWN_CONFIGURED_PROPERTY",
            transaction.metadata["property_review_reasons"],
        )

    def test_unknown_utility_reference_does_not_take_shared_default(self) -> None:
        transaction = self.transaction()
        transaction.category = "Electricity & Water"
        transaction.metadata["utility_provider"] = "DEWA"
        transaction.metadata["utility_account_reference"] = "not-configured"

        self.assertIsNone(project_property_tags(transaction, self.registry))
        self.assertTrue(transaction.review_required)
        self.assertNotIn("shared", transaction.tags)
        self.assertIn(
            "UNKNOWN_CONFIGURED_PROPERTY",
            transaction.metadata["property_review_reasons"],
        )

        missing_provider = self.transaction()
        missing_provider.category = "Electricity & Water"
        missing_provider.metadata["utility_account_reference"] = "not-configured"
        self.assertIsNone(project_property_tags(missing_provider, self.registry))
        self.assertTrue(missing_provider.review_required)
        self.assertNotIn("shared", missing_provider.tags)
