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
            "source_kind": "outlook_attachment",
            "source_filename": "EI statement July 2026.pdf",
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
        self.assertEqual(manifest["source_evidence"]["source_kind"], "outlook_attachment")
        self.assertEqual(
            manifest["source_evidence"]["source_filename"],
            "EI statement July 2026.pdf",
        )
        self.assertEqual(len(manifest["source_evidence"]["document_sha256"]), 64)
        self.assertEqual(first["envelope_count"], 1)
        statement_rows = manifest["envelopes"][0]["records"]
        self.assertTrue(statement_rows)
        self.assertTrue(
            all(
                "message:synthetic-email-message" in row["notes"]
                for row in statement_rows
            )
        )
        self.assertGreater(first["ai_request_count"], 0)
        self.assertEqual(first["ai_request_count"], len(first["ai_requests"]))
        ai_request = next(
            item
            for item in manifest["ai_requests"]
            if item["policy_id"] == "classify-unresolved"
            and "tags" in item["allowed_fields"]
        )
        enriched = self.runner.submit(
            {
                **request,
                "ai_responses": [
                    {
                        "transaction_id": ai_request["transaction"]["transaction_id"],
                        "policy_id": ai_request["policy_id"],
                        "provider": "codex-scheduled-task",
                        "model": "gpt-5.6-sol",
                        "proposals": [
                            {
                                "field": "tags",
                                "value": ["gift"],
                                "confidence": 0.95,
                                "rationale": "Synthetic E2E proposal",
                                "source_refs": ["synthetic-email-message"],
                            }
                        ],
                    }
                ],
            }
        )
        enriched_manifest = json.loads(
            Path(enriched["manifest_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(enriched["ai_response_count"], 1)
        self.assertEqual(enriched["ai_trace_count"], 1)
        self.assertEqual(enriched["ai_accepted_count"], 1)
        self.assertEqual(enriched["ai_rejected_count"], 0)
        self.assertTrue(enriched_manifest["ai_trace"][0]["accepted"])
        self.assertTrue(
            any(
                "#gift" in row["notes"]
                for envelope in enriched_manifest["envelopes"]
                for row in envelope["records"]
            )
        )
        rejected = self.runner.submit(
            {
                **request,
                "ai_responses": [
                    {
                        "transaction_id": ai_request["transaction"]["transaction_id"],
                        "policy_id": ai_request["policy_id"],
                        "proposals": [
                            {
                                "field": "tags",
                                "value": ["gift"],
                                "confidence": 0.1,
                                "rationale": "Deliberately below threshold",
                                "source_refs": [],
                            }
                        ],
                    }
                ],
            }
        )
        self.assertEqual(rejected["staging_status"], "REVIEW_REQUIRED")
        self.assertEqual(rejected["review_count"], 1)
        self.assertEqual(rejected["ai_rejected_count"], 1)

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

    def test_unmatched_ai_response_is_rejected(self) -> None:
        pdf = self.runner.inbox / "synthetic-ei-statement.pdf"
        write_ei_pdf(pdf)
        with self.assertRaisesRegex(ValueError, "did not match generated requests"):
            self.runner.submit(
                {
                    "type": "STATEMENT_PDF",
                    "source_path": pdf.name,
                    "card_code": "EI_AMAZON",
                    "actual_mode": "STAGE",
                    "ai_responses": [
                        {
                            "transaction_id": "invented-transaction",
                            "policy_id": "classify-unresolved",
                            "proposals": [],
                        }
                    ],
                }
            )

    def test_actual_preflight_requires_completed_ai_handoff(self) -> None:
        pdf = self.runner.inbox / "synthetic-ei-statement.pdf"
        write_ei_pdf(pdf)
        with self.assertRaisesRegex(ValueError, "ai_handoff_complete=true"):
            self.runner.submit(
                {
                    "type": "STATEMENT_PDF",
                    "source_path": pdf.name,
                    "card_code": "EI_AMAZON",
                    "actual_mode": "PREFLIGHT",
                }
            )

    def test_review_required_browser_capture_cannot_reach_actual(self) -> None:
        source = self.runner.inbox / "browser-capture.json"
        shutil.copyfile(Path("tests/fixtures/browser-capture.sample.json"), source)
        with self.assertRaisesRegex(ValueError, "review-free staging manifest"):
            self.runner.submit(
                {
                    "type": "BROWSER_CAPTURE",
                    "source_path": source.name,
                    "actual_mode": "PREFLIGHT",
                    "ai_handoff_complete": True,
                }
            )


if __name__ == "__main__":
    unittest.main()
