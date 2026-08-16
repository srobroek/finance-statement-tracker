from datetime import datetime
from decimal import Decimal
from unittest import TestCase

from finance_tracker.models import Transaction
from finance_tracker.platforms import ActualBudgetAdapter, PlatformKind


class ActualBudgetAdapterTests(TestCase):
    def test_groups_transactions_by_account_and_uses_imported_id(self) -> None:
        rows = [
            Transaction(
                transaction_id="bank:one",
                transaction_at=datetime(2026, 8, 16, 9, 30),
                card="RAK",
                account="RAK World",
                merchant_raw="CARREFOUR 123",
                vendor="Carrefour",
                amount_aed=Decimal("12.345"),
                source_type="statement_pdf",
                source_message_id="mail-1",
                tags={"groceries", "shared"},
            ),
            Transaction(
                transaction_id="bank:two",
                transaction_at=datetime(2026, 8, 17, 9, 30),
                card="ADCB",
                account="ADCB TouchPoints",
                merchant_raw="REVERSAL",
                amount_aed=Decimal("4.50"),
                is_refund=True,
            ),
        ]

        envelopes = ActualBudgetAdapter().serialize_import(rows)

        self.assertEqual(ActualBudgetAdapter.kind, PlatformKind.ACTUAL)
        self.assertEqual([item.account for item in envelopes], ["ADCB TouchPoints", "RAK World"])
        purchase = envelopes[1].records[0]
        refund = envelopes[0].records[0]
        self.assertEqual(purchase["amount"], -1235)
        self.assertEqual(purchase["imported_id"], "bank:one")
        self.assertEqual(purchase["payee_name"], "Carrefour")
        self.assertEqual(purchase["imported_payee"], "CARREFOUR 123")
        self.assertIn("#groceries", purchase["notes"])
        self.assertTrue(envelopes[1].default_cleared)
        self.assertFalse(envelopes[0].default_cleared)
        self.assertEqual(refund["amount"], 450)

    def test_statement_rows_are_cleared_and_tags_are_actual_safe(self) -> None:
        row = Transaction(
            transaction_id="statement:one",
            transaction_at=datetime(2026, 8, 16),
            card="ADCB",
            account="ADCB Credit Card",
            merchant_raw="EXAMPLE",
            amount_aed=Decimal("5"),
            source_type="statement_pdf",
            owner="Sjors van der Meer",
            reward_bucket="Online Spend",
            tags={"Shared Household"},
        )

        envelope = ActualBudgetAdapter().serialize_import([row])[0]

        self.assertTrue(envelope.default_cleared)
        self.assertTrue(envelope.records[0]["cleared"])
        self.assertIn("#shared-household", envelope.records[0]["notes"])
        self.assertIn("#owner-sjors-van-der-meer", envelope.records[0]["notes"])
        self.assertIn("#cashback-online-spend", envelope.records[0]["notes"])

    def test_tied_browser_statement_rows_are_cleared_but_portal_rows_are_not(self) -> None:
        common = {
            "transaction_at": datetime(2026, 8, 16),
            "card": "WIO",
            "account": "Wio Current",
            "merchant_raw": "EXAMPLE",
            "amount_aed": Decimal("5"),
        }
        statement = Transaction(
            transaction_id="browser:statement",
            source_type="browser_statement",
            **common,
        )
        portal = Transaction(
            transaction_id="browser:portal",
            source_type="browser_portal",
            **common,
        )

        statement_envelope = ActualBudgetAdapter().serialize_import([statement])[0]
        portal_envelope = ActualBudgetAdapter().serialize_import([portal])[0]

        self.assertTrue(statement_envelope.default_cleared)
        self.assertFalse(portal_envelope.default_cleared)

    def test_requires_an_account_mapping(self) -> None:
        row = Transaction(
            transaction_id="bank:missing-account",
            transaction_at=datetime(2026, 8, 16),
            card="RAK",
            merchant_raw="Example",
            amount_aed=Decimal("1"),
        )

        with self.assertRaisesRegex(ValueError, "destination account"):
            ActualBudgetAdapter().serialize_import([row])
