import json
import shutil
import tempfile
import unittest
from pathlib import Path

from reportlab.pdfgen import canvas

from finance_tracker.ingestion_jobs import IngestionJobRunner


EI_LINES = (
    "Statement of Card Account",
    "From: 1st Jul 2026",
    "31st Jul 2026",
    "To:",
    "OPENING BALANCE 100.00",
    "PRIMARY CARD NO:5424XXXXXXXX0082",
    "02 JUL 02 JUL TRANSFER PAYMENT RECEIVED THANK YOU 100.00CR",
    "10 JUL 09 JUL AMAZON.AE DUBAI ARE 25.00",
    "Card Limit Available Limit Minimum Payment Due Payment Due Date Total Payment Due Profit/Other Charges (AED) Current Balance (AED)",
    "50,000.00 49,975.00 25.00 25/08/26 25.00 0.00 25.00",
)


def write_ei_pdf(path: Path) -> None:
    document = canvas.Canvas(str(path))
    y = 800
    for line in EI_LINES:
        document.drawString(36, y, line)
        y -= 18
    document.save()


class IngestionJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runner = IngestionJobRunner(self.root, Path.cwd())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_synthetic_email_pdf_stages_full_actual_manifest_idempotently(self) -> None:
        pdf = self.runner.inbox / "synthetic-ei-statement.pdf"
        write_ei_pdf(pdf)
        request = {
            "type": "STATEMENT_PDF",
            "source_path": pdf.name,
            "card_code": "EI_AMAZON",
            "actual_mode": "STAGE",
            "source_message_id": "synthetic-email-message",
            "source_attachment_id": "synthetic-attachment",
        }
        first = self.runner.submit(request)
        replay = self.runner.submit(request)
        manifest = json.loads(Path(first["manifest_path"]).read_text(encoding="utf-8"))
        self.assertEqual(first["status"], "STAGED")
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertTrue(manifest["statement"]["balance_tied"])
        self.assertEqual(manifest["statement"]["transaction_count"], 2)
        self.assertEqual(
            manifest["source_evidence"]["source_message_id"], "synthetic-email-message"
        )
        self.assertEqual(
            manifest["source_evidence"]["source_attachment_id"], "synthetic-attachment"
        )
        self.assertEqual(len(manifest["source_evidence"]["document_sha256"]), 64)
        self.assertEqual(first["envelope_count"], 1)

    def test_placeholder_statement_adapter_is_blocked_before_parsing(self) -> None:
        pdf = self.runner.inbox / "unknown-rak.pdf"
        pdf.write_bytes(b"not a real pdf")
        with self.assertRaisesRegex(ValueError, "placeholder"):
            self.runner.submit(
                {
                    "type": "STATEMENT_PDF",
                    "source_path": pdf.name,
                    "card_code": "RAK_WORLD",
                    "actual_mode": "STAGE",
                }
            )

    def test_browser_capture_stages_without_a_second_ledger(self) -> None:
        source = self.runner.inbox / "browser-capture.json"
        shutil.copyfile(Path("tests/fixtures/browser-capture.sample.json"), source)
        result = self.runner.submit(
            {
                "type": "BROWSER_CAPTURE",
                "source_path": source.name,
                "actual_mode": "STAGE",
            }
        )
        self.assertEqual(result["status"], "STAGED")
        self.assertGreaterEqual(result["envelope_count"], 1)

    def test_source_path_cannot_escape_inbox(self) -> None:
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.runner.submit(
                {
                    "type": "BROWSER_CAPTURE",
                    "source_path": "../outside.json",
                    "actual_mode": "STAGE",
                }
            )


if __name__ == "__main__":
    unittest.main()
