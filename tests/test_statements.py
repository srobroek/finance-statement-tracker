from decimal import Decimal
from unittest import TestCase

from finance_tracker.statements import (
    AdcbStatementAdapter,
    BankStatementAdapter,
    EmiratesIslamicStatementAdapter,
    StatementAdapterRegistry,
    parse_statement_text,
)


class StatementParserTests(TestCase):
    def test_emirates_islamic_statement_reconciles(self) -> None:
        text = """Statement of Card Account
From: 1st Jul 2026
31st Jul 2026
To:
OPENING BALANCE 1,043.29
PRIMARY CARD NO:5424XXXXXXXX0082
02 JUL 02 JUL TRANSFER PAYMENT RECEIVED THANK YOU 1,100.00CR
10 JUL 09 JUL AMAZON.AE DUBAI ARE 93.42
13 JUL 12 JUL AMAZON.AE DUBAI ARE 3.55CR
15 JUL 14 JUL AMAZON.AE DUBAI ARE 2.57CR
17 JUL 16 JUL AMAZON RETAIL DUBAI ARE 58.90
24 JUL 23 JUL AMAZON.AE DUBAI ARE 129.90
25 JUL 24 JUL AMAZON.AE DUBAI ARE 66.31
Card Limit Available Limit Minimum Payment Due Payment Due Date Total Payment Due Profit/Other Charges (AED) Current Balance (AED)
50,000.00 49,714.30 100.00 25/08/26 285.70 0.00 285.70
"""
        statement = parse_statement_text(text, "ei.pdf")
        self.assertEqual(len(statement.transactions), 7)
        self.assertEqual(statement.transactions[0].transaction_type, "PAYMENT")
        self.assertEqual(statement.transactions[2].transaction_type, "REFUND")
        self.assertEqual(statement.calculated_closing_balance_aed, Decimal("285.70"))
        self.assertTrue(statement.balance_tied)
        self.assertEqual(statement.balance_difference_aed, Decimal("0.00"))
        self.assertEqual(statement.payment_due_date.isoformat(), "2026-08-25")

    def test_adcb_statement_parses_card_sections_and_foreign_currency(self) -> None:
        text = """15/07/26
09/08/26
PREVIOUS BALANCE OUTSTANDING 100.00
Card No : XXXXXXXXXXXX8833 - TEST USER
14/06/2026 LOCAL SHOP DUBAI ARE 50.00
18/06/2026 PAYMENT RECEIVED, THANK YOU 25.00 CR
23/06/2026 FOREIGN VENDOR USA 10.00 USD 38.25
[1 USD=AED 3.82500]
10/07/2026 1% Cashback-Other Purchase JUN-26 1.00 CR
Card No : XXXXXXXXXXXX6838 - TEST USER TWO
04/07/2026 APPLE.COM/BILL IRL 12.75
15/07/2026 NEW BALANCE OUTSTANDING 175.00
"""
        statement = parse_statement_text(text, "adcb.pdf")
        self.assertEqual(statement.card_last4s, ("8833", "6838"))
        self.assertEqual(len(statement.transactions), 5)
        self.assertEqual(statement.transactions[2].currency_original, "USD")
        self.assertEqual(statement.transactions[2].amount_original, Decimal("10.00"))
        self.assertEqual(statement.transactions[2].exchange_rate, Decimal("3.82500"))
        self.assertEqual(statement.transactions[3].transaction_type, "REWARD_CREDIT")
        self.assertEqual(statement.calculated_closing_balance_aed, Decimal("175.00"))
        self.assertTrue(statement.balance_tied)

    def test_registry_is_the_bank_extension_boundary(self) -> None:
        adapters = (EmiratesIslamicStatementAdapter(), AdcbStatementAdapter())
        registry = StatementAdapterRegistry(adapters)
        self.assertTrue(all(isinstance(adapter, BankStatementAdapter) for adapter in adapters))
        self.assertEqual(registry.adapter("adcb_v1").bank_name, "ADCB")
        with self.assertRaises(ValueError):
            registry.parse("not a statement")
