from decimal import Decimal
from unittest import TestCase

from finance_tracker.cashback import total_spend
from finance_tracker.ingestion import stage_statement
from finance_tracker.statements import parse_statement_text


class StatementStagingTests(TestCase):
    def setUp(self) -> None:
        self.statement = parse_statement_text(
            """Statement of Card Account
From: 1st Jul 2026
31st Jul 2026
To:
OPENING BALANCE 100.00
PRIMARY CARD NO:5424XXXXXXXX0082
02 JUL 02 JUL TRANSFER PAYMENT RECEIVED THANK YOU 50.00CR
10 JUL 09 JUL EXAMPLE SHOP DUBAI ARE 25.00
Card Limit Available Limit Minimum Payment Due Payment Due Date Total Payment Due Profit/Other Charges (AED) Current Balance (AED)
50,000.00 49,925.00 75.00 25/08/26 75.00 0.00 75.00
""",
            "statement.pdf",
        )

    def test_balanced_statement_stages_without_claiming_reconciliation(self) -> None:
        batch = stage_statement(
            self.statement,
            {"0082": "EI_AMAZON"},
            source_message_id="message-1",
        )
        self.assertEqual(batch.status, "READY_FOR_LEDGER_MATCH")
        self.assertTrue(batch.balance_tied)
        self.assertFalse(batch.ledger_reconciled)
        self.assertEqual(batch.review_count, 0)
        self.assertTrue(all(not row.metadata["ledger_reconciled"] for row in batch.transactions))
        self.assertEqual(total_spend(batch.transactions, "EI_AMAZON"), Decimal("25.00"))

    def test_unknown_card_requires_review(self) -> None:
        batch = stage_statement(self.statement, {})
        self.assertEqual(batch.status, "REVIEW_REQUIRED")
        self.assertEqual(batch.review_count, 2)
        self.assertTrue(all(row.card == "UNMAPPED_CARD" for row in batch.transactions))
