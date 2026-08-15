import unittest
from datetime import datetime, timezone
from decimal import Decimal

from finance_tracker.evidence import EvidenceCandidate, best_match, document_relative_path
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


if __name__ == "__main__":
    unittest.main()
