from __future__ import annotations

import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from finance_tracker.cashback_events import CashbackEventStore, events_to_transactions
from finance_tracker.reporting import breakdown
from finance_tracker.reports import month_category_totals
from finance_tracker.transaction_semantics import is_finalized_for_consumption


class TransactionConsumptionIntegrationTests(TestCase):
    def test_reports_exclude_outlook_events_but_include_statement_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.upsert(
                [
                    {
                        "source_event_id": "outlook-message-1:0",
                        "occurred_at": "2026-08-02T12:00:00+04:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "900",
                        "currency": "AED",
                        "purchase_type": "Shopping",
                        "channel": "Online",
                        "merchant": "Notification Shop",
                        "source": "outlook",
                        "status": "ACTIVE",
                        "reconciliation_status": "RECONCILED",
                        "statement_reference": "RAK-2026-08",
                        "confidence": 1,
                        "review_required": False,
                    },
                    {
                        "source_event_id": "statement:RAK-2026-08:line-1",
                        "occurred_at": "2026-08-03T12:00:00+04:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "100",
                        "currency": "AED",
                        "purchase_type": "Groceries",
                        "channel": "Card",
                        "merchant": "Statement Market",
                        "source": "statement",
                        "status": "ACTIVE",
                        "reconciliation_status": "RECONCILED",
                        "statement_reference": "RAK-2026-08",
                        "confidence": 1,
                        "review_required": False,
                    },
                ]
            )
            transactions = events_to_transactions(
                store.rows(date(2026, 8, 1), date(2026, 8, 31)), ()
            )

        by_id = {
            transaction.transaction_id: transaction for transaction in transactions
        }
        notification = by_id["outlook-message-1:0"]
        statement = by_id["statement:RAK-2026-08:line-1"]

        self.assertEqual(notification.source_type, "outlook")
        self.assertEqual(notification.metadata["reconciliation_status"], "RECONCILED")
        self.assertFalse(is_finalized_for_consumption(notification))
        self.assertEqual(statement.source_type, "statement")
        self.assertTrue(is_finalized_for_consumption(statement))
        self.assertEqual(
            [
                (row.key, row.spend_aed)
                for row in breakdown(transactions, dimension="category")
            ],
            [("GROCERIES", Decimal(100))],
        )
        self.assertEqual(
            month_category_totals(transactions, "2026-08"),
            {"GROCERIES": Decimal(100)},
        )
