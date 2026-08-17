import json
import shutil
import tempfile
import unittest
from pathlib import Path

from reportlab.pdfgen import canvas

from finance_tracker.ingestion_jobs import IngestionJobRunner, compact_ai_handoff


EI_LINES = (
    "Statement of Card Account",
    "From: 1st Jul 2026",
    "31st Jul 2026",
    "To:",
    "OPENING BALANCE 100.00",
    "PRIMARY CARD NO:5424XXXXXXXX0082",
    "02 JUL 02 JUL TRANSFER PAYMENT RECEIVED THANK YOU 100.00CR",
    "10 JUL 09 JUL UNKNOWN MARKETPLACE DUBAI ARE 25.00",
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
        self.assertNotIn("ai_requests", first)
        self.assertEqual(
            first["ai_request_count"], len(first["ai_handoff"]["requests"])
        )
        ai_request = next(
            item
            for item in first["ai_handoff"]["requests"]
            if item["policy_id"] == "classify-unresolved"
            and "tags" in item["allowed_fields"]
        )
        enriched = self.runner.submit(
            {
                **request,
                "ai_responses": [
                    {
                        "transaction_id": ai_request["transaction_id"],
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
                        "transaction_id": ai_request["transaction_id"],
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

    def test_idempotent_replay_upgrades_legacy_result_from_audit_manifest(self) -> None:
        pdf = self.runner.inbox / "legacy-result-ei.pdf"
        write_ei_pdf(pdf)
        request = {
            "type": "STATEMENT_PDF",
            "source_path": pdf.name,
            "card_code": "EI_AMAZON",
            "actual_mode": "STAGE",
            "source_message_id": "legacy-result-message",
        }
        first = self.runner.submit(request)
        result_path = self.runner.jobs / first["job_id"] / "result.json"
        legacy = json.loads(result_path.read_text(encoding="utf-8"))
        legacy.pop("ai_handoff")
        legacy.pop("ai_handoff_complete")
        legacy.pop("result_schema_version")
        result_path.write_text(json.dumps(legacy, indent=2), encoding="utf-8")

        replay = self.runner.submit(request)

        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["result_schema_version"], 3)
        self.assertEqual(
            len(replay["ai_handoff"]["requests"]), replay["ai_request_count"]
        )
        persisted = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertFalse(persisted["idempotent_replay"])
        self.assertIn("ai_handoff", persisted)

    def test_idempotent_replay_rebuilds_outdated_compact_handoff(self) -> None:
        pdf = self.runner.inbox / "old-handoff-ei.pdf"
        write_ei_pdf(pdf)
        request = {
            "type": "STATEMENT_PDF",
            "source_path": pdf.name,
            "card_code": "EI_AMAZON",
            "actual_mode": "STAGE",
            "source_message_id": "old-handoff-message",
        }
        first = self.runner.submit(request)
        result_path = self.runner.jobs / first["job_id"] / "result.json"
        old_result = json.loads(result_path.read_text(encoding="utf-8"))
        old_result["ai_handoff"]["schema_version"] = 1
        old_result["result_schema_version"] = 2
        result_path.write_text(json.dumps(old_result, indent=2), encoding="utf-8")

        replay = self.runner.submit(request)

        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["result_schema_version"], 3)
        self.assertEqual(replay["ai_handoff"]["schema_version"], 2)

    def test_compact_ai_handoff_deduplicates_policy_and_transaction_context(self) -> None:
        transaction_one = {"transaction_id": "tx-1", "merchant_raw": "Example"}
        transaction_two = {"transaction_id": "tx-2", "merchant_raw": "Other"}
        classify = {
            "schema_version": 1,
            "policy_version": 1,
            "instruction": "Classify",
            "allowed_values": {"category": ["Groceries"]},
            "allowed_tags": ["grocery"],
            "response_contract": {"proposals": []},
        }
        evidence = {
            **classify,
            "instruction": "Decide whether evidence should be searched",
            "allowed_values": {"evidence_policy": ["SEARCH_PURCHASE_EVIDENCE"]},
        }

        handoff = compact_ai_handoff([
            {
                **classify,
                "policy_id": "classify",
                "allowed_fields": ["category"],
                "transaction": transaction_one,
            },
            {
                **evidence,
                "policy_id": "evidence",
                "allowed_fields": ["evidence_policy"],
                "transaction": transaction_one,
            },
            {
                **classify,
                "policy_id": "classify",
                "allowed_fields": ["category"],
                "transaction": transaction_two,
            },
        ])

        self.assertEqual(list(handoff["policies"]), ["classify", "evidence"])
        self.assertEqual(list(handoff["transactions"]), ["tx-1", "tx-2"])
        self.assertEqual(len(handoff["requests"]), 3)
        self.assertTrue(all(item["transaction_ref"] for item in handoff["requests"]))

    def test_compact_ai_handoff_preserves_evolving_transaction_context(self) -> None:
        shared = {
            "policy_version": 1,
            "allowed_values": {},
            "allowed_tags": ["gift"],
            "response_contract": {"proposals": []},
        }
        handoff = compact_ai_handoff([
            {
                **shared,
                "policy_id": "classify",
                "instruction": "Classify",
                "allowed_fields": ["tags"],
                "transaction": {"transaction_id": "tx-1", "tags": []},
            },
            {
                **shared,
                "policy_id": "evidence",
                "instruction": "Decide evidence",
                "allowed_fields": ["evidence_policy"],
                "transaction": {"transaction_id": "tx-1", "tags": ["gift"]},
            },
        ])

        self.assertEqual(handoff["schema_version"], 2)
        self.assertEqual(len(handoff["transactions"]), 2)
        references = [item["transaction_ref"] for item in handoff["requests"]]
        self.assertEqual(len(set(references)), 2)
        self.assertTrue(all(reference.startswith("tx-1@") for reference in references))

    def test_compact_ai_handoff_rejects_duplicate_request_identity(self) -> None:
        request = {
            "policy_id": "classify",
            "policy_version": 1,
            "instruction": "Classify",
            "allowed_values": {},
            "allowed_tags": [],
            "response_contract": {"proposals": []},
            "allowed_fields": ["category"],
            "transaction": {"transaction_id": "tx-1"},
        }

        with self.assertRaisesRegex(ValueError, "Duplicate AI request"):
            compact_ai_handoff([request, request])

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

    def test_statement_request_cannot_select_another_secret(self) -> None:
        pdf = self.runner.inbox / "synthetic-ei-statement.pdf"
        write_ei_pdf(pdf)
        with self.assertRaisesRegex(ValueError, "must match the configured source registry"):
            self.runner.submit(
                {
                    "type": "STATEMENT_PDF",
                    "source_path": pdf.name,
                    "card_code": "EI_AMAZON",
                    "actual_mode": "STAGE",
                    "password_env": "ADCB_STATEMENT_PASSWORD",
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

    def test_completed_ai_handoff_requires_one_response_per_request(self) -> None:
        pdf = self.runner.inbox / "synthetic-ei-statement.pdf"
        write_ei_pdf(pdf)
        with self.assertRaisesRegex(ValueError, "did not answer every request"):
            self.runner.submit(
                {
                    "type": "STATEMENT_PDF",
                    "source_path": pdf.name,
                    "card_code": "EI_AMAZON",
                    "actual_mode": "STAGE",
                    "ai_handoff_complete": True,
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
