from datetime import datetime
from decimal import Decimal
from unittest import TestCase

from finance_tracker.models import Transaction
from finance_tracker.notion_io import card_period_properties, transaction_properties


class NotionSerializationTests(TestCase):
    def test_refund_is_negative_for_notion_ledger(self) -> None:
        transaction = Transaction(
            transaction_id="refund-1",
            transaction_at=datetime(2026, 7, 12),
            card="EI_AMAZON",
            merchant_raw="AMAZON.AE",
            amount_aed=Decimal("3.55"),
            source_type="statement",
            source_message_id="message-1",
            vendor="Amazon UAE",
            category="Online Shopping",
            transaction_type="REFUND",
            is_refund=True,
        )
        properties = transaction_properties(transaction)
        self.assertEqual(properties["Amount AED"], -3.55)
        self.assertEqual(properties["Transaction Type"], "REFUND")
        self.assertEqual(properties["Is Refund"], "__YES__")

    def test_payment_maps_to_transfer_and_negative_amount(self) -> None:
        transaction = Transaction(
            transaction_id="payment-1",
            transaction_at=datetime(2026, 7, 2),
            card="EI_AMAZON",
            merchant_raw="PAYMENT RECEIVED",
            amount_aed=Decimal("1100.00"),
            transaction_type="PAYMENT",
        )
        properties = transaction_properties(transaction)
        self.assertEqual(properties["Amount AED"], -1100.0)
        self.assertEqual(properties["Transaction Type"], "TRANSFER")

    def test_card_period_is_received_but_not_reconciled_or_finalized(self) -> None:
        properties = card_period_properties(
            title="EI Amazon · 2026-07",
            card_page_url="https://app.notion.com/card",
            period="2026-07",
            period_start="2026-07-01",
            period_end="2026-07-31",
            statement_date="2026-07-31",
            payment_due_date="2026-08-25",
            actual_spend_aed=Decimal("340.00"),
        )
        self.assertEqual(properties["Statement Status"], "RECEIVED")
        self.assertEqual(properties["Reconciliation Status"], "NOT_STARTED")
        self.assertEqual(properties["Cashback Finalized"], "__NO__")
        self.assertEqual(properties["date:Payment Due Date:start"], "2026-08-25")
