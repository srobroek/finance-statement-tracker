"""Evidence-first foreign-currency conversion for the statement boundary.

Mastercard's public calculator can be selected without credentials, but an
unauthenticated response is surfaced as a provider error (one request, no
retry or challenge bypass).  The supported Mastercard Developers API variant
is available by selecting its documented endpoint and supplying its OAuth
Authorization header.  Both paths request a unit quote so no transaction
amount, merchant, or event id is sent to the rate provider.

Mastercard API documentation:
https://www.postman.com/mastercard/mastercard-developers/documentation/
vm9iy9y/mastercard-standard-currency-conversion-calculator-api
Machine-readable request collection used to verify the documented operation:
https://raw.githubusercontent.com/api-evangelist/mastercard/main/collections/
mastercard-standard-currency-conversion-calculator.opencollection.json
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import re
from typing import Any, Protocol, TypeAlias
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_MONEY_UNIT = Decimal("0.01")
_CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")
MASTERCARD_SANDBOX_ENDPOINT = (
    "https://sandbox.api.mastercard.com/settlement/currencyrate/conversion-rate"
)
MASTERCARD_PUBLIC_CALCULATOR_ENDPOINT = (
    "https://www.mastercard.com/marketingservices/public/"
    "mccom-services/currency-conversions/conversion-rates"
)
MASTERCARD_STANDARD_DOCS_URL = (
    "https://www.postman.com/mastercard/mastercard-developers/documentation/"
    "vm9iy9y/mastercard-standard-currency-conversion-calculator-api"
)
POSTED = "POSTED"
ESTIMATED = "ESTIMATED"
MoneyInput: TypeAlias = Decimal | str | int | float
DateInput: TypeAlias = date | datetime | str


class FxConversionError(ValueError):
    """Explicit failure retaining source facts for retry or review."""

    retryable = True

    def __init__(
        self,
        message: str,
        *,
        source: str = "unknown",
        original_amount: Decimal | None = None,
        original_currency: str | None = None,
        provider_source: str | None = None,
        quote: FxQuote | None = None,
    ) -> None:
        super().__init__(message)
        self.source = source
        self.original_amount = original_amount
        self.original_currency = original_currency
        self.provider_source = provider_source
        self.quote = quote

    @property
    def provenance(self) -> dict[str, Any]:
        """Return source facts retained when conversion cannot complete."""
        return {
            "original_amount": (
                None if self.original_amount is None else str(self.original_amount)
            ),
            "original_currency": self.original_currency,
            "source": self.source,
            "provider_source": self.provider_source,
            "quote": None if self.quote is None else self.quote.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "FAILED",
            "error": type(self).__name__,
            "message": str(self),
            "retryable": self.retryable,
            "review_required": True,
            "provenance": self.provenance,
        }


class InvalidAmountError(FxConversionError):
    """An amount is missing, malformed, non-finite, or non-positive."""


class UnsupportedCurrencyError(FxConversionError):
    """A currency is not an ISO-4217 three-letter code."""


class MissingQuoteError(FxConversionError):
    """Neither a posted AED amount nor a usable quote was available."""


class InvalidQuoteError(FxConversionError):
    """A quote is malformed, non-finite, non-positive, or lacks provenance."""


class StaleQuoteError(FxConversionError):
    """A quote is older than the allowed age for the transaction date."""


class FutureQuoteError(FxConversionError):
    """A quote is dated after the transaction or after today."""


class FxProviderError(FxConversionError):
    """A provider failed without a safe value to use.

    Provider failures are terminal for this conversion attempt.  Callers may
    retain the source envelope for an explicit review/replay, but this module
    never asks a provider to retry.
    """

    retryable = False

    def __init__(self, message: str, *, provider_source: str, **context: Any) -> None:
        super().__init__(message, provider_source=provider_source, **context)


class ProviderChallengeError(FxProviderError):
    """Interactive login, MFA, CAPTCHA, or bot challenge; never bypass it."""


@dataclass(frozen=True, slots=True)
class FxQuote:
    """Immutable quote: units of ``to_currency`` per ``from_currency``."""

    from_currency: str
    to_currency: str
    rate: Decimal
    rate_date: date
    source: str
    source_url: str | None = None
    retrieved_at: datetime | None = None
    reference: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_currency": self.from_currency,
            "to_currency": self.to_currency,
            "rate": str(self.rate),
            "rate_date": self.rate_date.isoformat(),
            "source": self.source,
            "source_url": self.source_url,
            "retrieved_at": None if self.retrieved_at is None else self.retrieved_at.isoformat(),
            "reference": self.reference,
        }


class ExchangeRateProvider(Protocol):
    def quote(
        self,
        *,
        from_currency: str,
        to_currency: str,
        rate_date: date,
    ) -> FxQuote:
        """Return one dated quote or raise an :class:`FxProviderError`."""


@dataclass(frozen=True, slots=True)
class FxConversionRequest:
    """Immutable source envelope used by the downstream integration."""

    original_amount: MoneyInput
    original_currency: str
    transaction_date: DateInput
    source: str = "unknown"
    bank_posted_aed: MoneyInput | None = None
    bank_posted_source: str | None = None
    quote: FxQuote | Mapping[str, Any] | None = None
    target_currency: str = "AED"


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """Converted amount with source, status, and quote provenance."""

    original_amount: Decimal
    original_currency: str
    amount_aed: Decimal
    target_currency: str
    status: str
    source: str
    quote: FxQuote | None = None
    bank_posted_aed: Decimal | None = None
    request_source: str | None = None

    @property
    def estimated(self) -> bool:
        return self.status == ESTIMATED

    @property
    def rate(self) -> Decimal | None:
        return None if self.quote is None else self.quote.rate

    @property
    def rate_date(self) -> date | None:
        return None if self.quote is None else self.quote.rate_date

    @property
    def quote_source(self) -> str | None:
        return None if self.quote is None else self.quote.source

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_amount": str(self.original_amount),
            "original_currency": self.original_currency,
            "amount_aed": str(self.amount_aed),
            "target_currency": self.target_currency,
            "status": self.status,
            "estimated": self.estimated,
            "source": self.source,
            "request_source": self.request_source,
            "rate": None if self.rate is None else str(self.rate),
            "rate_date": None if self.rate_date is None else self.rate_date.isoformat(),
            "quote_source": self.quote_source,
            "bank_posted_aed": None if self.bank_posted_aed is None else str(self.bank_posted_aed),
            "quote": None if self.quote is None else self.quote.to_dict(),
        }




def _context(
    error: FxConversionError,
    *,
    source: str,
    original_amount: Decimal | None,
    original_currency: str | None,
) -> FxConversionError:
    error.source = source
    error.original_amount = original_amount
    error.original_currency = original_currency
    return error


def _decimal(value: object, label: str) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise InvalidAmountError(f"{label} must be a decimal amount") from error
    if not parsed.is_finite():
        raise InvalidAmountError(f"{label} must be finite")
    return parsed


def _money(value: object, label: str) -> Decimal:
    try:
        return _decimal(value, label).quantize(_MONEY_UNIT, rounding=ROUND_HALF_UP)
    except InvalidOperation as error:
        raise InvalidAmountError(f"{label} cannot be rounded to minor units") from error


def _currency(value: object, label: str) -> str:
    currency = str(value or "").strip().upper()
    if not _CURRENCY_CODE.fullmatch(currency):
        raise UnsupportedCurrencyError(f"{label} must be a three-letter ISO code")
    return currency


def _date(value: DateInput, label: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{label} is required")
    try:
        return date.fromisoformat(raw[:10])
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO date or datetime") from error


def _quote_from_value(value: FxQuote | Mapping[str, Any]) -> FxQuote:
    if isinstance(value, FxQuote):
        return value
    if not isinstance(value, Mapping):
        raise InvalidQuoteError("quote must be an FxQuote or mapping")

    def first(*keys: str) -> object:
        return next((value[key] for key in keys if value.get(key) not in (None, "")), None)

    raw_date = first("rate_date", "quote_date", "fxDate")
    if raw_date is None:
        raise InvalidQuoteError("quote rate_date is required")
    try:
        parsed_date = _date(raw_date, "quote rate_date")
        raw_rate = first("rate", "conversion_rate", "conversionRate")
        parsed_rate = raw_rate if isinstance(raw_rate, Decimal) else Decimal(str(raw_rate))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise InvalidQuoteError("quote rate or rate_date is invalid") from error
    retrieved = first("retrieved_at", "retrievedAt")
    try:
        retrieved_at = (
            None
            if retrieved is None
            else datetime.fromisoformat(str(retrieved).replace("Z", "+00:00"))
        )
    except ValueError as error:
        raise InvalidQuoteError("quote retrieved_at must be an ISO datetime") from error
    source_url = first("source_url", "sourceUrl")
    reference = first("reference", "quote_reference", "name")
    return FxQuote(
        from_currency=str(first("from_currency", "from", "transCurr") or ""),
        to_currency=str(first("to_currency", "to", "crdhldBillCurr") or ""),
        rate=parsed_rate,
        rate_date=parsed_date,
        source=str(first("source", "provider") or ""),
        source_url=None if source_url is None else str(source_url),
        retrieved_at=retrieved_at,
        reference=None if reference is None else str(reference),
    )


def _validate_quote(
    quote: FxQuote,
    *,
    original_currency: str,
    target_currency: str,
    transaction_date: date,
    source: str,
    original_amount: Decimal,
    max_quote_age_days: int | None,
) -> FxQuote:
    try:
        quote_from = _currency(quote.from_currency, "quote from_currency")
        quote_to = _currency(quote.to_currency, "quote to_currency")
    except FxConversionError as error:
        raise InvalidQuoteError(
            str(error), source=source, original_amount=original_amount,
            original_currency=original_currency, quote=quote,
        ) from error
    if (quote_from, quote_to) != (original_currency, target_currency):
        raise InvalidQuoteError(
            f"quote currency pair {quote_from}/{quote_to} does not match "
            f"{original_currency}/{target_currency}",
            source=source, original_amount=original_amount,
            original_currency=original_currency, quote=quote,
        )
    try:
        rate = quote.rate if isinstance(quote.rate, Decimal) else Decimal(str(quote.rate))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise InvalidQuoteError(
            "quote rate must be a decimal", source=source,
            original_amount=original_amount, original_currency=original_currency, quote=quote,
        ) from error
    if not rate.is_finite() or rate <= 0:
        raise InvalidQuoteError(
            "quote rate must be finite and greater than zero", source=source,
            original_amount=original_amount, original_currency=original_currency, quote=quote,
        )
    if isinstance(quote.rate_date, datetime):
        quote_date = quote.rate_date.date()
    elif isinstance(quote.rate_date, date):
        quote_date = quote.rate_date
    else:
        raise InvalidQuoteError(
            "quote rate_date must be a date", source=source,
            original_amount=original_amount, original_currency=original_currency, quote=quote,
        )
    if quote_date > date.today() or quote_date > transaction_date:
        raise FutureQuoteError(
            f"quote dated {quote_date.isoformat()} is in the future", source=source,
            original_amount=original_amount, original_currency=original_currency, quote=quote,
        )
    if max_quote_age_days is not None:
        if max_quote_age_days < 0:
            raise ValueError("max_quote_age_days must be non-negative or None")
        age = (transaction_date - quote_date).days
        if age > max_quote_age_days:
            raise StaleQuoteError(
                f"quote dated {quote_date.isoformat()} is {age} days old; maximum is "
                f"{max_quote_age_days}", source=source, original_amount=original_amount,
                original_currency=original_currency, quote=quote,
            )
    if not str(quote.source or "").strip():
        raise InvalidQuoteError(
            "quote source is required", source=source, original_amount=original_amount,
            original_currency=original_currency, quote=quote,
        )
    if (
        rate != quote.rate or quote_date != quote.rate_date
        or quote.from_currency != quote_from or quote.to_currency != quote_to
    ):
        return FxQuote(
            quote_from, quote_to, rate, quote_date, quote.source, quote.source_url,
            quote.retrieved_at, quote.reference,
        )
    return quote


def _posted_result(
    *,
    original_amount: Decimal,
    original_currency: str,
    amount_aed: Decimal,
    source: str,
    request_source: str,
    quote: FxQuote | None = None,
    bank_posted_aed: Decimal | None = None,
) -> ConversionResult:
    return ConversionResult(
        original_amount, original_currency, amount_aed, "AED", POSTED, source,
        quote, bank_posted_aed, request_source,
    )


def convert_to_aed(
    original_amount: MoneyInput,
    original_currency: str,
    *,
    transaction_date: DateInput,
    bank_posted_aed: MoneyInput | None = None,
    bank_posted_source: str | None = None,
    quote: FxQuote | Mapping[str, Any] | None = None,
    provider: ExchangeRateProvider | None = None,
    source: str = "unknown",
    max_quote_age_days: int | None = 1,
) -> ConversionResult:
    """Prefer posted AED, then replayed quote, then a dated unit provider quote."""
    source = str(source or "").strip() or "unknown"
    try:
        original_currency = _currency(original_currency, "original_currency")
    except FxConversionError as error:
        error.source = source
        error.original_currency = str(original_currency or "").strip().upper() or None
        raise
    try:
        original = _decimal(original_amount, "original_amount")
    except FxConversionError as error:
        error.source = source
        error.original_currency = original_currency
        raise
    try:
        transaction_date = _date(transaction_date, "transaction_date")
    except ValueError as error:
        raise FxConversionError(
            str(error), source=source, original_amount=original,
            original_currency=original_currency,
        ) from error
    if original <= 0:
        raise InvalidAmountError(
            "original_amount must be greater than zero", source=source,
            original_amount=original, original_currency=original_currency,
        )

    if bank_posted_aed is not None:
        try:
            posted = _money(bank_posted_aed, "bank_posted_aed")
        except FxConversionError as error:
            raise _context(
                error, source=source, original_amount=original,
                original_currency=original_currency,
            ) from error
        if posted <= 0:
            raise InvalidAmountError(
                "bank_posted_aed must be greater than zero", source=source,
                original_amount=original, original_currency=original_currency,
            )
        preserved_quote = None
        if isinstance(quote, FxQuote):
            preserved_quote = quote
        elif quote is not None:
            try:
                preserved_quote = _quote_from_value(quote)
            except FxConversionError:
                pass
        return _posted_result(
            original_amount=original, original_currency=original_currency,
            amount_aed=posted, source=str(bank_posted_source or "bank-posted-aed").strip()
            or "bank-posted-aed", request_source=source, quote=preserved_quote,
            bank_posted_aed=posted,
        )

    if original_currency == "AED":
        return _posted_result(
            original_amount=original, original_currency=original_currency,
            amount_aed=_money(original, "original_amount"), source=source,
            request_source=source,
        )

    recorded_quote: FxQuote | None = None
    if quote is not None:
        try:
            recorded_quote = _validate_quote(
                _quote_from_value(quote), original_currency=original_currency,
                target_currency="AED", transaction_date=transaction_date, source=source,
                original_amount=original, max_quote_age_days=max_quote_age_days,
            )
        except FxConversionError as error:
            raise _context(
                error, source=source, original_amount=original,
                original_currency=original_currency,
            ) from error

    if recorded_quote is None:
        if provider is None:
            raise MissingQuoteError(
                f"no posted AED amount or FX quote for {original_currency} -> AED",
                source=source, original_amount=original, original_currency=original_currency,
            )
        provider_source = str(getattr(provider, "source", type(provider).__name__))
        try:
            provided = provider.quote(
                from_currency=original_currency, to_currency="AED", rate_date=transaction_date,
            )
        except FxConversionError as error:
            raise _context(
                error, source=source, original_amount=original,
                original_currency=original_currency,
            ) from error
        except Exception as error:
            raise FxProviderError(
                f"FX provider failed: {error}", source=source,
                original_amount=original, original_currency=original_currency,
                provider_source=provider_source,
            ) from error
        if provided is None:
            raise MissingQuoteError(
                f"provider returned no quote for {original_currency} -> AED", source=source,
                original_amount=original, original_currency=original_currency,
                provider_source=provider_source,
            )
        try:
            recorded_quote = _validate_quote(
                _quote_from_value(provided), original_currency=original_currency,
                target_currency="AED", transaction_date=transaction_date, source=source,
                original_amount=original, max_quote_age_days=max_quote_age_days,
            )
        except FxConversionError as error:
            raise _context(
                error, source=source, original_amount=original,
                original_currency=original_currency,
            ) from error

    try:
        amount_aed = (original * recorded_quote.rate).quantize(
            _MONEY_UNIT, rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError) as error:
        raise InvalidQuoteError(
            "quote could not produce a rounded AED amount", source=source,
            original_amount=original, original_currency=original_currency, quote=recorded_quote,
        ) from error
    if amount_aed <= 0:
        raise InvalidQuoteError(
            "quote result rounds to zero; retain the source for review",
            source=source, original_amount=original,
            original_currency=original_currency, quote=recorded_quote,
        )
    return ConversionResult(
        original, original_currency, amount_aed, "AED", ESTIMATED, source,
        recorded_quote, None, source,
    )


def convert(
    request: FxConversionRequest,
    *,
    provider: ExchangeRateProvider | None = None,
    max_quote_age_days: int | None = 1,
) -> ConversionResult:
    """Execute the request-shaped downstream integration contract."""
    target = _currency(request.target_currency, "target_currency")
    if target != "AED":
        raise UnsupportedCurrencyError("this conversion core's target currency is AED")
    return convert_to_aed(
        request.original_amount, request.original_currency,
        transaction_date=request.transaction_date, bank_posted_aed=request.bank_posted_aed,
        bank_posted_source=request.bank_posted_source, quote=request.quote, provider=provider,
        source=request.source, max_quote_age_days=max_quote_age_days,
    )


class MastercardCurrencyRateProvider:
    """Mastercard public calculator or documented Developers API provider."""

    source = "mastercard-currency-conversion-calculator"

    def __init__(
        self,
        *,
        endpoint_url: str = MASTERCARD_PUBLIC_CALCULATOR_ENDPOINT,
        authorization: str | Callable[[str, str], str] | None = None,
        public_calculator: bool = True,
        timeout: float = 15.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        if not endpoint_url.startswith(("https://", "http://")):
            raise ValueError("Mastercard endpoint_url must use HTTP(S)")
        self.endpoint_url = endpoint_url
        self.authorization = authorization
        self.public_calculator = public_calculator
        self.source = (
            "mastercard-public-calculator"
            if public_calculator else "mastercard-standard-currency-conversion-calculator"
        )
        self.timeout = timeout
        self.opener = opener

    @staticmethod
    def request_params(
        *,
        from_currency: str,
        to_currency: str,
        rate_date: date,
        public_calculator: bool = False,
    ) -> dict[str, str]:
        if public_calculator:
            return {
                "exchange_date": rate_date.isoformat(),
                "transaction_currency": from_currency,
                "cardholder_billing_currency": to_currency,
                "bank_fee": "0",
                "transaction_amount": "1",
            }
        return {
            "fxDate": rate_date.isoformat(),
            "transCurr": from_currency,
            "crdhldBillCurr": to_currency,
            "transAmt": "1",
        }

    def _authorization_header(self, request_url: str) -> str | None:
        if self.authorization is None:
            return None
        try:
            value = (
                self.authorization("GET", request_url)
                if callable(self.authorization) else self.authorization
            )
        except Exception as error:
            raise FxProviderError(
                f"Mastercard authorization signer failed: {error}", provider_source=self.source,
            ) from error
        value = str(value or "").strip()
        return value or None

    @staticmethod
    def _challenge(body: bytes) -> bool:
        text = body.decode("utf-8", errors="replace").casefold()
        return any(
            marker in text for marker in (
                "captcha",
                "verify you are human",
                "interactive challenge",
                "mfa challenge",
                "multi-factor challenge",
                "login challenge",
                "challenge",
            )
        )

    @staticmethod
    def _status(response: Any) -> int:
        return int(getattr(response, "status", 200))

    def quote(
        self,
        *,
        from_currency: str,
        to_currency: str,
        rate_date: date,
    ) -> FxQuote:
        from_currency = _currency(from_currency, "from_currency")
        to_currency = _currency(to_currency, "to_currency")
        query = self.request_params(
            from_currency=from_currency, to_currency=to_currency, rate_date=rate_date,
            public_calculator=self.public_calculator,
        )
        request_url = f"{self.endpoint_url}?{urlencode(query)}"
        headers = {"Accept": "application/json"}
        if not self.public_calculator:
            authorization = self._authorization_header(request_url)
            if authorization is None:
                raise FxProviderError(
                    "Mastercard OAuth Authorization header or signer is required",
                    provider_source=self.source,
                )
            headers["Authorization"] = authorization
        request = Request(request_url, headers=headers, method="GET")
        try:
            response = self.opener(request, timeout=self.timeout)
            body = response.read()
            if not isinstance(body, bytes):
                body = str(body).encode("utf-8")
            status = self._status(response)
        except HTTPError as error:
            body = error.read()
            if self._challenge(body):
                raise ProviderChallengeError(
                    "Mastercard provider returned an interactive challenge; stopping",
                    provider_source=self.source,
                ) from error
            raise FxProviderError(
                f"Mastercard provider returned HTTP {error.code}", provider_source=self.source,
            ) from error
        except URLError as error:
            raise FxProviderError(
                f"Mastercard provider is unavailable: {error.reason}", provider_source=self.source,
            ) from error
        except OSError as error:
            raise FxProviderError(
                f"Mastercard provider is unavailable: {error}", provider_source=self.source,
            ) from error
        if status < 200 or status >= 300:
            if self._challenge(body):
                raise ProviderChallengeError(
                    "Mastercard provider returned an interactive challenge; stopping",
                    provider_source=self.source,
                )
            raise FxProviderError(
                f"Mastercard provider returned HTTP {status}", provider_source=self.source,
            )
        if self._challenge(body):
            raise ProviderChallengeError(
                "Mastercard provider returned an interactive challenge; stopping",
                provider_source=self.source,
            )
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FxProviderError(
                "Mastercard response was not JSON", provider_source=self.source,
            ) from error
        if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), Mapping):
            raise FxProviderError(
                "Mastercard response is missing its data object", provider_source=self.source,
            )
        data = payload["data"]
        error_code = data.get("errorCode") or payload.get("errorCode")
        error_message = data.get("errorMessage") or payload.get("errorMessage")
        if error_code or error_message:
            raise FxProviderError(
                f"Mastercard quote unavailable ({error_code or 'provider-error'}): "
                f"{error_message or 'no rate returned'}", provider_source=self.source,
            )
        raw_rate, raw_date = data.get("conversionRate"), data.get("fxDate")
        if raw_rate in (None, "") or raw_date in (None, ""):
            raise FxProviderError(
                "Mastercard response is missing conversionRate or fxDate",
                provider_source=self.source,
            )
        try:
            rate = Decimal(str(raw_rate))
            parsed_date = _date(raw_date, "Mastercard fxDate")
        except (InvalidOperation, TypeError, ValueError) as error:
            raise InvalidQuoteError(
                "Mastercard conversionRate or fxDate is invalid", provider_source=self.source,
            ) from error
        if not rate.is_finite() or rate <= 0:
            raise InvalidQuoteError(
                "Mastercard conversionRate must be finite and greater than zero",
                provider_source=self.source,
            )
        return FxQuote(
            str(data.get("transCurr") or from_currency).upper(),
            str(data.get("crdhldBillCurr") or to_currency).upper(),
            rate,
            parsed_date,
            self.source,
            self.endpoint_url,
            datetime.now(UTC),
            str(payload.get("name") or "settlement-conversion-rate"),
        )


__all__ = [
    "ConversionResult",
    "DateInput",
    "ESTIMATED",
    "ExchangeRateProvider",
    "FxConversionError",
    "FxConversionRequest",
    "FxProviderError",
    "FxQuote",
    "FutureQuoteError",
    "InvalidAmountError",
    "InvalidQuoteError",
    "MASTERCARD_PUBLIC_CALCULATOR_ENDPOINT",
    "MASTERCARD_SANDBOX_ENDPOINT",
    "MASTERCARD_STANDARD_DOCS_URL",
    "MastercardCurrencyRateProvider",
    "MissingQuoteError",
    "POSTED",
    "ProviderChallengeError",
    "StaleQuoteError",
    "UnsupportedCurrencyError",
    "convert",
    "convert_to_aed",
]
