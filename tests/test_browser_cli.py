from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from finance_tracker.cli import main


ROOT = Path(__file__).resolve().parents[1]


class BrowserCliTests(unittest.TestCase):
    def test_status_and_recipe_commands_write_machine_readable_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status_path = root / "status.json"
            recipe_path = root / "recipe.json"
            params_path = root / "params.json"
            params_path.write_text(json.dumps({
                "card_ref": "Cashback 8833",
                "from_date": "01/07/2026",
                "to_date": "31/07/2026",
            }), encoding="utf-8")
            self.assertEqual(0, main([
                "browser-adapters-status",
                "--sources", str(ROOT / "config" / "browser-sources.json"),
                "--adapters-root", str(ROOT / "browser_adapters"),
                "--output", str(status_path),
            ]))
            self.assertEqual(0, main([
                "browser-render-recipe",
                "--provider", "adcb",
                "--data-id", "credit-card-transactions",
                "--params", str(params_path),
                "--adapters-root", str(ROOT / "browser_adapters"),
                "--output", str(recipe_path),
            ]))
            self.assertEqual("ok", json.loads(status_path.read_text(encoding="utf-8"))["status"])
            self.assertIn("Cashback 8833", json.loads(recipe_path.read_text(encoding="utf-8"))["data_recipe"])

    def test_official_export_command_builds_capture_for_configured_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export_path = root / "wio.csv"
            capture_path = root / "capture.json"
            export_path.write_text(
                "Date,Description,Debit,Credit\n2026-07-31,Groceries,125.00,\n",
                encoding="utf-8",
            )
            self.assertEqual(0, main([
                "browser-export-file",
                "--provider", "generic-csv",
                "--data-id", "csv-transactions",
                "--file", str(export_path),
                "--sources", str(ROOT / "config" / "browser-sources.json"),
                "--actual-account", "Wio Credit Card · 4113 / 5009",
                "--adapters-root", str(ROOT / "browser_adapters"),
                "--output", str(capture_path),
            ]))
            capture = json.loads(capture_path.read_text(encoding="utf-8"))
            self.assertEqual("TRANSACTION_ROWS", capture["artifact"]["kind"])
            self.assertEqual("WIO_CREDIT", capture["account"]["card_code"])


if __name__ == "__main__":
    unittest.main()
