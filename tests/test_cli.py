import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from finance_tracker.cli import main


class ActualExportCliTests(TestCase):
    def test_actual_export_writes_bridge_envelopes(self) -> None:
        rows = [
            {
                "transaction_id": "poc:one",
                "transaction_at": "2026-08-16T10:00:00",
                "account": "POC Current",
                "merchant_raw": "CARREFOUR 123",
                "vendor": "Carrefour",
                "amount_aed": "25.50",
                "source_type": "statement_pdf",
                "tags": ["groceries"],
            }
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "transactions.json"
            output = root / "actual-import.json"
            source.write_text(json.dumps(rows), encoding="utf-8")

            result = main(
                ["actual-export", "--input", str(source), "--output", str(output)]
            )

            self.assertEqual(result, 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["account"], "POC Current")
            self.assertEqual(payload[0]["records"][0]["amount"], -2550)
            self.assertEqual(payload[0]["records"][0]["imported_id"], "poc:one")
