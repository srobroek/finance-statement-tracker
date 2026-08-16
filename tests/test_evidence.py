import unittest
import tempfile
from datetime import datetime, timezone
from decimal import Decimal

from pathlib import Path

from finance_tracker.evidence import (
    EvidenceCandidate,
    archive_evidence,
    best_match,
    document_relative_path,
    evidence_catalogue_record,
    update_evidence_catalogue,
    statement_catalogue_record,
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


if __name__ == "__main__":
    unittest.main()
