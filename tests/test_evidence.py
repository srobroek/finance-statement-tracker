import unittest
import tempfile
from datetime import datetime, timezone
from decimal import Decimal

from pathlib import Path

from finance_tracker.evidence import (
    ArchivedEvidence,
    EvidenceCandidate,
    archive_evidence,
    archive_statement_evidence,
    best_match,
    best_group_match,
    document_relative_path,
    evidence_catalogue_record,
    evidence_group_catalogue_record,
    render_outlook_evidence_snapshot,
    update_evidence_catalogue,
    statement_catalogue_record,
    statement_relative_path,
)
from finance_tracker.models import Transaction


class EvidenceTests(unittest.TestCase):
    def test_utility_pdf_matches_and_gets_structured_path(self):
        transaction = Transaction(
            "tx-dewa",
            datetime(2026, 8, 10, tzinfo=timezone.utc),
            "SC_PLATINUM_X",
            "DEWA DUBAI",
            Decimal("842.35"),
            vendor="DEWA",
            category="Utilities",
        )
        candidate = EvidenceCandidate(
            "mail-1",
            datetime(2026, 8, 11, tzinfo=timezone.utc),
            "Your DEWA bill and statement",
            vendor="DEWA",
            amount_aed=Decimal("842.35"),
            attachment_name="bill.pdf",
            document_type="bill",
        )
        match = best_match(transaction, [candidate])
        self.assertIsNotNone(match)
        self.assertGreaterEqual(match.score, Decimal("0.80"))
        path = document_relative_path(transaction, "bill", "account 123")
        self.assertEqual(path.parts[:4], ("Finance Evidence", "2026", "08", "dewa"))
        self.assertTrue(str(path).endswith(".pdf"))

    def test_archive_filename_and_catalogue_label_aed_equivalent_correctly(self):
        transaction = Transaction(
            "tx-foreign",
            datetime(2026, 7, 4, tzinfo=timezone.utc),
            "WIO_CREDIT",
            "Smarthoteloslo",
            Decimal("829.57"),
            currency="NOK",
            amount_original=Decimal("2211.37"),
            vendor="SmartHotel Oslo",
        )
        path = document_relative_path(transaction, "receipt", "hotel-payment")
        self.assertIn("__aed-829.57__", path.name)
        archived = ArchivedEvidence(
            transaction.transaction_id,
            "receipt",
            path.as_posix(),
            "b" * 64,
            10,
        )
        record = evidence_catalogue_record(archived, transaction, reference="hotel-payment")
        self.assertEqual(record["entity_type"], "TRANSACTION")
        self.assertEqual(record["entity_id"], "tx-foreign")
        self.assertEqual(record["currency"], "AED")
        self.assertEqual(record["original_currency"], "NOK")
        self.assertEqual(record["amount_original"], "2211.37")

    def test_low_confidence_candidate_is_not_linked(self):
        transaction = Transaction(
            "tx-1",
            datetime(2026, 8, 10, tzinfo=timezone.utc),
            "RAK_WORLD",
            "UNKNOWN SHOP",
            Decimal("100"),
        )
        candidate = EvidenceCandidate(
            "mail-2",
            datetime(2026, 6, 1, tzinfo=timezone.utc),
            "Newsletter",
            vendor="Different Vendor",
            amount_aed=Decimal("999"),
        )
        self.assertIsNone(best_match(transaction, [candidate]))

    def test_explicit_currency_mismatch_is_rejected(self):
        transaction = Transaction(
            "tx-fx",
            datetime(2026, 8, 10, tzinfo=timezone.utc),
            "SC_PLATINUM_X",
            "EXAMPLE",
            Decimal("100"),
            currency="USD",
        )
        candidate = EvidenceCandidate(
            "mail-fx",
            datetime(2026, 8, 10, tzinfo=timezone.utc),
            "Example receipt",
            vendor="Example",
            amount_aed=Decimal("100"),
            currency="AED",
        )

        self.assertIsNone(best_match(transaction, [candidate], Decimal("0.01")))

    def test_foreign_receipt_matches_on_vendor_date_and_card_when_original_amount_is_unavailable(self):
        transaction = Transaction(
            "tx-hotel",
            datetime(2026, 7, 4, tzinfo=timezone.utc),
            "WIO_CREDIT",
            "Smarthoteloslo",
            Decimal("829.57"),
            vendor="SmartHotel Oslo",
            account_last4="4113",
            currency="NOK",
            metadata={"statement_exchange_rate": "0.37", "statement_card_last4": "4113"},
        )
        candidate = EvidenceCandidate(
            "mail-hotel",
            datetime(2026, 7, 4, tzinfo=timezone.utc),
            "Your receipt from Smarthotel Oslo",
            vendor="SmartHotel Oslo",
            amount_original=Decimal("2211.37"),
            currency_original="NOK",
            account_reference="4113",
            document_type="receipt",
        )

        match = best_match(transaction, [candidate])

        self.assertIsNotNone(match)
        self.assertGreaterEqual(match.strong_fact_count, 3)
        self.assertIn("account_exact", match.reasons)

    def test_grouped_foreign_booking_matches_aggregate_statement_rows(self):
        rows = [
            Transaction(
                f"sas-{index}",
                datetime(2026, 7, 4, tzinfo=timezone.utc),
                "WIO_CREDIT",
                "Sas",
                amount,
                vendor="SAS",
                account_last4="4113",
                currency="EUR",
                metadata={"statement_exchange_rate": rate, "statement_card_last4": "4113"},
            )
            for index, (amount, rate) in enumerate(
                (
                    (Decimal("29.70"), "4.24"),
                    (Decimal("29.70"), "4.21"),
                    (Decimal("29.70"), "4.21"),
                    (Decimal("29.70"), "4.21"),
                    (Decimal("2272.65"), "4.21"),
                    (Decimal("2272.65"), "4.21"),
                )
            )
        ]
        candidate = EvidenceCandidate(
            "mail-sas",
            datetime(2026, 7, 4, tzinfo=timezone.utc),
            "Your SAS booking is confirmed",
            vendor="SAS",
            amount_original=Decimal("1099.20"),
            currency_original="EUR",
            order_reference="YYM26Y",
            document_type="booking-confirmation",
        )

        match = best_group_match(rows, [candidate])

        self.assertIsNotNone(match)
        self.assertIn("aggregate_original_amount_near", match.reasons)
        self.assertGreaterEqual(match.strong_fact_count, 3)

    def test_archive_is_structured_hashed_and_idempotent(self):
        transaction = Transaction(
            "tx-receipt",
            datetime(2026, 8, 10, tzinfo=timezone.utc),
            "EI_AMAZON",
            "AMAZON.AE",
            Decimal("99.95"),
            vendor="Amazon",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "receipt.pdf"
            source.write_bytes(b"synthetic receipt")

            first = archive_evidence(
                source,
                root / "archive",
                transaction,
                "receipt",
                "order 123",
                message_id="mail-1",
                attachment_id="attachment-1",
            )
            second = archive_evidence(
                source,
                root / "archive",
                transaction,
                "receipt",
                "order 123",
                message_id="mail-1",
                attachment_id="attachment-1",
            )

            self.assertEqual(first, second)
            self.assertTrue((root / "archive" / Path(first.relative_path)).is_file())
            self.assertIn("Finance Evidence/2026/08/amazon/", first.relative_path)

            record = evidence_catalogue_record(first, transaction, reference="order 123")
            catalogue = root / "archive" / "catalogue.json"
            self.assertEqual(update_evidence_catalogue(catalogue, record), {"inserted": 1, "updated": 0})
            self.assertEqual(update_evidence_catalogue(catalogue, record), {"inserted": 0, "updated": 1})
            self.assertEqual(len(__import__("json").loads(catalogue.read_text(encoding="utf-8"))), 1)

    def test_outlook_snapshot_strips_urls_and_redacts_authentication_values(self):
        message = {
            "id": "mail-1",
            "subject": "Booking confirmation",
            "sender": {"emailAddress": {"address": "merchant@example.com"}},
            "receivedDateTime": "2026-07-04T08:29:30Z",
            "web_link": "https://outlook.example/item/1",
            "body": {
                "content": (
                    "PIN code: 1745\nTotal price NOK 2,211.37\n"
                    "[Manage booking](https://merchant.example/manage?auth_key=secret)"
                )
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = render_outlook_evidence_snapshot(message, Path(temporary) / "snapshot.txt")
            rendered = output.read_text(encoding="utf-8")
        self.assertIn("Total price NOK 2,211.37", rendered)
        self.assertIn("PIN code: [REDACTED]", rendered)
        self.assertNotIn("1745", rendered)
        self.assertNotIn("auth_key", rendered)

    def test_group_catalogue_links_one_document_to_each_transaction(self):
        rows = tuple(
            Transaction(
                f"tx-{index}",
                datetime(2026, 7, 4, tzinfo=timezone.utc),
                "WIO_CREDIT",
                "Sas",
                Decimal("100"),
                account="Wio Credit",
                vendor="SAS",
            )
            for index in range(2)
        )
        archived = ArchivedEvidence(
            "transaction-group:YYM26Y",
            "booking-confirmation",
            "Finance Evidence/2026/07/sas/example.txt",
            "a" * 64,
            10,
            message_id="mail-sas",
        )
        record = evidence_group_catalogue_record(archived, rows, reference="YYM26Y")
        self.assertEqual(record["entity_type"], "TRANSACTION_GROUP")
        self.assertEqual(record["transaction_ids"], ["tx-0", "tx-1"])
        self.assertEqual(record["amount_aed"], "200")

    def test_statement_catalogue_uses_card_period_as_its_entity(self):
        with tempfile.TemporaryDirectory() as temporary:
            statement = Path(temporary) / "statement.pdf"
            statement.write_bytes(b"statement evidence")
            record = statement_catalogue_record(
                statement,
                bank="Example Bank",
                card_code="CARD_1",
                statement_date="2026-08-31",
                period_start="2026-08-01",
                period_end="2026-08-31",
                reference="card-1-aug-2026",
                closing_balance_aed="100.00",
                payment_due_date="2026-09-25",
            )
            self.assertEqual(record["entity_type"], "CARD_PERIOD")
            self.assertEqual(record["entity_id"], "CARD_1:2026-08-01:2026-08-31")
            catalogue = Path(temporary) / "catalogue.json"
            self.assertEqual(update_evidence_catalogue(catalogue, record)["inserted"], 1)

    def test_statement_archive_is_hashed_structured_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "original.pdf"
            source.write_bytes(b"real statement bytes")
            kwargs = {
                "statement_date": "2026-08-01",
                "bank": "Wio",
                "closing_balance_aed": "-274.40",
                "reference": "account 5009 jul 2026",
            }

            first = archive_statement_evidence(source, root / "archive", **kwargs)
            second = archive_statement_evidence(source, root / "archive", **kwargs)

            self.assertEqual(first, second)
            self.assertEqual(len(first.sha256), 64)
            self.assertTrue((root / "archive" / Path(first.relative_path)).is_file())
            self.assertIn(
                "Finance Evidence/2026/08/wio/2026-08-01__statement__wio__aed-274.40__",
                first.relative_path,
            )
            expected = statement_relative_path(content_digest=first.sha256, **kwargs)
            self.assertEqual(first.relative_path, expected.as_posix())


if __name__ == "__main__":
    unittest.main()
