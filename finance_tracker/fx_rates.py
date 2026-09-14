"""Credential-free issuer FX estimates for notification conversion.

The providers in this module read only public, indicative bank quotes.  They
never use a banking session, retry a challenge, or claim to reproduce a card
settlement rate.  A quote keeps the original request and the publication and
retrieval provenance needed for a later review.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any, Protocol, TypeAlias
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "fx-sources.json"
AED = "AED"
ESTIMATE = "ESTIMATE"
DISPLAYED_SELL = "DISPLAYED_SELL"
BASE_PER_QUOTE = "BASE_PER_QUOTE"
QUOTE_PER_BASE = "QUOTE_PER_BASE"
_MONEY = Decimal("0.01")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_DATE = re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})(?!\d)")
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


MoneyInput: TypeAlias = Decimal | str | int | float
OpenResponse: TypeAlias = Callable[..., Any]


class FxRateError(ValueError):
    """Base error retaining enough context for a retry or review."""

    retryable = True

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        currency: str | None = None,
        original_amount: Decimal | None = None,
        success_cursor: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.currency = currency
        self.original_amount = original_amount
        # A failed provider chain must never advance a caller's success cursor.
        self.success_cursor = success_cursor

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "FAILED",
            "error": type(self).__name__,
            "message": str(self),
            "retryable": self.retryable,
            "review_required": True,
            "provider": self.provider,
            "currency": self.currency,
            "original_amount": (
                None if self.original_amount is None else str(self.original_amount)
            ),
            "success_cursor": self.success_cursor,
        }


class MissingCurrencyError(FxRateError):
    """A source is usable but does not publish the requested currency."""


class MalformedQuoteError(FxRateError):
    """A source response exists but cannot be safely interpreted."""


class StaleQuoteError(FxRateError):
    """The published quote is older than the caller's allowed age."""


class FutureQuoteError(FxRateError):
    """A source quote is newer than the transaction/as-of date."""


class ProviderUnavailableError(FxRateError):
    """A public provider could not be read without a safe value."""


class ProviderChallengeError(ProviderUnavailableError):
    """A provider returned a login, MFA, CAPTCHA, or bot challenge."""

    retryable = False


class NoUsableQuoteError(FxRateError):
    """Every permitted provider failed; callers must not commit a cursor."""

    def __init__(
        self,
        message: str,
        *,
        errors: Sequence[FxRateError] = (),
        currency: str | None = None,
        original_amount: Decimal | None = None,
    ) -> None:
        super().__init__(
            message,
            currency=currency,
            original_amount=original_amount,
            success_cursor=None,
        )
        self.errors = tuple(errors)

    def to_dict(self) -> dict[str, Any]:
        result = super().to_dict()
        result["errors"] = [error.to_dict() for error in self.errors]
        return result


@dataclass(frozen=True, slots=True)
class FxQuote:
    """One displayed bank sell quote, expressed in AED per foreign unit."""

    from_currency: str
    to_currency: str
    rate: Decimal
    rate_date: date
    source: str
    source_url: str | None = None
    retrieved_at: datetime | None = None
    reference: str | None = None
    quote_basis: str = BASE_PER_QUOTE
    units: str = "AED per 1 foreign-currency unit"
    sell_basis: str = DISPLAYED_SELL
    published_at: datetime | None = None
    original_amount: Decimal | None = None
    original_currency: str | None = None
    uncertainty: str = ESTIMATE

    def __post_init__(self) -> None:
        from_currency = _currency(self.from_currency, "from_currency")
        to_currency = _currency(self.to_currency, "to_currency")
        if to_currency != AED:
            raise ValueError("issuer sell quotes must be expressed in AED")
        object.__setattr__(self, "from_currency", from_currency)
        object.__setattr__(self, "to_currency", to_currency)
        try:
            rate = (
                self.rate if isinstance(self.rate, Decimal) else Decimal(str(self.rate))
            )
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ValueError("rate must be a decimal") from error
        if not rate.is_finite() or rate <= 0:
            raise ValueError("rate must be finite and greater than zero")
        object.__setattr__(self, "rate", rate)
        if isinstance(self.rate_date, datetime) or not isinstance(self.rate_date, date):
            raise ValueError("rate_date must be a date")
        basis = str(self.quote_basis or "").strip().upper()
        if basis not in {BASE_PER_QUOTE, QUOTE_PER_BASE}:
            raise ValueError("quote_basis must be BASE_PER_QUOTE or QUOTE_PER_BASE")
        object.__setattr__(self, "quote_basis", basis)
        sell_basis = str(self.sell_basis or "").strip().upper()
        if sell_basis != DISPLAYED_SELL:
            raise ValueError("issuer quotes must retain the displayed sell basis")
        object.__setattr__(self, "sell_basis", sell_basis)
        if not str(self.units or "").strip():
            raise ValueError("units are required")
        if self.uncertainty != ESTIMATE:
            raise ValueError("public bank quotes must be labelled ESTIMATE")
        if self.retrieved_at is not None:
            object.__setattr__(
                self, "retrieved_at", _utc_datetime(self.retrieved_at, "retrieved_at")
            )
        if self.published_at is not None:
            object.__setattr__(
                self, "published_at", _utc_datetime(self.published_at, "published_at")
            )
        if self.original_amount is not None:
            amount = _amount(self.original_amount, "original_amount")
            object.__setattr__(self, "original_amount", amount)
        if self.original_currency is not None:
            object.__setattr__(
                self,
                "original_currency",
                _currency(self.original_currency, "original_currency"),
            )

    @property
    def provider(self) -> str:
        """Provider label retained alongside the legacy ``source`` field."""
        return self.source

    @property
    def quote_date(self) -> date:
        return self.rate_date

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_currency": self.from_currency,
            "to_currency": self.to_currency,
            "original_currency": self.original_currency,
            "original_amount": (
                None if self.original_amount is None else str(self.original_amount)
            ),
            "rate": str(self.rate),
            "rate_date": self.rate_date.isoformat(),
            "quote_date": self.rate_date.isoformat(),
            "quote_basis": self.quote_basis,
            "units": self.units,
            "sell_basis": self.sell_basis,
            "source": self.source,
            "provider": self.provider,
            "source_url": self.source_url,
            "published_at": _iso_datetime(self.published_at),
            "retrieved_at": _iso_datetime(self.retrieved_at),
            "reference": self.reference,
            "uncertainty": self.uncertainty,
        }


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """A rounded AED estimate while retaining the unrounded source request."""

    original_amount: Decimal
    original_currency: str
    amount_aed: Decimal
    quote: FxQuote | None
    status: str = ESTIMATE
    target_currency: str = AED

    @property
    def estimated(self) -> bool:
        return self.status == ESTIMATE

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_amount": str(self.original_amount),
            "original_currency": self.original_currency,
            "amount_aed": str(self.amount_aed),
            "target_currency": self.target_currency,
            "status": self.status,
            "estimated": self.estimated,
            "quote": None if self.quote is None else self.quote.to_dict(),
        }

    def to_trace(self) -> dict[str, Any]:
        """Return a lossless, replayable audit record for this conversion."""
        return {"trace_type": "FX_CONVERSION", **self.to_dict()}


class QuoteProvider(Protocol):
    source: str

    def quote(
        self,
        *,
        currency: str,
        original_amount: Decimal | None = None,
        as_of: date | datetime | None = None,
    ) -> FxQuote:
        """Return one quote or raise a typed :class:`FxRateError`."""
        ...


class _TableParser(HTMLParser):
    """Small dependency-free parser for the public ADCB table."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self.table_attributes: list[dict[str, str]] = []
        self._table: list[list[str]] | None = None
        self._table_attributes: dict[str, str] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.lower()
        if lower == "table" and self._table is None:
            self._table = []
            self._table_attributes = {
                key.casefold(): value for key, value in attrs if value is not None
            }
        elif lower == "tr" and self._table is not None:
            self._row = []
        elif lower in {"th", "td"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower in {"th", "td"} and self._cell is not None and self._row is not None:
            value = " ".join("".join(self._cell).split())
            self._row.append(value)
            self._cell = None
        elif lower == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif lower == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
                self.table_attributes.append(self._table_attributes or {})
            self._table = None
            self._table_attributes = None


def _currency(value: object, label: str = "currency") -> str:
    result = str(value or "").strip().upper()
    if not _CURRENCY.fullmatch(result):
        raise ValueError(f"{label} must be a three-letter ISO currency")
    return result


def _amount(value: object, label: str = "amount") -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a decimal") from error
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{label} must be finite and greater than zero")
    return result


def _utc_datetime(value: datetime, label: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{label} must include a UTC offset")
    return value.astimezone(UTC)


def _iso_datetime(value: datetime | None) -> str | None:
    return (
        None
        if value is None
        else value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    )


def _as_date(value: date | datetime | str | None, label: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    try:
        return date.fromisoformat(raw[:10])
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO date") from error


def _parse_date(value: object) -> date | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = date.fromisoformat(raw[:10])
    except ValueError:
        parsed = None
    if parsed is not None:
        return parsed
    match = _DATE.search(raw)
    if match:
        first, second, year = (int(part) for part in match.groups())
        if first > 12:
            day, month = first, second
        elif second > 12 or "/" in match.group(0):
            month, day = first, second
        else:
            day, month = first, second
        try:
            return date(year, month, day)
        except ValueError as error:
            raise MalformedQuoteError("provider date is invalid") from error
    match = _ISO_DATE.search(raw)
    if match:
        year, month, day = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError as error:
            raise MalformedQuoteError("provider date is invalid") from error
    for pattern in (
        r"(?<!\d)(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})(?!\d)",
        r"(?<!\d)([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})(?!\d)",
    ):
        month_match = re.search(pattern, raw)
        if month_match:
            try:
                if month_match.group(1).isalpha():
                    parsed = datetime.strptime(
                        f"{month_match.group(1)} {month_match.group(2)} {month_match.group(3)}",
                        "%B %d %Y",
                    ).replace(tzinfo=UTC)
                else:
                    parsed = datetime.strptime(
                        f"{month_match.group(1)} {month_match.group(2)} {month_match.group(3)}",
                        "%d %B %Y",
                    ).replace(tzinfo=UTC)
            except ValueError as error:
                raise MalformedQuoteError("provider date is invalid") from error
            return parsed.date()
    raise MalformedQuoteError("provider date is invalid")


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise MalformedQuoteError("provider datetime is invalid") from error
    return (
        parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    )


def _decimal(
    value: object,
    label: str,
    *,
    provider: str | None = None,
    currency: str | None = None,
    original_amount: Decimal | None = None,
) -> Decimal:
    try:
        result = (
            value
            if isinstance(value, Decimal)
            else Decimal(str(value).replace(",", "").strip())
        )
    except (InvalidOperation, TypeError, ValueError) as error:
        raise MalformedQuoteError(
            f"{label} is not decimal",
            provider=provider,
            currency=currency,
            original_amount=original_amount,
        ) from error
    if not result.is_finite() or result <= 0:
        raise MalformedQuoteError(
            f"{label} must be finite and greater than zero",
            provider=provider,
            currency=currency,
            original_amount=original_amount,
        )
    return result


def _json_mapping(
    payload: object,
    *,
    provider: str | None = None,
    currency: str | None = None,
    original_amount: Decimal | None = None,
) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        return payload
    raise MalformedQuoteError(
        "provider response must be an object",
        provider=provider,
        currency=currency,
        original_amount=original_amount,
    )


def _body(response: Any, *, provider: str) -> bytes:
    status = int(getattr(response, "status", 200))
    raw = response.read()
    if status in {401, 403, 429}:
        raise ProviderChallengeError(
            f"{provider} returned an access challenge ({status})", provider=provider
        )
    if status >= 500:
        raise ProviderUnavailableError(
            f"{provider} is unavailable ({status})", provider=provider
        )
    if status >= 400:
        raise ProviderUnavailableError(
            f"{provider} returned HTTP {status}", provider=provider
        )
    if isinstance(raw, str):
        return raw.encode("utf-8")
    return bytes(raw)


def _open_request(
    opener: OpenResponse, request: Request, *, provider: str, timeout: float
) -> bytes:
    try:
        response = opener(request, timeout=timeout)
        return _body(response, provider=provider)
    except (ProviderChallengeError, ProviderUnavailableError):
        raise
    except HTTPError as error:
        if error.code in {401, 403, 429}:
            raise ProviderChallengeError(
                f"{provider} returned an access challenge ({error.code})",
                provider=provider,
            ) from error
        raise ProviderUnavailableError(
            f"{provider} returned HTTP {error.code}", provider=provider
        ) from error
    except (URLError, TimeoutError, OSError) as error:
        raise ProviderUnavailableError(
            f"{provider} is unavailable", provider=provider
        ) from error


def _challenge_text(text: str) -> bool:
    return bool(
        re.search(
            r"captcha|verify\s+you\s+are\s+human|mfa|one[- ]time\s+pass|"
            r"access denied|challenge required",
            text,
            re.IGNORECASE,
        )
    )


def _retrieved(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        raise ValueError("clock must return an aware datetime")
    return value.astimezone(UTC)


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    lowered = {str(key).lower(): value for key, value in mapping.items()}
    for key in keys:
        if key.lower() in lowered and lowered[key.lower()] not in (None, ""):
            return lowered[key.lower()]
    return None


def _quote_from_row(
    row: Mapping[str, Any],
    *,
    provider: str,
    source_url: str,
    requested_currency: str,
    retrieved_at: datetime,
    original_amount: Decimal | None,
    default_date: date | None = None,
    reference: str | None = None,
    strict_evidence: bool = False,
) -> FxQuote:
    if strict_evidence:
        raw_from = _first(row, "FromCurrency", "fromCurrency", "from_currency")
        raw_to = _first(row, "ToCurrency", "toCurrency", "to_currency")
        raw_rate = _first(
            row, "SellRate", "sellRate", "sell_rate", "sellingRate", "selling_rate"
        )
        for field, value in (
            ("FromCurrency", raw_from),
            ("ToCurrency", raw_to),
            ("SellRate", raw_rate),
        ):
            if value is None:
                raise MalformedQuoteError(
                    f"{provider} response has no {field}",
                    provider=provider,
                    currency=requested_currency,
                    original_amount=original_amount,
                )
    else:
        raw_from = _first(
            row, "fromCurrency", "from_currency", "baseCurrency", "base_currency"
        )
        raw_to = _first(
            row,
            "toCurrency",
            "to_currency",
            "quoteCurrency",
            "quote_currency",
            "fixedCurrencyCode",
            "currency",
            "code",
        )
        raw_rate = _first(
            row,
            "sellRate",
            "sell_rate",
            "sellingRate",
            "selling_rate",
            "rate",
            "value",
        )
    try:
        from_currency = _currency(
            raw_from if strict_evidence else raw_from or AED, "from_currency"
        )
        to_currency = _currency(
            raw_to if strict_evidence else raw_to or requested_currency,
            "to_currency",
        )
    except ValueError as error:
        raise MalformedQuoteError(
            f"{provider} response has an invalid currency",
            provider=provider,
            currency=requested_currency,
            original_amount=original_amount,
        ) from error
    if from_currency != AED or to_currency != requested_currency:
        raise MissingCurrencyError(
            f"{provider} has no AED sell quote for {requested_currency}",
            provider=provider,
            currency=requested_currency,
            original_amount=original_amount,
        )
    if raw_rate is None:
        raise MalformedQuoteError(
            f"{provider} response has no sell rate",
            provider=provider,
            currency=requested_currency,
            original_amount=original_amount,
        )
    quote_date = (
        _parse_date(
            _first(
                row,
                "quoteDate",
                "quote_date",
                "rateDate",
                "rate_date",
                "asOfDate",
                "publishedDate",
                "fileDate",
            )
        )
        or default_date
    )
    if quote_date is None:
        raise MalformedQuoteError(
            f"{provider} response has no publication date",
            provider=provider,
            currency=requested_currency,
            original_amount=original_amount,
        )
    published_at = _parse_datetime(
        _first(row, "publishedAt", "published_at", "published")
    )
    return FxQuote(
        from_currency=requested_currency,
        to_currency=AED,
        rate=_decimal(
            raw_rate,
            "sell rate",
            provider=provider,
            currency=requested_currency,
            original_amount=original_amount,
        ),
        rate_date=quote_date,
        source=provider,
        source_url=source_url,
        retrieved_at=retrieved_at,
        published_at=published_at,
        reference=reference,
        original_amount=original_amount,
        original_currency=requested_currency,
    )


def _required_field(
    row: Mapping[str, Any],
    field: str,
    *,
    provider: str,
    currency: str,
    original_amount: Decimal | None,
) -> Any:
    value = row.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise MalformedQuoteError(
            f"{provider} response is missing required {field}",
            provider=provider,
            currency=currency,
            original_amount=original_amount,
        )
    return value


def _rakbank_quote_from_row(
    row: Mapping[str, Any],
    *,
    source_url: str,
    requested_currency: str,
    retrieved_at: datetime,
    original_amount: Decimal | None,
    quote_date: date,
    reference: str,
) -> FxQuote:
    raw_currency = _required_field(
        row,
        "fixedCurrencyCode",
        provider=RakbankRateProvider.source,
        currency=requested_currency,
        original_amount=original_amount,
    )
    try:
        row_currency = _currency(raw_currency, "RAKBANK fixedCurrencyCode")
    except ValueError as error:
        raise MalformedQuoteError(
            "RAKBANK fixedCurrencyCode is malformed",
            provider=RakbankRateProvider.source,
            currency=requested_currency,
            original_amount=original_amount,
        ) from error
    if row_currency != requested_currency:
        raise MissingCurrencyError(
            f"RAKBANK has no AED sell quote for {requested_currency}",
            provider=RakbankRateProvider.source,
            currency=requested_currency,
            original_amount=original_amount,
        )
    raw_rate = _required_field(
        row,
        "sellRate",
        provider=RakbankRateProvider.source,
        currency=requested_currency,
        original_amount=original_amount,
    )
    if "publishedAt" in row:
        try:
            published_at = _parse_datetime(row["publishedAt"])
        except MalformedQuoteError as error:
            raise MalformedQuoteError(
                "RAKBANK publishedAt is malformed",
                provider=RakbankRateProvider.source,
                currency=requested_currency,
                original_amount=original_amount,
            ) from error
    else:
        published_at = None
    return FxQuote(
        from_currency=requested_currency,
        to_currency=AED,
        rate=_decimal(
            raw_rate,
            "RAKBANK sellRate",
            provider=RakbankRateProvider.source,
            currency=requested_currency,
            original_amount=original_amount,
        ),
        rate_date=quote_date,
        source=RakbankRateProvider.source,
        source_url=source_url,
        retrieved_at=retrieved_at,
        published_at=published_at,
        reference=reference,
        original_amount=original_amount,
        original_currency=requested_currency,
    )


class RakbankRateProvider:
    """RAKBANK's credential-free public JSON POST quote endpoint."""

    source = "RAKBANK_PUBLIC_SELL"

    def __init__(
        self,
        endpoint_url: str = "https://www.rakbank.ae/api/forex/rate",
        *,
        opener: OpenResponse = urlopen,
        timeout: float = 10.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.endpoint_url = endpoint_url
        self.opener = opener
        self.timeout = timeout
        self.clock = clock

    def quote(
        self,
        *,
        currency: str,
        original_amount: Decimal | None = None,
        as_of: date | datetime | None = None,
    ) -> FxQuote:
        requested = _currency(currency)
        amount = (
            None
            if original_amount is None
            else _amount(original_amount, "original_amount")
        )
        # The public endpoint has only been observed with an empty JSON body.
        # Do not send caller-controlled currency or amount fields that are not
        # part of that observed contract.
        payload: dict[str, Any] = {}
        request = Request(
            self.endpoint_url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        raw = _open_request(
            self.opener, request, provider=self.source, timeout=self.timeout
        )
        text = raw.decode("utf-8", errors="replace")
        if _challenge_text(text):
            raise ProviderChallengeError(
                "RAKBANK returned an access challenge", provider=self.source
            )
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as error:
            raise MalformedQuoteError(
                "RAKBANK response is not JSON",
                provider=self.source,
                currency=requested,
                original_amount=amount,
            ) from error
        response = _json_mapping(
            decoded,
            provider=self.source,
            currency=requested,
            original_amount=amount,
        )
        retrieved_at = _retrieved(self.clock)
        wrapped = _required_field(
            response,
            "responseContent",
            provider=self.source,
            currency=requested,
            original_amount=amount,
        )
        if isinstance(wrapped, str):
            if not wrapped.strip():
                raise MalformedQuoteError(
                    "RAKBANK responseContent is empty",
                    provider=self.source,
                    currency=requested,
                    original_amount=amount,
                )
            try:
                wrapped = json.loads(wrapped)
            except json.JSONDecodeError as error:
                raise MalformedQuoteError(
                    "RAKBANK responseContent is not JSON",
                    provider=self.source,
                    currency=requested,
                    original_amount=amount,
                ) from error
        try:
            content = _json_mapping(wrapped)
        except MalformedQuoteError as error:
            raise MalformedQuoteError(
                "RAKBANK responseContent must be an object",
                provider=self.source,
                currency=requested,
                original_amount=amount,
            ) from error
        raw_rates = _required_field(
            content,
            "forexRates",
            provider=self.source,
            currency=requested,
            original_amount=amount,
        )
        if not isinstance(raw_rates, list):
            raise MalformedQuoteError(
                "RAKBANK forexRates must be a list",
                provider=self.source,
                currency=requested,
                original_amount=amount,
            )
        raw_file_date = _required_field(
            content,
            "fileDate",
            provider=self.source,
            currency=requested,
            original_amount=amount,
        )
        try:
            quote_date = _parse_date(raw_file_date)
        except MalformedQuoteError as error:
            raise MalformedQuoteError(
                "RAKBANK fileDate is malformed",
                provider=self.source,
                currency=requested,
                original_amount=amount,
            ) from error
        if quote_date is None:
            raise MalformedQuoteError(
                "RAKBANK fileDate is malformed",
                provider=self.source,
                currency=requested,
                original_amount=amount,
            )
        rows: list[Mapping[str, Any]] = []
        for index, row in enumerate(raw_rates):
            if not isinstance(row, Mapping):
                raise MalformedQuoteError(
                    f"RAKBANK forexRates row {index} must be an object",
                    provider=self.source,
                    currency=requested,
                    original_amount=amount,
                )
            raw_row_currency = _required_field(
                row,
                "fixedCurrencyCode",
                provider=self.source,
                currency=requested,
                original_amount=amount,
            )
            try:
                row_currency = _currency(raw_row_currency, "RAKBANK fixedCurrencyCode")
            except ValueError as error:
                raise MalformedQuoteError(
                    f"RAKBANK forexRates row {index} fixedCurrencyCode is malformed",
                    provider=self.source,
                    currency=requested,
                    original_amount=amount,
                ) from error
            _required_field(
                row,
                "sellRate",
                provider=self.source,
                currency=requested,
                original_amount=amount,
            )
            if row_currency == requested:
                rows.append(row)
        if rows:
            return _rakbank_quote_from_row(
                rows[0],
                source_url=self.endpoint_url,
                requested_currency=requested,
                retrieved_at=retrieved_at,
                original_amount=amount,
                quote_date=quote_date,
                reference="POST /api/forex/rate",
            )
        raise MissingCurrencyError(
            f"RAKBANK has no AED sell quote for {requested}",
            provider=self.source,
            currency=requested,
            original_amount=amount,
        )


class AdcbRateProvider:
    """ADCB public HTML quote provider, restricted to the main/ASP segment."""

    source = "ADCB_PUBLIC_SELL_MAIN_ASP"

    def __init__(
        self,
        endpoint_url: str = "https://www.adcb.com/en/personal/accounts/money-transfer/fx-rate",
        *,
        opener: OpenResponse = urlopen,
        timeout: float = 10.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.endpoint_url = endpoint_url
        self.opener = opener
        self.timeout = timeout
        self.clock = clock

    def quote(
        self,
        *,
        currency: str,
        original_amount: Decimal | None = None,
        as_of: date | datetime | None = None,
    ) -> FxQuote:
        requested = _currency(currency)
        amount = (
            None
            if original_amount is None
            else _amount(original_amount, "original_amount")
        )
        request = Request(
            self.endpoint_url, headers={"Accept": "text/html"}, method="GET"
        )
        raw = _open_request(
            self.opener, request, provider=self.source, timeout=self.timeout
        )
        text = unescape(raw.decode("utf-8", errors="replace"))
        if _challenge_text(text):
            raise ProviderChallengeError(
                "ADCB returned an access challenge", provider=self.source
            )
        retrieved_at = _retrieved(self.clock)
        try:
            default_date = _page_date(text)
        except MalformedQuoteError as error:
            raise MalformedQuoteError(
                "ADCB publication date is malformed",
                provider=self.source,
                currency=requested,
                original_amount=amount,
            ) from error
        rows = _adcb_standard_rows(text)
        for row in rows:
            try:
                return _quote_from_row(
                    row,
                    provider=self.source,
                    source_url=self.endpoint_url,
                    requested_currency=requested,
                    retrieved_at=retrieved_at,
                    original_amount=amount,
                    default_date=default_date,
                    strict_evidence=True,
                    reference="ADCB main/ASP sell table",
                )
            except MissingCurrencyError:
                continue
        if not rows and not _adcb_has_standard_segment_evidence(text):
            raise MalformedQuoteError(
                "ADCB response has no evidenced main/ASP segment",
                provider=self.source,
                currency=requested,
                original_amount=amount,
            )
        if default_date is None:
            raise MalformedQuoteError(
                "ADCB response has no publication date",
                provider=self.source,
                currency=requested,
                original_amount=amount,
            )
        raise MissingCurrencyError(
            f"ADCB main/ASP table has no sell quote for {requested}",
            provider=self.source,
            currency=requested,
            original_amount=amount,
        )


def _page_date(text: str) -> date | None:
    for match in re.finditer(
        r"(?:as\s+of\s+date|as[- ]of|published|updated)[^<]{0,80}",
        text,
        re.IGNORECASE,
    ):
        parsed = _parse_date(match.group(0))
        if parsed is not None:
            return parsed
    return None


_ADCB_STANDARD_SEGMENTS = ("ASP_JsonD", "Main_JsonD", "STANDARD_JsonD")
_ADCB_PREMIUM_SEGMENTS = ("PCL_JsonD", "EXC_JsonD", "EMI_JsonD", "EME_JsonD")


def _segment_marker(value: object, segments: Sequence[str]) -> bool:
    normalized = str(value or "").casefold()
    return any(
        re.search(
            rf"(?<![a-z0-9_]){re.escape(segment.casefold())}(?![a-z0-9_])",
            normalized,
        )
        for segment in segments
    )


def _adcb_standard_table(attrs: Mapping[str, str]) -> bool:
    values = [
        attrs.get(key, "") for key in ("data-segment", "data-section", "id", "name")
    ]
    if any(_segment_marker(value, _ADCB_PREMIUM_SEGMENTS) for value in values):
        return False
    return any(_segment_marker(value, _ADCB_STANDARD_SEGMENTS) for value in values)


def _adcb_has_standard_segment_evidence(text: str) -> bool:
    for variable in _ADCB_STANDARD_SEGMENTS:
        assignment = re.compile(
            rf"(?<![A-Za-z0-9_])(?:var\s+)?{re.escape(variable)}"
            rf"(?![A-Za-z0-9_])\s*=",
            re.IGNORECASE,
        )
        if assignment.search(text):
            return True
    parser = _TableParser()
    parser.feed(text)
    return any(_adcb_standard_table(attrs) for attrs in parser.table_attributes)


def _rows_from_json_array(text: str, variable: str) -> list[Mapping[str, Any]]:
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_])(?:var\s+)?{re.escape(variable)}"
        rf"(?![A-Za-z0-9_])\s*=\s*(\[[\s\S]*?\])\s*;?",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        return []
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    return (
        [row for row in value if isinstance(row, Mapping)]
        if isinstance(value, list)
        else []
    )


def _adcb_standard_rows(text: str) -> list[Mapping[str, Any]]:
    # Premium arrays are never merged, so duplicate USD rows cannot overwrite
    # the explicitly identified standard main/ASP evidence.
    for variable in _ADCB_STANDARD_SEGMENTS:
        rows = _rows_from_json_array(text, variable)
        if rows:
            return rows
    parser = _TableParser()
    parser.feed(text)
    selected: list[Mapping[str, Any]] = []
    for table, attrs in zip(parser.tables, parser.table_attributes):
        if not table or not _adcb_standard_table(attrs):
            continue
        headers = [cell.casefold() for cell in table[0]]
        currency_index = next(
            (
                index
                for index, cell in enumerate(headers)
                if "currency code" in cell or cell == "currency"
            ),
            None,
        )
        sell_index = next(
            (index for index, cell in enumerate(headers) if "sell" in cell), None
        )
        if currency_index is None or sell_index is None:
            continue
        for row in table[1:]:
            if max(currency_index, sell_index) >= len(row):
                continue
            selected.append(
                {
                    "fromCurrency": AED,
                    "toCurrency": row[currency_index],
                    "sellRate": row[sell_index],
                }
            )
        if selected:
            return selected
    return []


class FabRateProvider(AdcbRateProvider):
    """Optional latest-only FAB fallback; never enabled by default."""

    source = "FAB_PUBLIC_SELL_ESTIMATE"

    def __init__(
        self,
        endpoint_url: str = "https://www.bankfab.com/en-ae/personal/fx-rate",
        **kwargs: Any,
    ) -> None:
        super().__init__(endpoint_url, **kwargs)

    def quote(
        self,
        *,
        currency: str,
        original_amount: Decimal | None = None,
        as_of: date | datetime | None = None,
    ) -> FxQuote:
        # FAB renders one latest-only table.  Reuse only the dependency-free
        # table parser; its direction and AED/unit basis remain an estimate.
        requested = _currency(currency)
        amount = (
            None
            if original_amount is None
            else _amount(original_amount, "original_amount")
        )
        request = Request(
            self.endpoint_url, headers={"Accept": "text/html"}, method="GET"
        )
        raw = _open_request(
            self.opener, request, provider=self.source, timeout=self.timeout
        )
        text = unescape(raw.decode("utf-8", errors="replace"))
        if _challenge_text(text):
            raise ProviderChallengeError(
                "FAB returned an access challenge", provider=self.source
            )
        retrieved_at = _retrieved(self.clock)
        published = _page_date(text)
        parser = _TableParser()
        parser.feed(text)
        for table in parser.tables:
            if not table:
                continue
            headers = [cell.casefold() for cell in table[0]]
            currency_index = next(
                (
                    index
                    for index, cell in enumerate(headers)
                    if "currency" in cell or cell == "code"
                ),
                None,
            )
            sell_index = next(
                (
                    index
                    for index, cell in enumerate(headers)
                    if "sell" in cell or "selling" in cell
                ),
                None,
            )
            if currency_index is None or sell_index is None:
                continue
            for row in table[1:]:
                if (
                    max(currency_index, sell_index) >= len(row)
                    or row[currency_index].upper() != requested
                ):
                    continue
                return _quote_from_row(
                    {
                        "fromCurrency": AED,
                        "toCurrency": requested,
                        "sellRate": row[sell_index],
                    },
                    provider=self.source,
                    source_url=self.endpoint_url,
                    requested_currency=requested,
                    retrieved_at=retrieved_at,
                    original_amount=amount,
                    default_date=published,
                    reference="FAB latest-only selling table",
                )
        raise MissingCurrencyError(
            f"FAB latest table has no sell quote for {requested}",
            provider=self.source,
            currency=requested,
            original_amount=amount,
        )


def validate_quote(
    quote: FxQuote,
    *,
    as_of: date | datetime | None = None,
    max_age_seconds: int = 86_400,
) -> FxQuote:
    """Reject future or stale source dates before any conversion."""
    if isinstance(max_age_seconds, bool) or max_age_seconds < 1:
        raise ValueError("max_age_seconds must be positive")
    if as_of is None:
        target_at = datetime.now(UTC)
        target_date = target_at.date()
    elif isinstance(as_of, datetime):
        target_at = _utc_datetime(as_of, "as_of")
        target_date = target_at.date()
    else:
        target_date = _as_date(as_of, "as_of")
        assert target_date is not None
        # A date-only as-of value covers the complete UTC publication day.
        target_at = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            23,
            59,
            59,
            999999,
            tzinfo=UTC,
        )
    if quote.published_at is not None:
        # Provider timestamps are second-precision evidence.  Normalize both
        # sides before comparing so sub-second transport noise cannot turn a
        # same-second publication into a future or stale quote.
        publication_at = quote.published_at.replace(microsecond=0)
        comparison_at = target_at.replace(microsecond=0)
        age_seconds = (comparison_at - publication_at).total_seconds()
        if age_seconds < 0:
            raise FutureQuoteError(
                f"{quote.provider} publication is after the requested time",
                provider=quote.provider,
                currency=quote.from_currency,
                original_amount=quote.original_amount,
            )
        if age_seconds > max_age_seconds:
            raise StaleQuoteError(
                f"{quote.provider} publication is stale by {age_seconds:g} seconds",
                provider=quote.provider,
                currency=quote.from_currency,
                original_amount=quote.original_amount,
            )
    if quote.rate_date > target_date:
        raise FutureQuoteError(
            f"{quote.provider} quote is after the requested date",
            provider=quote.provider,
            currency=quote.from_currency,
            original_amount=quote.original_amount,
        )
    if quote.published_at is None:
        age = (target_date - quote.rate_date).days * 86_400
        if age > max_age_seconds:
            raise StaleQuoteError(
                f"{quote.provider} quote is stale by {age} seconds",
                provider=quote.provider,
                currency=quote.from_currency,
                original_amount=quote.original_amount,
            )
    else:
        # Precise publication evidence governs age.  Keep only the
        # contradiction guard for a quote date that predates that evidence;
        # recomputing calendar-day age here would penalize a midnight boundary.
        quote_publication_date = quote.published_at.date()
        if quote.rate_date < quote_publication_date:
            raise StaleQuoteError(
                f"{quote.provider} quote date predates precise publication evidence",
                provider=quote.provider,
                currency=quote.from_currency,
                original_amount=quote.original_amount,
            )
    return quote


def resolve_quote(
    currency: str,
    *,
    original_amount: MoneyInput | None = None,
    as_of: date | datetime | None = None,
    providers: Iterable[QuoteProvider] | None = None,
    include_fab: bool = False,
    max_age_seconds: int = 86_400,
) -> FxQuote:
    """Try issuer then ADCB, optionally FAB, without hiding source failures."""
    requested = _currency(currency)
    amount = (
        None if original_amount is None else _amount(original_amount, "original_amount")
    )
    if requested == AED:
        raise ValueError("AED does not require an issuer quote")
    chain = (
        tuple(providers)
        if providers is not None
        else (
            RakbankRateProvider(),
            AdcbRateProvider(),
            *((FabRateProvider(),) if include_fab else ()),
        )
    )
    errors: list[FxRateError] = []
    for provider in chain:
        try:
            quote = provider.quote(
                currency=requested, original_amount=amount, as_of=as_of
            )
            return validate_quote(quote, as_of=as_of, max_age_seconds=max_age_seconds)
        except ProviderChallengeError:
            # Never bypass a provider's interactive safety boundary.
            raise
        except FxRateError as error:
            errors.append(error)
    raise NoUsableQuoteError(
        f"no usable public FX quote for {requested}",
        errors=errors,
        currency=requested,
        original_amount=amount,
    )


def convert_to_aed(
    original_amount: MoneyInput,
    original_currency: str,
    *,
    as_of: date | datetime | None = None,
    quote: FxQuote | Mapping[str, Any] | None = None,
    providers: Iterable[QuoteProvider] | None = None,
    include_fab: bool = False,
    max_age_seconds: int = 86_400,
) -> ConversionResult:
    """Convert using a displayed sell quote and round only the AED result."""
    amount = _amount(original_amount, "original_amount")
    currency = _currency(original_currency, "original_currency")
    if currency == AED:
        return ConversionResult(
            original_amount=amount,
            original_currency=currency,
            amount_aed=amount.quantize(_MONEY, rounding=ROUND_HALF_UP),
            quote=None,
            status="LOCAL",
        )
    if quote is not None and not isinstance(quote, (FxQuote, Mapping)):
        raise TypeError("quote must be FxQuote, mapping, or None")
    if quote is None:
        selected = resolve_quote(
            currency,
            original_amount=amount,
            as_of=as_of,
            providers=providers,
            include_fab=include_fab,
            max_age_seconds=max_age_seconds,
        )
    elif isinstance(quote, FxQuote):
        selected = quote
        validate_quote(selected, as_of=as_of, max_age_seconds=max_age_seconds)
    else:
        data = dict(quote)
        raw_rate = data.get("rate")
        if raw_rate is None:
            raise MalformedQuoteError(
                "quote mapping has no rate",
                provider=str(data.get("source", data.get("provider", "unknown"))),
                currency=currency,
                original_amount=amount,
            )
        parsed_rate = _decimal(
            raw_rate,
            "quote rate",
            provider=str(data.get("source", data.get("provider", "unknown"))),
            currency=currency,
            original_amount=amount,
        )
        parsed_date = _parse_date(data.get("rate_date", data.get("quote_date")))
        if parsed_date is None:
            raise MalformedQuoteError(
                "quote mapping has no publication date",
                provider=str(data.get("source", data.get("provider", "unknown"))),
                currency=currency,
                original_amount=amount,
            )
        selected = FxQuote(
            from_currency=str(
                data.get("from_currency", data.get("original_currency", currency))
            ),
            to_currency=str(data.get("to_currency", AED)),
            rate=parsed_rate,
            rate_date=parsed_date,
            source=str(data.get("source", data.get("provider", "unknown"))),
            source_url=data.get("source_url"),
            retrieved_at=_parse_datetime(data.get("retrieved_at")),
            published_at=_parse_datetime(data.get("published_at")),
            reference=data.get("reference"),
            quote_basis=str(data.get("quote_basis", BASE_PER_QUOTE)),
            units=str(data.get("units", "AED per 1 foreign-currency unit")),
            sell_basis=str(data.get("sell_basis", DISPLAYED_SELL)),
            original_amount=amount,
            original_currency=currency,
        )
        validate_quote(selected, as_of=as_of, max_age_seconds=max_age_seconds)
    if selected.from_currency != currency or selected.to_currency != AED:
        raise MalformedQuoteError(
            "quote currency pair does not match the original currency",
            provider=selected.provider,
            currency=currency,
            original_amount=amount,
        )
    if selected.quote_basis == BASE_PER_QUOTE:
        converted = amount * selected.rate
    else:
        converted = amount / selected.rate
    result = converted.quantize(_MONEY, rounding=ROUND_HALF_UP)
    if result <= 0:
        raise MalformedQuoteError(
            "quote conversion rounded to zero",
            provider=selected.provider,
            currency=currency,
            original_amount=amount,
        )
    if selected.original_amount != amount or selected.original_currency != currency:
        selected = FxQuote(
            from_currency=selected.from_currency,
            to_currency=selected.to_currency,
            rate=selected.rate,
            rate_date=selected.rate_date,
            source=selected.source,
            source_url=selected.source_url,
            retrieved_at=selected.retrieved_at,
            reference=selected.reference,
            quote_basis=selected.quote_basis,
            units=selected.units,
            sell_basis=selected.sell_basis,
            published_at=selected.published_at,
            original_amount=amount,
            original_currency=currency,
            uncertainty=selected.uncertainty,
        )
    return ConversionResult(
        original_amount=amount,
        original_currency=currency,
        amount_aed=result,
        quote=selected,
    )


def load_fx_sources(path: str | Path = DEFAULT_CONFIG_PATH) -> Mapping[str, Any]:
    """Load and minimally validate the credential-free source registry."""
    source_path = Path(path)
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f"FX source registry cannot be loaded: {source_path}"
        ) from error
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise ValueError("FX source registry schema_version must be 1")
    providers = payload.get("providers")
    if not isinstance(providers, list) or not providers:
        raise ValueError("FX source registry must declare providers")
    provider_ids = {
        str(item.get("id")) for item in providers if isinstance(item, Mapping)
    }
    if not {"rakbank", "adcb"}.issubset(provider_ids):
        raise ValueError("FX source registry must declare RAKBANK and ADCB")
    if provider_ids & {"standard_chartered", "sc", "emirates_islamic", "ei"}:
        raise ValueError("FX source registry must not invent SC or EI FX endpoints")
    return payload


__all__ = [
    "AED",
    "BASE_PER_QUOTE",
    "DISPLAYED_SELL",
    "ESTIMATE",
    "AdcbRateProvider",
    "ConversionResult",
    "FabRateProvider",
    "FutureQuoteError",
    "FxQuote",
    "FxRateError",
    "MalformedQuoteError",
    "MissingCurrencyError",
    "NoUsableQuoteError",
    "ProviderChallengeError",
    "ProviderUnavailableError",
    "QUOTE_PER_BASE",
    "RakbankRateProvider",
    "StaleQuoteError",
    "convert_to_aed",
    "load_fx_sources",
    "resolve_quote",
    "validate_quote",
]
