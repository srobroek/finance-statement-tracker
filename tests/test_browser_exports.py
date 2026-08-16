from __future__ import annotations

import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from finance_tracker.browser_exports import build_capture_from_export


ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = {
    "label": "Test card",
    "actual_account": "Test card account",
    "card_code": "TEST_CARD",
    "account_last4": "1234",
    "currency": "AED",
}


def _write_minimal_xlsx(path: Path, rows: list[list[str]]) -> None:
    cells = []
    for row_number, row in enumerate(rows, start=1):
        rendered = []
        for column_number, value in enumerate(row, start=1):
            number = column_number
            letters = ""
            while number:
                number, remainder = divmod(number - 1, 26)
                letters = chr(65 + remainder) + letters
            rendered.append(
                f'<c r="{letters}{row_number}" t="inlineStr"><is><t>{value}</t></is></c>'
            )
        cells.append(f'<row r="{row_number}">{"".join(rendered)}</row>')
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(cells)}</sheetData></worksheet>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)


class BrowserExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.captured_at = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)

    def test_adcb_multicard_csv_preserves_card_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "adcb.csv"
            path.write_text(
                "Primary Card Number:XXXXXXXXXXXX8833-365 Cashback Platinum,Card Holder Name :PRIMARY,,\n"
                '31/07/2026,"SPINNEYS DUBAI",DR,"120.50"\n'
                "\n"
                "Supplementary Card Number:XXXXXXXXXXXX6838-365 Cashback Platinum,Card Holder Name :SECONDARY,,\n"
                '30/07/2026,"REFUND",CR,"20.00"\n',
                encoding="utf-8",
            )
            capture = build_capture_from_export(
                "adcb", "credit-card-transactions", path, ACCOUNT,
                adapters_root=ROOT / "browser_adapters", captured_at=self.captured_at,
            )
        self.assertEqual("TRANSACTION_ROWS", capture["artifact"]["kind"])
        self.assertEqual(["8833", "6838"], [row["account_last4"] for row in capture["rows"]])
        self.assertEqual(["primary", "supplementary"], [row["card_role"] for row in capture["rows"]])
        self.assertEqual("CREDIT", capture["rows"][1]["direction"])

    def test_adcb_rejects_a_malformed_candidate_instead_of_partial_import(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.csv"
            path.write_text(
                "Primary Card Number:XXXXXXXXXXXX8833-Card,Holder,,\n"
                "31/07/2026,BAD,UNKNOWN,12.00\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "completeness"):
                build_capture_from_export(
                    "adcb", "credit-card-transactions", path, ACCOUNT,
                    adapters_root=ROOT / "browser_adapters",
                )

    def test_fab_csv_preserves_debit_credit_and_account_last4(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fab.csv"
            path.write_text(
                "Account Number,AE001234567890123456789\nCurrency,AED\n\n"
                "Posting Date,Value date,Description,Debit Amount,Credit Amount,Running Balance\n"
                "31/07/2026,31/07/2026,DEWA,450.00,,1000.00\n"
                "30/07/2026,30/07/2026,SALARY,,5000.00,1450.00\n",
                encoding="utf-8",
            )
            capture = build_capture_from_export(
                "fab", "current-account-transactions", path, ACCOUNT,
                adapters_root=ROOT / "browser_adapters", captured_at=self.captured_at,
            )
        self.assertEqual(["DEBIT", "CREDIT"], [row["direction"] for row in capture["rows"]])
        self.assertEqual("6789", capture["rows"][0]["account_last4"])

    def test_generic_csv_detects_common_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "generic.csv"
            path.write_text(
                "Transaction Date,Description,Amount,Direction\n"
                "2026-07-31,Amazon,250.00,DR\n",
                encoding="utf-8",
            )
            capture = build_capture_from_export(
                "generic-csv", "csv-transactions", path, ACCOUNT,
                adapters_root=ROOT / "browser_adapters", captured_at=self.captured_at,
            )
        self.assertEqual("Amazon", capture["rows"][0]["description"])
        self.assertEqual("DEBIT", capture["rows"][0]["direction"])

    def test_emirates_islamic_xlsx_retains_pending_status_for_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ei.xlsx"
            _write_minimal_xlsx(path, [
                ["Card Number : XXXXXXXXXXXX0082"],
                ["Date", "Details", "Amount", "Currency", "Debit/Credit", "Status"],
                ["Jul 31, 2026", "AMAZON.AE", "75.25", "AED", "Debit", "Pending"],
            ])
            capture = build_capture_from_export(
                "emirates-islamic", "credit-card-transactions", path, ACCOUNT,
                adapters_root=ROOT / "browser_adapters", captured_at=self.captured_at,
            )
        self.assertEqual("0082", capture["rows"][0]["account_last4"])
        self.assertEqual("PENDING", capture["rows"][0]["status"])
        self.assertTrue(capture["rows"][0]["review_required"])

    def test_emirates_islamic_xlsx_rejects_an_unparsed_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ei.xlsx"
            _write_minimal_xlsx(path, [
                ["Date", "Details", "Amount", "Currency", "Debit/Credit", "Status"],
                ["not-a-date", "AMAZON.AE", "75.25", "AED", "Debit", "SETTLED"],
            ])
            with self.assertRaisesRegex(ValueError, "invalid date"):
                build_capture_from_export(
                    "emirates-islamic", "credit-card-transactions", path, ACCOUNT,
                    adapters_root=ROOT / "browser_adapters",
                )

    def test_foreign_export_without_aed_equivalent_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ei.xlsx"
            _write_minimal_xlsx(path, [
                ["Date", "Details", "Amount", "Currency", "Debit/Credit", "Status"],
                ["Jul 31, 2026", "FOREIGN MERCHANT", "25.00", "USD", "Debit", "SETTLED"],
            ])
            with self.assertRaisesRegex(ValueError, "no evidenced AED equivalent"):
                build_capture_from_export(
                    "emirates-islamic", "credit-card-transactions", path, ACCOUNT,
                    adapters_root=ROOT / "browser_adapters",
                )

    def test_email_pdf_routes_to_statement_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wio.pdf"
            path.write_bytes(b"%PDF-1.7 test")
            capture = build_capture_from_export(
                "wio", "credit-statement", path, ACCOUNT,
                adapters_root=ROOT / "browser_adapters", captured_at=self.captured_at,
            )
        self.assertEqual("STATEMENT_PDF", capture["artifact"]["kind"])
        self.assertEqual("OFFICIAL_EXPORT", capture["source"]["capture_method"])


if __name__ == "__main__":
    unittest.main()
