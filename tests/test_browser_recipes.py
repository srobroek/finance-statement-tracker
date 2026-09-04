from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from finance_tracker.browser_recipes import render_recipe, validate_registry
from finance_tracker.browser_sources import (
    load_browser_sources,
    validate_source_coverage,
)

ROOT = Path(__file__).resolve().parents[1]


class BrowserRecipeTests(unittest.TestCase):
    def test_mfa_sources_declare_the_headed_write_disabled_boundary(self) -> None:
        sources = load_browser_sources(ROOT / "config" / "browser-sources.json")
        contract = sources["browser_contract"]
        self.assertEqual("HEADED_ON_DEMAND", contract["capture_mode"])
        self.assertEqual("USER_COMPLETED", contract["authentication"])
        self.assertFalse(contract["session_persistence"])
        self.assertFalse(contract["actual_mutation"])
        self.assertFalse(contract["cashback_mutation"])
        self.assertEqual("N8N", contract["archive_owner"])
        self.assertEqual("FINANCE_BROWSER_ARCHIVE_PARENT_ID", contract["archive_parent_binding"])
        self.assertEqual("finance_document_operations", contract["archive_receipt_table"])
        self.assertEqual("SHARED_STATEMENT_PIPELINE", contract["headless_workflow_code"])
        for provider_id in ("fab", "sarwa", "adcb"):
            metadata = json.loads(
                (ROOT / "browser_adapters" / provider_id / "provider.json").read_text(encoding="utf-8")
            )
            self.assertEqual("HEADED_ON_DEMAND", metadata["execution"]["mode"])
            self.assertEqual("USER_COMPLETED", metadata["execution"]["authentication"])
            self.assertFalse(metadata["execution"]["session_persistence"])
            self.assertFalse(metadata["execution"]["actual_mutation"])
            self.assertFalse(metadata["execution"]["cashback_mutation"])
            recipe = (ROOT / "browser_adapters" / provider_id / "provider.recipe").read_text(encoding="utf-8")
            self.assertIn("PAUSE_FOR_USER reason:", recipe)
            self.assertIn("never type, inspect, copy, log, or store", recipe)

    def test_browser_capture_schema_contract_is_immutable_redacted_and_write_disabled(self) -> None:
        schema = json.loads(
            (ROOT / "config" / "browser-capture-schema-v1.json").read_text(encoding="utf-8")
        )
        self.assertIn("capture_contract", schema["required"])
        self.assertIn("provenance", schema["required"])
        contract = schema["properties"]["capture_contract"]
        self.assertEqual(
            [
                "capture_mode",
                "redaction",
                "immutability",
                "handoff_workflow",
                "actual_mutation",
                "cashback_mutation",
            ],
            contract["required"],
        )
        properties = contract["properties"]
        self.assertEqual("HEADED_ON_DEMAND", properties["capture_mode"]["const"])
        self.assertEqual("REDACTED", properties["redaction"]["const"])
        self.assertEqual("SHA256_ARCHIVED", properties["immutability"]["const"])
        self.assertIs(properties["actual_mutation"]["const"], False)
        self.assertIs(properties["cashback_mutation"]["const"], False)
        provenance = schema["properties"]["provenance"]
        self.assertEqual(
            ["capture_id", "captured_at", "source_content_sha256", "hash_algorithm"],
            provenance["required"],
        )
        self.assertEqual("SHA-256", provenance["properties"]["hash_algorithm"]["const"])
        self.assertEqual(
            ["kind", "source_content_sha256"],
            schema["properties"]["artifact"]["required"],
        )
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

    def test_sarwa_holdings_recipe_is_portfolio_and_as_of_bounded(self) -> None:
        data = json.loads(
            (ROOT / "browser_adapters" / "sarwa" / "data" / "holdings" / "data.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(["portfolio_ref", "as_of_date"], data["inputs"])
        recipe = (
            ROOT / "browser_adapters" / "sarwa" / "data" / "holdings" / "recipe"
        ).read_text(encoding="utf-8")
        self.assertIn('SELECT field: portfolio_ref, option: "<portfolio_ref>"', recipe)
        self.assertIn('SELECT field: as_of_date, option: "<as_of_date>"', recipe)
        self.assertIn("Stop at 100 pages", recipe)
        self.assertIn("pagination_exhausted", recipe)
        self.assertIn("duplicate stable instrument IDs are rejected", recipe)
        self.assertNotIn("For each Invest account", recipe)

    def test_account_last4_must_be_exactly_four_digits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "accounts": [{"actual_account": "Bad", "label": "Bad", "account_last4": "123"}],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exactly four"):
                load_browser_sources(path)

    def test_account_names_are_unique_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "accounts": [
                    {"actual_account": "Card", "label": "One"},
                    {"actual_account": "card", "label": "Two"},
                ],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicated"):
                load_browser_sources(path)

    def test_read_recipe_names_the_visible_value_and_output_field(self) -> None:
        rendered = render_recipe(
            "fab",
            "current-account-balance",
            {"account_ref": "Current 1234"},
            ROOT / "browser_adapters",
        )

        self.assertIn('READ selector: "Balance", as: "balance"', rendered["data_recipe"])

    def test_amazon_browser_capture_is_removed_from_the_required_path(self) -> None:
        sources = load_browser_sources(ROOT / "config" / "browser-sources.json")
        provider_ids = [row["provider_id"] for row in validate_registry(ROOT / "browser_adapters")["providers"]]
        self.assertNotIn("amazon", provider_ids)
        self.assertNotIn("amazon-orders", json.dumps(sources))
        amazon_path = ROOT / "browser_adapters" / "amazon"
        self.assertFalse(amazon_path.exists() and any(path.is_file() for path in amazon_path.rglob("*")))


if __name__ == "__main__":
    unittest.main()
