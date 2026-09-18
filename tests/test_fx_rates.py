from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
import json
from pathlib import Path
import unittest
from urllib.request import Request
from finance_tracker.fx_rates import (
    AdcbRateProvider,
    FabRateProvider,
    FxQuote,
    FutureQuoteError,
    MalformedQuoteError,
    MissingCurrencyError,
    NoUsableQuoteError,
    ProviderChallengeError,
    ProviderUnavailableError,
    RakbankRateProvider,
    StaleQuoteError,
    convert_to_aed,
    load_fx_sources,
    resolve_quote,
)
from jsonschema import Draft202012Validator, FormatChecker


class _Response:
    def __init__(self, payload: str | bytes, *, status: int = 200) -> None:
        self.payload = payload.encode("utf-8") if isinstance(payload, str) else payload
        self.status = status

    def read(self) -> bytes:
        return self.payload


class _Provider:
    source = "test"

    def __init__(self, result: FxQuote | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def quote(self, **kwargs: object) -> FxQuote:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FxRateTests(unittest.TestCase):
    as_of = date(2026, 9, 13)
    retrieved = datetime(2026, 9, 13, 12, tzinfo=UTC)

    def test_rakbank_post_success_preserves_amount_and_public_provenance(self) -> None:
        requests: list[Request] = []

        def opener(request: Request, **_: object) -> _Response:
            requests.append(request)
            return _Response(
                json.dumps(
                    {
                        "responseContent": json.dumps(
                            {
                                "fileDate": "09/13/2026 03:01:21 PM",
                                "forexRates": [
                                    {
                                        "fixedCurrencyCode": "USD",
                                        "sellRate": "3.69200",
                                    }
                                ],
                            }
                        )
                    }
                )
            )

        provider = RakbankRateProvider(opener=opener, clock=lambda: self.retrieved)
        result = convert_to_aed(
            "12.345",
            "USD",
            as_of=self.as_of,
            providers=(provider,),
        )

        self.assertEqual(result.amount_aed, Decimal("45.58"))
        self.assertEqual(result.original_amount, Decimal("12.345"))
        self.assertEqual(result.original_currency, "USD")
        self.assertIsNotNone(result.quote)
        quote = result.quote
        self.assertEqual(quote.provider, "RAKBANK_PUBLIC_SELL")
        self.assertEqual(quote.original_amount, Decimal("12.345"))
        self.assertEqual(quote.quote_basis, "BASE_PER_QUOTE")
        self.assertEqual(quote.sell_basis, "DISPLAYED_SELL")
        self.assertEqual(quote.uncertainty, "ESTIMATE")
        self.assertEqual(quote.rate_date, self.as_of)
        self.assertEqual(requests[0].method, "POST")
        self.assertIsNotNone(requests[0].data)
        self.assertEqual(
            json.loads(requests[0].data),
            {},
        )

    def test_rakbank_malformed_date_is_typed_error(self) -> None:
        def provider_for(inner: dict[str, object]) -> RakbankRateProvider:
            return RakbankRateProvider(
                opener=lambda *_args, **_kwargs: _Response(
                    json.dumps({"responseContent": json.dumps(inner)})
                ),
                clock=lambda: self.retrieved,
            )

        with self.assertRaises(MalformedQuoteError):
            provider_for(
                {
                    "fileDate": "09/31/2026 03:01:21 PM",
                    "forexRates": [{"fixedCurrencyCode": "USD", "sellRate": "3.69"}],
                }
            ).quote(currency="USD", as_of=self.as_of)

    def test_rakbank_invalid_and_non_positive_sell_rates_are_typed_errors(
        self,
    ) -> None:
        for sell_rate in ("not-a-number", "0", "-0.01"):
            with self.subTest(sell_rate=sell_rate):
                provider = RakbankRateProvider(
                    opener=lambda *_args, sell_rate=sell_rate, **_kwargs: _Response(
                        json.dumps(
                            {
                                "responseContent": json.dumps(
                                    {
                                        "fileDate": "09/13/2026 03:01:21 PM",
                                        "forexRates": [
                                            {
                                                "fixedCurrencyCode": "USD",
                                                "sellRate": sell_rate,
                                            }
                                        ],
                                    }
                                )
                            }
                        )
                    ),
                    clock=lambda: self.retrieved,
                )

                with self.assertRaises(MalformedQuoteError) as raised:
                    provider.quote(currency="USD", as_of=self.as_of)
                self.assertEqual(raised.exception.provider, provider.source)
                self.assertEqual(raised.exception.currency, "USD")

    def test_rakbank_malformed_response_content_mapping_is_typed(self) -> None:
        provider = RakbankRateProvider(
            opener=lambda *_args, **_kwargs: _Response(
                json.dumps({"responseContent": []})
            ),
            clock=lambda: self.retrieved,
        )

        with self.assertRaises(MalformedQuoteError) as raised:
            provider.quote(currency="USD", as_of=self.as_of)
        self.assertEqual(raised.exception.provider, provider.source)
        self.assertEqual(raised.exception.currency, "USD")

    def test_malformed_quote_mapping_is_typed(self) -> None:
        with self.assertRaises(MalformedQuoteError) as raised:
            convert_to_aed(
                "10",
                "USD",
                as_of=self.as_of,
                quote={"rate": "not-a-number", "rate_date": "2026-09-13"},
            )
        self.assertEqual(raised.exception.provider, "unknown")
        self.assertEqual(raised.exception.currency, "USD")

    def test_rakbank_missing_sell_rate_is_typed_error(self) -> None:
        provider = RakbankRateProvider(
            opener=lambda *_args, **_kwargs: _Response(
                json.dumps(
                    {
                        "responseContent": json.dumps(
                            {
                                "fileDate": "09/13/2026 03:01:21 PM",
                                "forexRates": [{"fixedCurrencyCode": "USD"}],
                            }
                        )
                    }
                )
            ),
            clock=lambda: self.retrieved,
        )

        with self.assertRaises(MalformedQuoteError) as raised:
            provider.quote(currency="USD", as_of=self.as_of)
        self.assertEqual(raised.exception.provider, provider.source)
        self.assertEqual(raised.exception.currency, "USD")
        self.assertIn("sellRate", str(raised.exception))

    def test_rakbank_rejects_unwrapped_generic_rate_shape(self) -> None:
        provider = RakbankRateProvider(
            opener=lambda *_args, **_kwargs: _Response(
                json.dumps(
                    {
                        "rates": [
                            {
                                "currency": "USD",
                                "rate": "3.69200",
                                "date": "2026-09-13",
                            }
                        ]
                    }
                )
            ),
            clock=lambda: self.retrieved,
        )

        with self.assertRaises(MalformedQuoteError) as raised:
            provider.quote(currency="USD", as_of=self.as_of)
        self.assertEqual(raised.exception.provider, provider.source)
        self.assertEqual(raised.exception.currency, "USD")
        self.assertIn("responseContent", str(raised.exception))

    def test_rakbank_rejects_row_without_fixed_currency_code(self) -> None:
        provider = RakbankRateProvider(
            opener=lambda *_args, **_kwargs: _Response(
                json.dumps(
                    {
                        "responseContent": json.dumps(
                            {
                                "fileDate": "09/13/2026 03:01:21 PM",
                                "forexRates": [{"sellRate": "3.69200"}],
                            }
                        )
                    }
                )
            ),
            clock=lambda: self.retrieved,
        )

        with self.assertRaises(MalformedQuoteError) as raised:
            provider.quote(currency="USD", as_of=self.as_of)
        self.assertIn("fixedCurrencyCode", str(raised.exception))

    def test_rakbank_selects_requested_currency_from_multiple_rows(self) -> None:
        provider = RakbankRateProvider(
            opener=lambda *_args, **_kwargs: _Response(
                json.dumps(
                    {
                        "responseContent": json.dumps(
                            {
                                "fileDate": "09/13/2026 03:01:21 PM",
                                "forexRates": [
                                    {
                                        "fixedCurrencyCode": "EUR",
                                        "sellRate": "not-a-number",
                                    },
                                    {
                                        "fixedCurrencyCode": "USD",
                                        "sellRate": "3.69200",
                                    },
                                ],
                            }
                        )
                    }
                )
            ),
            clock=lambda: self.retrieved,
        )

        quote = provider.quote(currency="USD", as_of=self.as_of)

        self.assertEqual(quote.from_currency, "USD")
        self.assertEqual(quote.to_currency, "AED")
        self.assertEqual(quote.rate, Decimal("3.69200"))

    def test_adcb_unqualified_html_table_is_rejected(self) -> None:
        html = """
        <div>As of Date 13-09-2026</div>
        <table><tr><th>Currency</th><th>Sell Rate</th></tr>
        <tr><td>USD</td><td>3.69200</td></tr></table>
        """
        provider = AdcbRateProvider(
            opener=lambda *_args, **_kwargs: _Response(html),
            clock=lambda: self.retrieved,
        )

        with self.assertRaises(MalformedQuoteError) as raised:
            provider.quote(currency="USD", as_of=self.as_of)
        self.assertIn("main/ASP", str(raised.exception))

    def test_adcb_segment_array_requires_explicit_sell_and_currency_fields(
        self,
    ) -> None:
        html = """
        <div>As of Date 13-09-2026</div>
        <script>var ASP_JsonD = [{"rate":"3.69200"}];</script>
        """
        provider = AdcbRateProvider(
            opener=lambda *_args, **_kwargs: _Response(html),
            clock=lambda: self.retrieved,
        )

        with self.assertRaises(MalformedQuoteError) as raised:
            provider.quote(currency="USD", as_of=self.as_of)
        self.assertEqual(raised.exception.provider, provider.source)
        self.assertEqual(raised.exception.currency, "USD")

    def test_adcb_premium_html_table_is_rejected(self) -> None:
        html = """
        <div>As of Date 13-09-2026</div>
        <table id="PCL_JsonD"><tr><th>Currency</th><th>Sell Rate</th></tr>
        <tr><td>USD</td><td>3.68495</td></tr></table>
        """
        provider = AdcbRateProvider(
            opener=lambda *_args, **_kwargs: _Response(html),
            clock=lambda: self.retrieved,
        )

        with self.assertRaises(MalformedQuoteError):
            provider.quote(currency="USD", as_of=self.as_of)

    def test_adcb_qualified_html_table_is_accepted(self) -> None:
        html = """
        <div>As of Date 13-09-2026</div>
        <table data-segment="ASP_JsonD">
          <tr><th>Currency</th><th>Sell Rate</th></tr>
          <tr><td>USD</td><td>3.69200</td></tr>
        </table>
        """
        provider = AdcbRateProvider(
            opener=lambda *_args, **_kwargs: _Response(html),
            clock=lambda: self.retrieved,
        )

        quote = provider.quote(currency="USD", as_of=self.as_of)
        self.assertEqual(quote.rate, Decimal("3.69200"))
        self.assertEqual(quote.reference, "ADCB main/ASP sell table")

    def test_adcb_fallback_selects_main_asp_not_premium_duplicate(self) -> None:
        rak = _Provider(ProviderUnavailableError("issuer unavailable", provider="RAK"))
        html = """
        <html><body>As of Date 13-09-2026
        <script>
        var ASP_JsonD = [{"FromCurrency":"AED","ToCurrency":"USD","SellRate":"3.69200","RateDate":"2026-09-13"}];
        var PCL_JsonD = [{"FromCurrency":"AED","ToCurrency":"USD","SellRate":"3.68495","RateDate":"2026-09-13"}];
        </script></body></html>
        """
        adcb = AdcbRateProvider(
            opener=lambda *_args, **_kwargs: _Response(html),
            clock=lambda: self.retrieved,
        )

        quote = resolve_quote(
            "USD",
            original_amount="10",
            as_of=self.as_of,
            providers=(rak, adcb),
        )

        self.assertEqual(quote.rate, Decimal("3.69200"))
        self.assertEqual(quote.reference, "ADCB main/ASP sell table")
        self.assertEqual(quote.rate_date, self.as_of)

    def test_adcb_retrieved_at_cannot_refresh_stale_quote_date(self) -> None:
        html = (
            "<script>var ASP_JsonD = "
            + json.dumps(
                [
                    {
                        "FromCurrency": "AED",
                        "ToCurrency": "USD",
                        "SellRate": "3.69200",
                        "quoteDate": "2026-09-01",
                        "retrievedAt": "2026-09-13T11:59:00Z",
                    }
                ]
            )
            + ";</script>"
        )
        provider = AdcbRateProvider(
            opener=lambda *_args, **_kwargs: _Response(html),
            clock=lambda: datetime(2026, 9, 13, 12, tzinfo=UTC),
        )

        quote = provider.quote(
            currency="USD",
            as_of=datetime(2026, 9, 13, 12, tzinfo=UTC),
        )
        self.assertIsNone(quote.published_at)
        self.assertEqual(
            quote.retrieved_at,
            datetime(2026, 9, 13, 12, tzinfo=UTC),
        )

        with self.assertRaises(NoUsableQuoteError) as raised:
            resolve_quote(
                "USD",
                as_of=datetime(2026, 9, 13, 12, tzinfo=UTC),
                providers=(provider,),
                max_age_seconds=86_400,
            )
        self.assertEqual(len(raised.exception.errors), 1)
        self.assertIsInstance(raised.exception.errors[0], StaleQuoteError)

    def test_adcb_same_day_publication_is_valid_and_retrieval_is_local_fetch_time(
        self,
    ) -> None:
        html = (
            "<script>var ASP_JsonD = "
            + json.dumps(
                [
                    {
                        "FromCurrency": "AED",
                        "ToCurrency": "USD",
                        "SellRate": "3.69200",
                        "quoteDate": "2026-09-13",
                        "publishedAt": "2026-09-13T11:59:30Z",
                        "retrievedAt": "2026-09-13T00:01:00Z",
                    }
                ]
            )
            + ";</script>"
        )
        local_fetch_time = datetime(2026, 9, 13, 12, 0, 1, tzinfo=UTC)
        provider = AdcbRateProvider(
            opener=lambda *_args, **_kwargs: _Response(html),
            clock=lambda: local_fetch_time,
        )

        quote = resolve_quote(
            "USD",
            as_of=datetime(2026, 9, 13, 12, tzinfo=UTC),
            providers=(provider,),
            max_age_seconds=60,
        )

        self.assertEqual(
            quote.published_at,
            datetime(2026, 9, 13, 11, 59, 30, tzinfo=UTC),
        )
        self.assertEqual(quote.retrieved_at, local_fetch_time)

    def test_freshness_accepts_precise_publication_across_midnight(self) -> None:
        as_of = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
        quote = FxQuote(
            "GBP",
            "AED",
            Decimal("4"),
            date(2026, 9, 13),
            "test",
            published_at=datetime(2026, 9, 13, 23, 59, 30, tzinfo=UTC),
        )

        result = convert_to_aed(
            "10",
            "GBP",
            as_of=as_of,
            quote=quote,
            max_age_seconds=60,
        )

        self.assertEqual(result.amount_aed, Decimal("40.00"))
        self.assertIsNotNone(result.quote)
        assert result.quote is not None
        self.assertEqual(result.quote.published_at, quote.published_at)

    def test_publication_cannot_bypass_contradictory_quote_date(self) -> None:
        as_of = datetime(2026, 9, 13, 12, tzinfo=UTC)
        publication = datetime(2026, 9, 13, 11, 59, 30, tzinfo=UTC)
        for rate_date, expected_error in (
            (date(2026, 9, 1), StaleQuoteError),
            (date(2026, 9, 14), FutureQuoteError),
        ):
            with self.subTest(rate_date=rate_date):
                provider = _Provider(
                    FxQuote(
                        "USD",
                        "AED",
                        Decimal("3.69200"),
                        rate_date,
                        "test",
                        published_at=publication,
                    )
                )
                with self.assertRaises(NoUsableQuoteError) as raised:
                    resolve_quote(
                        "USD",
                        as_of=as_of,
                        providers=(provider,),
                    )
                self.assertEqual(len(raised.exception.errors), 1)
                self.assertIsInstance(raised.exception.errors[0], expected_error)
                fallback = _Provider(
                    FxQuote(
                        "USD",
                        "AED",
                        Decimal("3.69300"),
                        date(2026, 9, 13),
                        "fallback",
                    )
                )
                resolved = resolve_quote(
                    "USD",
                    as_of=as_of,
                    providers=(provider, fallback),
                )
                self.assertEqual(resolved.provider, "fallback")
                self.assertEqual(len(fallback.calls), 1)

    def test_adcb_html_missing_currency_is_explicit(self) -> None:
        html = """<div>As of Date 13-09-2026</div>
        <script>var ASP_JsonD = [{"FromCurrency":"AED","ToCurrency":"EUR","SellRate":"4.41274"}];</script>"""
        provider = AdcbRateProvider(opener=lambda *_args, **_kwargs: _Response(html))

        with self.assertRaises(MissingCurrencyError):
            provider.quote(currency="USD", as_of=self.as_of)

    def test_malformed_stale_future_and_unavailable_quotes_fail_without_zero(
        self,
    ) -> None:
        malformed = _Provider(MalformedQuoteError("bad", provider="malformed"))
        unavailable = _Provider(ProviderUnavailableError("down", provider="down"))
        with self.assertRaises(NoUsableQuoteError) as raised:
            resolve_quote("GBP", as_of=self.as_of, providers=(malformed, unavailable))
        self.assertIsNone(raised.exception.success_cursor)
        self.assertEqual(len(raised.exception.errors), 2)

        stale = FxQuote("GBP", "AED", Decimal("4"), date(2026, 9, 1), "test")
        with self.assertRaises(StaleQuoteError):
            convert_to_aed(
                "10", "GBP", as_of=self.as_of, quote=stale, max_age_seconds=86_400
            )
        future = FxQuote("GBP", "AED", Decimal("4"), date(2026, 9, 14), "test")
        with self.assertRaises(FutureQuoteError):
            convert_to_aed("10", "GBP", as_of=self.as_of, quote=future)

    def test_freshness_rejects_stale_same_day_publication_timestamp(self) -> None:
        as_of = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
        quote = FxQuote(
            "GBP",
            "AED",
            Decimal("4"),
            as_of.date(),
            "test",
            published_at=datetime(2026, 9, 13, 11, 58, 30, tzinfo=UTC),
        )

        with self.assertRaises(StaleQuoteError):
            convert_to_aed(
                "10",
                "GBP",
                as_of=as_of,
                quote=quote,
                max_age_seconds=60,
            )

    def test_freshness_rejects_future_same_day_publication_timestamp(self) -> None:
        as_of = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
        quote = FxQuote(
            "GBP",
            "AED",
            Decimal("4"),
            as_of.date(),
            "test",
            published_at=datetime(2026, 9, 13, 12, 0, 1, tzinfo=UTC),
        )

        with self.assertRaises(FutureQuoteError):
            convert_to_aed("10", "GBP", as_of=as_of, quote=quote)

    def test_optional_fab_uses_latest_published_date_as_estimate(self) -> None:
        html = """
        <div>Last updated date: 11 September, 2026 05:46:58 pm</div>
        <table><tr><th>Currency</th><th>Selling</th></tr>
        <tr><td>USD</td><td>3.693</td></tr></table>
        """
        provider = FabRateProvider(
            opener=lambda *_args, **_kwargs: _Response(html),
            clock=lambda: self.retrieved,
        )
        quote = provider.quote(currency="USD", as_of=date(2026, 9, 11))
        self.assertEqual(quote.rate, Decimal("3.693"))
        self.assertEqual(quote.rate_date, date(2026, 9, 11))
        self.assertEqual(quote.uncertainty, "ESTIMATE")

    def test_provider_challenge_stops_chain_instead_of_bypassing_it(self) -> None:
        challenged = _Provider(ProviderChallengeError("captcha", provider="issuer"))
        fallback = _Provider(
            FxQuote("USD", "AED", Decimal("3.69"), self.as_of, "fallback")
        )
        with self.assertRaises(ProviderChallengeError):
            resolve_quote("USD", as_of=self.as_of, providers=(challenged, fallback))
        self.assertEqual(fallback.calls, [])

    def test_schema_and_source_registry_are_versioned_and_credential_free(self) -> None:
        config = load_fx_sources(Path("config/fx-sources.json"))
        self.assertEqual(config["provider_order"], ["rakbank", "adcb"])
        self.assertEqual(config["optional_fallbacks"], ["fab"])
        adcb = next(
            provider for provider in config["providers"] if provider["id"] == "adcb"
        )
        self.assertIn(adcb["response"].get("units"), (None, "UNKNOWN"))
        self.assertIn(adcb["response"].get("direction_status"), (None, "UNKNOWN"))
        self.assertFalse(
            any("SC" in str(item) or "EI" in str(item) for item in config["providers"])
        )
        schema = json.loads(Path("config/fx-snapshot-schema-v1.json").read_text())
        snapshot = {
            "schema_version": 1,
            "snapshot_id": "quote-1",
            "provider": "RAKBANK_PUBLIC_SELL",
            "base_currency": "AED",
            "quote_currency": "USD",
            "observed_at": "2026-09-13T12:00:00Z",
            "quote_date": "2026-09-13",
            "quote_basis": "BASE_PER_QUOTE",
            "rate": "3.69200",
            "precision": 5,
            "max_age_seconds": 86400,
            "source_identity": "rakbank-public-forex-rate-v1",
            "uncertainty": "ESTIMATE",
            "original_amount": "12.345",
            "original_currency": "USD",
            "published_at": "2026-09-13T00:00:00Z",
            "retrieved_at": "2026-09-13T12:00:00Z",
            "units": "AED per 1 foreign-currency unit",
            "sell_basis": "DISPLAYED_SELL",
        }
        self.assertEqual(
            list(
                Draft202012Validator(
                    schema, format_checker=FormatChecker()
                ).iter_errors(snapshot)
            ),
            [],
        )

    def test_conversion_trace_replays_frozen_quote_without_provider(self) -> None:
        provider = _Provider(
            FxQuote("USD", "AED", Decimal("3.692"), self.as_of, "fixture")
        )
        result = convert_to_aed(
            "12.345",
            "USD",
            as_of=self.as_of,
            providers=(provider,),
        )
        trace = result.to_trace()
        replay_provider = _Provider(
            ProviderUnavailableError("must not be called", provider="replay")
        )
        replay = convert_to_aed(
            trace["original_amount"],
            trace["original_currency"],
            as_of=self.as_of,
            quote=trace["quote"],
            providers=(replay_provider,),
        )
        self.assertEqual(trace["trace_type"], "FX_CONVERSION")
        self.assertEqual(replay.amount_aed, result.amount_aed)
        self.assertEqual(replay.to_trace(), trace)
        self.assertEqual(replay_provider.calls, [])

    def test_aed_passthrough_rounds_output_without_provider(self) -> None:
        result = convert_to_aed("12.345", "AED", as_of=self.as_of, providers=())
        self.assertEqual(result.amount_aed, Decimal("12.35"))
        self.assertIsNone(result.quote)
        self.assertEqual(result.status, "LOCAL")


if __name__ == "__main__":
    unittest.main()
