from __future__ import annotations

from datetime import date
from decimal import Decimal
import io
import json
from urllib.request import Request
import unittest

from finance_tracker.fx import (
    ESTIMATED,
    POSTED,
    FutureQuoteError,
    FxQuote,
    InvalidQuoteError,
    MASTERCARD_SANDBOX_ENDPOINT,
    MastercardCurrencyRateProvider,
    MissingQuoteError,
    ProviderChallengeError,
    StaleQuoteError,
    convert_to_aed,
)


class _FailingProvider:
    source = "test-provider"

    def __init__(self) -> None:
        self.calls = 0

    def quote(self, **_: object) -> FxQuote:
        self.calls += 1
        raise AssertionError("the provider must not be called")


class _QuoteProvider:
    source = "test-mastercard"

    def __init__(self, quote: FxQuote) -> None:
        self.quote_value = quote
        self.calls: list[dict[str, object]] = []

    def quote(self, **kwargs: object) -> FxQuote:
        self.calls.append(kwargs)
        return self.quote_value


class _Response:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class FxConversionTests(unittest.TestCase):
    transaction_date = date(2026, 9, 7)

    def test_bank_posted_aed_wins_and_keeps_original_without_provider_call(self) -> None:
        provider = _FailingProvider()

        result = convert_to_aed(
            "12.345",
            "USD",
            transaction_date=self.transaction_date,
            bank_posted_aed="44.445",
            bank_posted_source="statement:adcb:row-7",
            provider=provider,
            source="notification:message-7",
        )

        self.assertEqual(result.original_amount, Decimal("12.345"))
        self.assertEqual(result.original_currency, "USD")
        self.assertEqual(result.amount_aed, Decimal("44.45"))
        self.assertEqual(result.status, POSTED)
        self.assertEqual(result.source, "statement:adcb:row-7")
        self.assertEqual(result.bank_posted_aed, Decimal("44.45"))
        self.assertEqual(provider.calls, 0)

    def test_provider_is_asked_for_a_unit_quote_and_rounds_only_output(self) -> None:
        quote = FxQuote("USD", "AED", Decimal("3.67295"), self.transaction_date, "mastercard")
        provider = _QuoteProvider(quote)

        result = convert_to_aed(
            "2.005",
            "USD",
            transaction_date=self.transaction_date,
            provider=provider,
            source="notification:message-8",
        )

        self.assertEqual(result.amount_aed, Decimal("7.36"))
        self.assertEqual(result.status, ESTIMATED)
        self.assertEqual(result.quote, quote)
        self.assertEqual(result.to_dict()["quote"]["rate_date"], "2026-09-07")
        self.assertEqual(provider.calls[0]["from_currency"], "USD")
        self.assertEqual(provider.calls[0]["rate_date"], self.transaction_date)

    def test_replay_uses_recorded_quote_and_does_not_revalue(self) -> None:
        quote = FxQuote("EUR", "AED", Decimal("4.001"), self.transaction_date, "mastercard")
        provider = _FailingProvider()

        result = convert_to_aed(
            "10",
            "EUR",
            transaction_date=self.transaction_date,
            quote=quote,
            provider=provider,
            source="statement:replay:row-1",
        )

        self.assertEqual(result.amount_aed, Decimal("40.01"))
        self.assertIs(result.quote, quote)
        self.assertEqual(provider.calls, 0)

    def test_missing_stale_and_future_quotes_keep_retry_provenance(self) -> None:
        with self.assertRaises(MissingQuoteError) as missing:
            convert_to_aed(
                "10",
                "GBP",
                transaction_date=self.transaction_date,
                source="notification:missing-1",
            )
        self.assertEqual(missing.exception.provenance["original_amount"], "10")
        self.assertEqual(missing.exception.provenance["source"], "notification:missing-1")

        stale = FxQuote("GBP", "AED", Decimal("4"), date(2026, 9, 4), "mastercard")
        with self.assertRaises(StaleQuoteError):
            convert_to_aed(
                "10",
                "GBP",
                transaction_date=self.transaction_date,
                quote=stale,
                source="statement:stale-1",
            )

        future = FxQuote("GBP", "AED", Decimal("4"), date(2026, 9, 8), "mastercard")
        with self.assertRaises(FutureQuoteError):
            convert_to_aed(
                "10",
                "GBP",
                transaction_date=self.transaction_date,
                quote=future,
                source="statement:future-1",
            )

    def test_invalid_quote_is_explicit_and_never_zero(self) -> None:
        invalid = FxQuote("USD", "AED", Decimal("0"), self.transaction_date, "mastercard")

        with self.assertRaises(InvalidQuoteError) as raised:
            convert_to_aed(
                "10",
                "USD",
                transaction_date=self.transaction_date,
                quote=invalid,
                source="statement:invalid-1",
            )

        self.assertTrue(raised.exception.to_dict()["review_required"])
        self.assertNotIn("amount_aed", raised.exception.to_dict())

    def test_default_mastercard_provider_uses_public_unit_calculator(self) -> None:
        captured: list[Request] = []

        def opener(request: Request, **_: object) -> _Response:
            captured.append(request)
            return _Response({
                "data": {
                    "conversionRate": "3.6725",
                    "fxDate": "2026-09-07",
                    "transCurr": "USD",
                    "crdhldBillCurr": "AED",
                },
            })

        quote = MastercardCurrencyRateProvider(opener=opener).quote(
            from_currency="USD",
            to_currency="AED",
            rate_date=self.transaction_date,
        )

        self.assertEqual(quote.source, "mastercard-public-calculator")
        self.assertIn("transaction_amount=1", captured[0].full_url)
        self.assertIn("exchange_date=2026-09-07", captured[0].full_url)
        self.assertNotIn("Authorization", str(captured[0].header_items()))

    def test_explicit_standard_api_mode_uses_documented_unit_request(self) -> None:
        captured: list[Request] = []

        def opener(request: Request, **_: object) -> _Response:
            captured.append(request)
            return _Response({
                "data": {
                    "conversionRate": 3.6725,
                    "fxDate": "2026-09-07",
                    "transCurr": "USD",
                    "crdhldBillCurr": "AED",
                },
                "name": "settlement-conversion-rate",
            })

        provider = MastercardCurrencyRateProvider(
            endpoint_url=MASTERCARD_SANDBOX_ENDPOINT,
            public_calculator=False,
            authorization="OAuth signed",
            opener=opener,
        )
        quote = provider.quote(
            from_currency="USD",
            to_currency="AED",
            rate_date=self.transaction_date,
        )

        self.assertEqual(quote.rate, Decimal("3.6725"))
        self.assertEqual(quote.source, provider.source)
        self.assertEqual(quote.source_url, provider.endpoint_url)
        self.assertEqual(len(captured), 1)
        self.assertIn("transAmt=1", captured[0].full_url)
        self.assertNotIn("merchant", captured[0].full_url)

    def test_mastercard_interactive_challenge_stops(self) -> None:
        class ChallengeResponse:
            status = 200
            headers = {"Content-Type": "text/html"}

            def read(self) -> bytes:
                return io.BytesIO(b"verify you are human").read()

        provider = MastercardCurrencyRateProvider(
            endpoint_url=MASTERCARD_SANDBOX_ENDPOINT,
            public_calculator=False,
            authorization="OAuth signed",
            opener=lambda *_args, **_kwargs: ChallengeResponse(),
        )
        with self.assertRaises(ProviderChallengeError):
            provider.quote(
                from_currency="USD",
                to_currency="AED",
                rate_date=self.transaction_date,
            )
