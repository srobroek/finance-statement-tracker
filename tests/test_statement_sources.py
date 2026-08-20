import shlex
import subprocess
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
    ROOT = Path(__file__).resolve().parents[1]

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

    def test_runtime_passwords_never_appear_in_tracked_files(self) -> None:
        envfile = Path("/tmp/finance-n8n-session.env")
        if not envfile.is_file():
            self.skipTest("protected finance runtime envfile unavailable")

        passwords: dict[str, str] = {}
        for raw_line in envfile.read_text(encoding="utf-8").splitlines():
            if not raw_line or raw_line.lstrip().startswith("#") or "=" not in raw_line:
                continue
            key, raw_value = raw_line.split("=", 1)
            values = shlex.split(raw_value, comments=False)
            if key.endswith("_PASSWORD") and values and values[0]:
                passwords[key] = values[0]
        self.assertTrue(
            {"ADCB_STATEMENT_PASSWORD", "EI_STATEMENT_PASSWORD"}.issubset(passwords),
            "protected envfile must provide both statement password keys",
        )

        tracked_paths = subprocess.check_output(
            ["git", "ls-files", "-z"], cwd=self.ROOT
        ).split(b"\0")
        for key, value in passwords.items():
            needle = value.encode("utf-8")
            matches: list[str] = []
            for raw_path in tracked_paths:
                if not raw_path:
                    continue
                path = self.ROOT / raw_path.decode("utf-8")
                try:
                    content = path.read_bytes()
                except (OSError, ValueError):
                    continue
                if needle in content:
                    matches.append(str(path.relative_to(self.ROOT)))
            self.assertFalse(
                matches,
                f"runtime password key {key} appears in tracked files: {matches}",
            )


if __name__ == "__main__":
    unittest.main()
