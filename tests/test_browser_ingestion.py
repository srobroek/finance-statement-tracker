import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from finance_tracker.browser_ingestion import (
    build_browser_ingestion_run,
    export_browser_capture_for_actual,
)


class BrowserIngestionTests(TestCase):
    def config(self):
        return {
            "schema_version": 1,
            "accounts": [
                {
                    "name": "Wio Current Account · 4113",
                    "type": "checking",
                    "card_code": "WIO_4113",
                    "card_last4": ["4113"],
                    "owner": "Owner A",
                },
                {
                    "name": "Emirates Islamic Amazon Credit Card · 0082",
                    "type": "credit",
                    "card_code": "EI_AMAZON",
                    "card_last4": ["0082"],
                },
            ],
        }

    def capture(self):
        return {
            "schema_version": 1,
            "capture_id": "wio-4113-2026-08",
            "source": {
                "provider": "Wio",
                "site": "Wio",
                "url": "https://bank.example.test/accounts/4113?token=must-not-persist#transactions",
                "page_context": "Transactions",
                "captured_at": "2026-08-16T12:00:00Z",
                "capture_method": "VISIBLE_ROWS",
                "date_range": {"start": "2026-08-01", "end": "2026-08-16"},
                "limitations": ["Pending rows unavailable"],
            },
            "artifact": {"kind": "TRANSACTION_ROWS"},
            "account": {
                "label": "Wio Current Account ending 4113",
                "account_last4": "4113",
                "currency": "AED",
                "balance": "1000.00",
                "balance_as_of": "2026-08-16T12:00:00Z",
            },
            "rows": [
                {
                    "transaction_date": "2026-08-15",
                    "description": "EXAMPLE MERCHANT",
                    "amount_aed": "25.50",
                    "direction": "DEBIT",
                    "channel": "ONLINE",
                }
            ],
        }

    def test_visible_rows_are_stable_reviewable_and_do_not_keep_url_secrets(self):
        first = build_browser_ingestion_run(self.capture(), self.config())
        second = build_browser_ingestion_run(self.capture(), self.config())

        self.assertEqual(first.staging_status, "REVIEW_REQUIRED")
        self.assertEqual(first.review_count, 1)
        self.assertEqual(first.source["url"], "https://bank.example.test/accounts/4113")
        self.assertEqual(
            first.transactions[0]["transaction_id"],
            second.transactions[0]["transaction_id"],
        )
        self.assertEqual(first.transactions[0]["source_type"], "browser_portal")
        self.assertFalse(first.envelopes[0]["default_cleared"])
        self.assertIn("#browser-import", first.envelopes[0]["records"][0]["notes"])
        self.assertIn("#owner-owner-a", first.envelopes[0]["records"][0]["notes"])

    def test_tied_official_statement_rows_are_authoritative_and_cleared(self):
        capture = self.capture()
        capture["capture_id"] = "ei-0082-2026-08"
        capture["source"]["provider"] = "Emirates Islamic"
        capture["source"]["capture_method"] = "OFFICIAL_EXPORT"
        capture["artifact"]["kind"] = "STATEMENT_ROWS"
        capture["account"] = {
            "label": "EI Amazon ending 0082",
            "account_last4": "0082",
            "currency": "AED",
        }
        capture["statement"] = {
            "statement_reference": "EI-0082-2026-08",
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
            "opening_balance_aed": "100.00",
            "closing_balance_aed": "120.00",
            "balance_convention": "LIABILITY",
        }
        capture["rows"] = [
            {
                "source_id": "purchase-1",
                "transaction_date": "2026-08-15",
                "description": "EXAMPLE PURCHASE",
                "amount_aed": "30.00",
                "direction": "DEBIT",
                "transaction_type": "PURCHASE",
            },
            {
                "source_id": "payment-1",
                "transaction_date": "2026-08-16",
                "description": "PAYMENT RECEIVED",
                "amount_aed": "10.00",
                "direction": "CREDIT",
                "transaction_type": "PAYMENT",
            },
        ]

        run = build_browser_ingestion_run(capture, self.config())

        self.assertEqual(run.staging_status, "READY_FOR_APPROVAL")
        self.assertEqual(run.review_count, 0)
        self.assertTrue(run.statement_check["balance_tied"])
        self.assertTrue(run.envelopes[0]["default_cleared"])
        self.assertTrue(all(row["source_type"] == "browser_statement" for row in run.transactions))

    def test_unbalanced_statement_rows_are_blocked_from_partial_import(self):
        capture = self.capture()
        capture["source"]["capture_method"] = "OFFICIAL_EXPORT"
        capture["artifact"]["kind"] = "STATEMENT_ROWS"
        capture["statement"] = {
            "statement_reference": "WIO-4113-2026-08",
            "opening_balance_aed": "100.00",
            "closing_balance_aed": "999.00",
            "balance_convention": "ASSET",
        }

        run = build_browser_ingestion_run(capture, self.config())

        self.assertEqual(run.staging_status, "REVIEW_REQUIRED")
        self.assertEqual(run.envelopes, ())
        self.assertTrue(run.import_blockers)

    def test_account_snapshot_never_creates_a_balance_transaction(self):
        capture = self.capture()
        capture["source"]["capture_method"] = "ACCOUNT_OVERVIEW"
        capture["artifact"]["kind"] = "ACCOUNT_SNAPSHOT"
        capture.pop("rows")

        run = build_browser_ingestion_run(capture, self.config())

        self.assertEqual(run.staging_status, "ACCOUNT_REVIEW_REQUIRED")
        self.assertEqual(run.envelopes, ())
        self.assertFalse(run.account_snapshot["balance_posting_allowed"])

    def test_statement_pdf_routes_to_existing_statement_pipeline(self):
        capture = self.capture()
        capture["source"]["capture_method"] = "STATEMENT_DOWNLOAD"
        capture["artifact"] = {"kind": "STATEMENT_PDF", "local_path": "statement.pdf"}
        capture.pop("rows")

        run = build_browser_ingestion_run(capture, self.config())

        self.assertEqual(run.staging_status, "ROUTE_TO_STATEMENT_PIPELINE")
        self.assertEqual(run.envelopes, ())

    def test_sensitive_browser_state_is_rejected(self):
        capture = self.capture()
        capture["source"]["cookies"] = "secret-cookie"

        with self.assertRaisesRegex(ValueError, "forbidden sensitive field"):
            build_browser_ingestion_run(capture, self.config())

    def test_source_url_rejects_embedded_credentials(self):
        capture = self.capture()
        capture["source"]["url"] = "https://user:password@bank.example/accounts"

        with self.assertRaisesRegex(ValueError, "must not contain credentials"):
            build_browser_ingestion_run(capture, self.config())

    def test_duplicate_portal_source_ids_are_rejected(self):
        capture = self.capture()
        row = capture["rows"][0]
        row["source_id"] = "duplicate"
        capture["rows"].append(deepcopy(row))

        with self.assertRaisesRegex(ValueError, "Duplicate browser source_id"):
            build_browser_ingestion_run(capture, self.config())

    def test_export_writes_portable_actual_handoff(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            capture_path = root / "capture.json"
            config_path = root / "config.json"
            output_path = root / "browser-run.json"
            capture_path.write_text(json.dumps(self.capture()), encoding="utf-8")
            config_path.write_text(json.dumps(self.config()), encoding="utf-8")

            run = export_browser_capture_for_actual(capture_path, config_path, output_path)

            self.assertTrue(output_path.is_file())
            saved = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["capture_id"], run.capture_id)
            self.assertEqual(saved["envelopes"][0]["account"], "Wio Current Account · 4113")
