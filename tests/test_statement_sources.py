import unittest
from pathlib import Path

from finance_tracker.statement_sources import (
    load_statement_sources,
    require_active_statement_adapter,
    require_active_statement_source,
    validate_statement_adapter_coverage,
)
from finance_tracker.statements import DEFAULT_STATEMENT_ADAPTERS


class StatementSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sources = load_statement_sources(Path("config/statement-sources.json"))

    def test_registry_covers_implemented_adapters_and_preserves_placeholders(self) -> None:
        validate_statement_adapter_coverage(
            self.sources,
            DEFAULT_STATEMENT_ADAPTERS.codes,
        )
        placeholders = {source.card_code for source in self.sources if not source.adapter_active}
        self.assertEqual(placeholders, {"RAK_WORLD", "SC_PLATINUM_X"})

    def test_placeholder_card_cannot_be_ingested(self) -> None:
        with self.assertRaisesRegex(ValueError, "placeholder"):
            require_active_statement_adapter(self.sources, "RAK_WORLD", None)

    def test_active_card_resolves_only_its_registered_adapter(self) -> None:
        self.assertEqual(
            require_active_statement_adapter(self.sources, "EI_AMAZON", None),
            "emirates_islamic_v1",
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            require_active_statement_adapter(self.sources, "EI_AMAZON", "adcb_v1")

        source = require_active_statement_source(self.sources, "EI_AMAZON", None)
        self.assertEqual(source.password_env, "EI_STATEMENT_PASSWORD")

    def test_unencrypted_source_has_no_password_environment(self) -> None:
        source = require_active_statement_source(self.sources, "WIO_CREDIT", None)
        self.assertIsNone(source.password_env)
        self.assertEqual(
            source.email_senders,
            (
                "communications@email.wio.io",
                "communications@mail.wio.io",
            ),
        )


if __name__ == "__main__":
    unittest.main()
