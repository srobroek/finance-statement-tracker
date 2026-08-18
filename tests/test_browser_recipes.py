from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from finance_tracker.browser_recipes import render_recipe, validate_registry
from finance_tracker.browser_sources import load_browser_sources, validate_source_coverage


ROOT = Path(__file__).resolve().parents[1]


class BrowserRecipeTests(unittest.TestCase):
    def test_migrated_registry_is_valid(self) -> None:
        result = validate_registry(ROOT / "browser_adapters")
        self.assertEqual("ok", result["status"])
        self.assertEqual(
            ["adcb", "emirates-islamic", "fab", "generic-csv", "sarwa", "wio"],
            [row["provider_id"] for row in result["providers"]],
        )
        self.assertEqual([], result["violations"])

    def test_recipe_rendering_substitutes_only_declared_parameters(self) -> None:
        rendered = render_recipe(
            "adcb",
            "credit-card-transactions",
            {"card_ref": "Cashback 8833", "from_date": "01/07/2026", "to_date": "31/07/2026"},
            ROOT / "browser_adapters",
        )
        self.assertIn('option: "Cashback 8833"', rendered["data_recipe"])
        self.assertNotIn("<from_date>", rendered["data_recipe"])
        self.assertIn("GOTO", rendered["provider_recipe"])

    def test_recipe_rendering_rejects_missing_and_secret_parameters(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing"):
            render_recipe("adcb", "credit-card-transactions", {}, ROOT / "browser_adapters")
        with self.assertRaisesRegex(ValueError, "unknown"):
            render_recipe(
                "generic-csv",
                "csv-transactions",
                {"password": "not-allowed"},
                ROOT / "browser_adapters",
            )

    def test_source_coverage_includes_accounts_and_supplemental_sources(self) -> None:
        sources = load_browser_sources(ROOT / "config" / "browser-sources.json")
        result = validate_source_coverage(sources, ROOT / "browser_adapters")
        self.assertEqual("ok", result["status"])
        self.assertEqual(7, len(result["coverage"]))
        self.assertEqual(2, len(result["supplemental"]))
        self.assertEqual("ADAPTER_REQUIRED", result["coverage"][-1]["status"])

    def test_account_last4_must_be_exactly_four_digits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "accounts": [{"actual_account": "Bad", "label": "Bad", "account_last4": "123"}],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exactly four"):
                load_browser_sources(path)

    def test_read_recipe_names_the_visible_value_and_output_field(self) -> None:
        rendered = render_recipe(
            "fab",
            "current-account-balance",
            {"account_ref": "Current 1234"},
            ROOT / "browser_adapters",
        )

        self.assertIn('READ selector: "Balance", as: "balance"', rendered["data_recipe"])


if __name__ == "__main__":
    unittest.main()
