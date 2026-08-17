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

    def test_statement_evidence_archive_updates_catalogue_idempotently(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "statement.pdf"
            catalogue = root / "catalogue.json"
            source.write_bytes(b"statement original")
            arguments = [
                "statement-evidence-archive",
                "--source", str(source),
                "--evidence-root", str(root),
                "--catalogue", str(catalogue),
                "--bank", "Wio",
                "--card-code", "WIO_CREDIT",
                "--statement-date", "2026-08-01",
                "--period-start", "2026-07-01",
                "--period-end", "2026-08-01",
                "--closing-balance-aed", "-274.40",
                "--reference", "account-5009-jul-2026",
                "--payment-due-date", "2026-08-01",
                "--message-id", "mail-1",
                "--attachment-id", "attachment-1",
            ]

            self.assertEqual(main(arguments), 0)
            self.assertEqual(main(arguments), 0)

            rows = json.loads(catalogue.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["entity_id"], "WIO_CREDIT:2026-07-01:2026-08-01")
            self.assertEqual(rows[0]["closing_balance_aed"], "-274.40")
            self.assertEqual(rows[0]["message_id"], "mail-1")
