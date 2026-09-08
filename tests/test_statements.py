from datetime import date
from decimal import Decimal
from unittest import TestCase

from finance_tracker.statements import (
    DEFAULT_STATEMENT_ADAPTERS,
    AdcbStatementAdapter,
    BankStatementAdapter,
    EmiratesIslamicStatementAdapter,
    RakbankStatementAdapter,
    StatementAdapterRegistry,
    WioCreditStatementAdapter,
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
        self.assertEqual(statement.transactions[2].transaction_type, "CREDIT")
        self.assertEqual(statement.calculated_closing_balance_aed, Decimal("285.70"))
        self.assertTrue(statement.balance_tied)
        self.assertEqual(statement.balance_difference_aed, Decimal("0.00"))
        self.assertEqual(statement.payment_due_date.isoformat(), "2026-08-25")

    def test_emirates_compact_dates_use_statement_bounds_across_years(self) -> None:
        text = """Statement of Card Account
From: 1st Dec 2025
31st Jan 2026
To:
OPENING BALANCE 100.00
PRIMARY CARD NO:5424XXXXXXXX0082
01 JAN 31 DEC CROSS YEAR PURCHASE 10.00
"""

        statement = parse_statement_text(text, "ei-cross-year.pdf")

        self.assertEqual(
            statement.transactions[0].transaction_date,
            date(2025, 12, 31),
        )
        self.assertEqual(
            statement.transactions[0].post_date,
            date(2026, 1, 1),
        )

    def test_emirates_compact_dates_without_statement_bounds_are_rejected(self) -> None:
        text = """Statement of Card Account
OPENING BALANCE 100.00
PRIMARY CARD NO:5424XXXXXXXX0082
02 JUL 02 JUL UNDATED PURCHASE 10.00
"""

        with self.assertRaisesRegex(ValueError, "statement bounds"):
            parse_statement_text(text, "ei-undated.pdf")

    def test_statement_credits_stay_provisional_until_normalization(self) -> None:
        text = """Statement of Card Account
From: 1st Jul 2026
31st Jul 2026
To:
OPENING BALANCE 100.00
PRIMARY CARD NO:5424XXXXXXXX0082
01 JUL 01 JUL SALARY PAYMENT 2,000.00CR
02 JUL 02 JUL TRANSFER FROM SAVINGS 50.00CR
03 JUL 03 JUL MERCHANT REFUND 25.00CR
"""

        statement = parse_statement_text(text, "ei-credit-types.pdf")

        self.assertEqual(
            [transaction.transaction_type for transaction in statement.transactions],
            ["CREDIT", "CREDIT", "REFUND"],
        )
        self.assertEqual(
            [transaction.direction for transaction in statement.transactions],
            ["CREDIT", "CREDIT", "CREDIT"],
        )
        self.assertEqual(
            [transaction.amount_aed for transaction in statement.transactions],
            [Decimal("2000.00"), Decimal("50.00"), Decimal("25.00")],
        )

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

    def test_adcb_statement_accepts_negative_overpaid_closing_balance(self) -> None:
        text = """15/08/26
15/09/26
PREVIOUS BALANCE OUTSTANDING 100.00
Card No : XXXXXXXXXXXX8833 - TEST USER
14/08/2026 PAYMENT RECEIVED, THANK YOU 150.00 CR
15/08/2026 NEW BALANCE OUTSTANDING -50.00
"""

        statement = parse_statement_text(text, "adcb-overpaid.pdf")

        self.assertEqual(statement.closing_balance_aed, Decimal("-50.00"))
        self.assertEqual(statement.calculated_closing_balance_aed, Decimal("-50.00"))
        self.assertTrue(statement.balance_tied)


    def test_rakbank_statement_preserves_dates_fx_and_printed_summary(self) -> None:
        text = """RAKBANK CREDIT CARD STATEMENT
DATE ISSUED : 31/07/2026 :
STATEMENT PERIOD : 01/07/2026 TO 31/07/2026 :
CARD NUMBER : 1234 XXXX 5678 :
PREVIOUS BALANCE AED 100.00
RETAIL TRANSACTIONS AED 28.25 +
PAYMENTS AND CREDITS AED 23.25 -
CURRENT BALANCE AED 105.00
CREDIT CARD LIMIT
AED 1,000.00
MINIMUM PAYMENT DUE
AED 10.00
TOTAL AMOUNT DUE (TO AVOID INTEREST)
AED 105.00
PAYMENT DUE DATE
15/08/2026
DATE TRANSACTION
DESCRIPTION
TRANSACTION CURRENCY
TRANSACTION AMOUNT
TOTAL AMOUNT (AED)
01/07/2026 LOCAL SHOP, DUBAI, AED 10.00 - 10.00
02/07/2026 FOREIGN MERCHANT, EUROPE, EUR 5.00 18.25
03/07/2026 PAYMENT RECEIVED - AED 23.25 CR - 23.25 CR
"""

        statement = parse_statement_text(text, "rakbank.pdf")

        self.assertIsInstance(
            DEFAULT_STATEMENT_ADAPTERS.adapter("rakbank_v1"),
            RakbankStatementAdapter,
        )
        self.assertEqual(statement.statement_date, date(2026, 7, 31))
        self.assertEqual(statement.period_start, date(2026, 7, 1))
        self.assertEqual(statement.payment_due_date, date(2026, 8, 15))
        self.assertEqual(statement.card_last4s, ("5678",))
        self.assertEqual(len(statement.transactions), 3)
        self.assertEqual(statement.transactions[1].amount_original, Decimal("5.00"))
        self.assertEqual(statement.transactions[1].currency_original, "EUR")
        self.assertEqual(statement.transactions[1].amount_aed, Decimal("18.25"))
        self.assertIsNone(statement.transactions[1].exchange_rate)
        self.assertEqual(statement.transactions[2].transaction_type, "PAYMENT")
        self.assertTrue(statement.balance_tied)
        self.assertEqual(statement.balance_difference_aed, Decimal("0.00"))

        with self.assertRaisesRegex(ValueError, "printed summary does not reconcile"):
            parse_statement_text(
                text.replace("CURRENT BALANCE AED 105.00", "CURRENT BALANCE AED 106.00"),
                "rakbank-bad-summary.pdf",
            )
    def test_registry_is_the_bank_extension_boundary(self) -> None:
        adapters = (EmiratesIslamicStatementAdapter(), AdcbStatementAdapter())
        registry = StatementAdapterRegistry(adapters)
        self.assertTrue(
            all(isinstance(adapter, BankStatementAdapter) for adapter in adapters)
        )
        self.assertEqual(registry.adapter("adcb_v1").bank_name, "ADCB")
        with self.assertRaises(ValueError):
            registry.parse("not a statement")

    def test_wio_credit_statement_parses_signed_transactions_and_account_suffixes(
        self,
    ) -> None:
        text = """CURRENCY MONTHLY INTEREST RATE ANNUAL INTEREST RATE
CREDIT STATEMENT
AED 3.25% 39.00%
FROM 01/04/2026 TO 01/05/2026
CREDIT LIMIT AVAILABLE CREDIT LIMIT TOT. INTEREST AND FEES
10,000.00 50,000.00 0.00
Wio Bank PAYMENT DUE DATE MIN. PAYMENT DUE TOTAL TO PAY
01/05/2026 15.61 312.30
ACCOUNT NUMBER 3342325009
Account summary
Balance From Last Statement 0.00
Purchases +14,312.30
Payments and credits -14,312.30
Closing balance (Total to pay) 0.00
Transactions
Date Ref. Number Description Card Number Amount
04/04/2026 P089884243 ADCB cashback payment -14,000.00
04/04/2026 P965728346 Credit Repayment +14,000.00
26/04/2026 P769104799 Kibsons ****4113 -312.30
Rate: 3.67 (AED/USD)
01/05/2026 P470244091 Credit Repayment Autopay +312.30
© 2026 Wio, PJSC. All Rights Reserved.
"""

        statement = parse_statement_text(text, "wio.pdf")

        self.assertIsInstance(
            DEFAULT_STATEMENT_ADAPTERS.adapter("wio_credit_v1"),
            WioCreditStatementAdapter,
        )
        self.assertEqual(statement.card_last4s, ("5009", "4113"))
        self.assertEqual(len(statement.transactions), 4)
        self.assertEqual(statement.transactions[0].transaction_type, "PURCHASE")
        self.assertEqual(statement.transactions[1].transaction_type, "PAYMENT")
        self.assertEqual(statement.transactions[2].card_last4, "4113")
        self.assertEqual(statement.transactions[2].currency_original, "USD")
        self.assertEqual(statement.transactions[2].exchange_rate, Decimal("3.67"))
        self.assertIsNone(statement.transactions[2].amount_original)
        self.assertTrue(statement.balance_tied)

    def test_wio_credit_statement_accepts_a_negative_overpaid_closing_balance(
        self,
    ) -> None:
        text = """CREDIT STATEMENT
FROM 01/07/2026 TO 01/08/2026
Wio Bank PAYMENT DUE DATE MIN. PAYMENT DUE TOTAL TO PAY
01/08/2026 0.00 0.00
ACCOUNT NUMBER 3342325009
Balance From Last Statement 0.00
Closing balance (Total to pay) -274.40
01/07/2026 P100000001 Example Merchant ****4113 -100.00
01/08/2026 P100000002 Credit Repayment +374.40
"""

        statement = parse_statement_text(text, "wio-overpaid.pdf", "wio_credit_v1")

        self.assertEqual(statement.closing_balance_aed, Decimal("-274.40"))
        self.assertEqual(statement.calculated_closing_balance_aed, Decimal("-274.40"))
        self.assertTrue(statement.balance_tied)

    def test_wio_credit_statement_accepts_legacy_closing_balance_label(self) -> None:
        text = """CREDIT STATEMENT
FROM 01/02/2025 TO 01/03/2025
Wio Bank PAYMENT DUE DATE MIN. PAYMENT DUE TOTAL TO PAY
01/03/2025 600.00 0.00
ACCOUNT NUMBER 3342325009
Balance From Last Statement 0.00
Closing Balance 0.00
01/02/2025 P100000001 Example Merchant ****4113 -100.00
01/03/2025 P100000002 Credit Repayment +100.00
"""

        statement = parse_statement_text(text, "wio-legacy.pdf", "wio_credit_v1")

        self.assertEqual(statement.closing_balance_aed, Decimal("0.00"))
        self.assertEqual(statement.calculated_closing_balance_aed, Decimal("0.00"))
        self.assertTrue(statement.balance_tied)
