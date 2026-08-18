from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest import TestCase

from finance_tracker.models import Transaction
from finance_tracker.properties import load_property_registry, project_property_tags


ROOT = Path(__file__).resolve().parent.parent


class PropertyRegistryTests(TestCase):
    def setUp(self) -> None:
        self.registry = load_property_registry(ROOT / "config" / "properties.json")

    @staticmethod
    def transaction() -> Transaction:
        return Transaction("property-1", datetime(2026, 8, 18), "ADCB", "EMPOWER", "100")

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
