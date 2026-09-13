from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


def money(value: Decimal | str | int | float | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _required_text(value: Any, context: str) -> str:
    rendered = str(value or "").strip()
    if not rendered:
        raise ValueError(f"{context} is required")
    return rendered


def _non_negative_money(value: Any, context: str) -> Decimal:
    if isinstance(value, bool) or value in (None, ""):
        raise ValueError(f"{context} is required")
    try:
        rendered = money(value)
    except Exception as exc:
        raise ValueError(f"{context} must be a decimal number") from exc
    if not rendered.is_finite() or rendered < 0:
        raise ValueError(f"{context} must be a non-negative amount")
    return rendered


def _utc_datetime(value: Any, context: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = _required_text(value, context)
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{context} must be an ISO datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{context} must include a UTC offset")
    return parsed.astimezone(UTC)


def _iso_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _iso_date(value: Any, context: str) -> date:
    if isinstance(value, datetime):
        raise ValueError(f"{context} must be an ISO date")
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(_required_text(value, context))
    except ValueError as exc:
        raise ValueError(f"{context} must be an ISO date") from exc


@dataclass(slots=True)
class Transaction:
    transaction_id: str
    transaction_at: datetime
    card: str
    merchant_raw: str
    amount_aed: Decimal
    account: str | None = None
    owner: str | None = None
    institution: str | None = None
    account_last4: str | None = None
    currency: str = "AED"
    amount_original: Decimal | None = None
    channel: str = "UNKNOWN"
    source_type: str = "manual"
    source_message_id: str | None = None
    vendor: str | None = None
    category: str | None = None
    subcategory: str | None = None
    transaction_type: str = "PURCHASE"
    reward_bucket: str | None = None
    tags: set[str] = field(default_factory=set)
    evidence_policy: str | None = None
    evidence_status: str = "NOT_REQUESTED"
    review_required: bool = False
    is_refund: bool = False
    is_subscription: bool = False
    property_code: str | None = None
    rental_unit: str | None = None
    source_direction: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.amount_aed = money(self.amount_aed)
        if self.amount_original is not None:
            self.amount_original = money(self.amount_original)
            if self.amount_original < 0:
                raise ValueError(
                    "Canonical amount_original must be a non-negative magnitude"
                )
        self.currency = self.currency.upper()
        self.card = self.card.upper()
        self.channel = self.channel.upper()
        if self.amount_aed < 0:
            raise ValueError("Canonical amount_aed must be a non-negative magnitude")
        if self.source_direction is not None:
            self.source_direction = str(self.source_direction).strip().upper() or None
            if self.source_direction not in {"CREDIT", "DEBIT"}:
                raise ValueError("source_direction must be CREDIT or DEBIT")

    @property
    def spend_aed(self) -> Decimal:
        from .transaction_semantics import spend_amount

        return spend_amount(self)

    @property
    def is_foreign(self) -> bool:
        return self.currency != "AED"

    def value(self, field_name: str) -> Any:
        if field_name == "is_foreign":
            return self.is_foreign
        if field_name == "spend_aed":
            return self.spend_aed
        if hasattr(self, field_name):
            return getattr(self, field_name)
        return self.metadata.get(field_name)

    def set_value(self, field_name: str, value: Any) -> None:
        locked = set(self.metadata.get("locked_fields", []))
        if field_name in locked:
            return
        if hasattr(self, field_name):
            if field_name in {"amount_aed", "amount_original"} and value is not None:
                value = money(value)
                if value < 0:
                    raise ValueError(
                        f"Canonical {field_name} must be a non-negative magnitude"
                    )
            if field_name == "source_direction" and value is not None:
                value = str(value).strip().upper()
                if value not in {"CREDIT", "DEBIT"}:
                    raise ValueError("source_direction must be CREDIT or DEBIT")
            setattr(self, field_name, value)
        else:
            self.metadata[field_name] = value

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "transaction_at": self.transaction_at.isoformat(),
            "card": self.card,
            "merchant_raw": self.merchant_raw,
            "amount_aed": str(self.amount_aed),
            "account": self.account,
            "owner": self.owner,
            "institution": self.institution,
            "account_last4": self.account_last4,
            "currency": self.currency,
            "amount_original": None if self.amount_original is None else str(self.amount_original),
            "channel": self.channel,
            "source_type": self.source_type,
            "source_message_id": self.source_message_id,
            "vendor": self.vendor,
            "category": self.category,
            "subcategory": self.subcategory,
            "transaction_type": self.transaction_type,
            "reward_bucket": self.reward_bucket,
            "tags": sorted(self.tags),
            "evidence_policy": self.evidence_policy,
            "evidence_status": self.evidence_status,
            "review_required": self.review_required,
            "is_refund": self.is_refund,
            "is_subscription": self.is_subscription,
            "property_code": self.property_code,
            "rental_unit": self.rental_unit,
            "source_direction": self.source_direction,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class StatementReceipt:
    receipt_id: str
    source_identity: str
    card_code: str
    original_received_at: datetime
    period_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "receipt_id", _required_text(self.receipt_id, "receipt_id")
        )
        object.__setattr__(
            self,
            "source_identity",
            _required_text(self.source_identity, "source_identity"),
        )
        object.__setattr__(
            self, "card_code", _required_text(self.card_code, "card_code").upper()
        )
        object.__setattr__(
            self,
            "original_received_at",
            _utc_datetime(self.original_received_at, "original_received_at"),
        )
        if self.period_id is not None:
            object.__setattr__(
                self, "period_id", _required_text(self.period_id, "period_id")
            )

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "receipt_id": self.receipt_id,
            "source_identity": self.source_identity,
            "card_code": self.card_code,
            "original_received_at": _iso_datetime(self.original_received_at),
        }
        if self.period_id is not None:
            result["period_id"] = self.period_id
        return result


@dataclass(frozen=True, slots=True)
class CashbackPeriod:
    period_id: str
    card_code: str
    period_start: datetime
    period_end: datetime
    status: str = "OPEN"
    closed_by_receipt_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "period_id", _required_text(self.period_id, "period_id")
        )
        object.__setattr__(
            self, "card_code", _required_text(self.card_code, "card_code").upper()
        )
        start = _utc_datetime(self.period_start, "period_start")
        end = _utc_datetime(self.period_end, "period_end")
        if end <= start:
            raise ValueError("period_end must be after period_start")
        object.__setattr__(self, "period_start", start)
        object.__setattr__(self, "period_end", end)
        status = _required_text(self.status, "period status").upper()
        if status not in {"OPEN", "CLOSED"}:
            raise ValueError("period status must be OPEN or CLOSED")
        object.__setattr__(self, "status", status)
        if status == "CLOSED" and not self.closed_by_receipt_id:
            raise ValueError("closed periods require closed_by_receipt_id")
        if status == "OPEN" and self.closed_by_receipt_id is not None:
            raise ValueError("open periods cannot carry closed_by_receipt_id")
        if self.closed_by_receipt_id is not None:
            object.__setattr__(
                self,
                "closed_by_receipt_id",
                _required_text(self.closed_by_receipt_id, "closed_by_receipt_id"),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "period_id": self.period_id,
            "card_code": self.card_code,
            "period_start": _iso_datetime(self.period_start),
            "period_end": _iso_datetime(self.period_end),
            "status": self.status,
            "closed_by_receipt_id": self.closed_by_receipt_id,
        }


@dataclass(frozen=True, slots=True)
class CardMembership:
    card_code: str
    coverage: str = "UNKNOWN"
    sc_held: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "card_code", _required_text(self.card_code, "card_code").upper()
        )
        coverage = _required_text(self.coverage, "coverage").upper()
        if coverage not in {"HELD", "NOT_HELD", "UNKNOWN"}:
            raise ValueError("coverage must be HELD, NOT_HELD, or UNKNOWN")
        if coverage == "UNKNOWN" and self.sc_held is not None:
            raise ValueError("unknown card coverage cannot claim a held value")
        if coverage == "HELD" and self.sc_held is not True:
            raise ValueError("held card coverage requires held=true")
        if coverage == "NOT_HELD" and self.sc_held is not False:
            raise ValueError("not-held card coverage requires held=false")
        object.__setattr__(self, "coverage", coverage)

    def to_dict(self) -> dict[str, object]:
        return {
            "card_code": self.card_code,
            "coverage": self.coverage,
            "sc_held": self.sc_held,
        }


@dataclass(frozen=True, slots=True)
class RewardAccounting:
    period_id: str
    card_code: str
    qualifying_spend: Decimal
    refund_deductions: Decimal
    consumed_cap_headroom: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "period_id", _required_text(self.period_id, "period_id")
        )
        object.__setattr__(
            self, "card_code", _required_text(self.card_code, "card_code").upper()
        )
        for field_name in (
            "qualifying_spend",
            "refund_deductions",
            "consumed_cap_headroom",
        ):
            object.__setattr__(
                self,
                field_name,
                _non_negative_money(getattr(self, field_name), field_name),
            )

    @property
    def qualification_spend(self) -> Decimal:
        return self.qualifying_spend

    def to_dict(self) -> dict[str, object]:
        return {
            "period_id": self.period_id,
            "card_code": self.card_code,
            "qualifying_spend": str(self.qualifying_spend),
            "refund_deductions": str(self.refund_deductions),
            "consumed_cap_headroom": str(self.consumed_cap_headroom),
        }


@dataclass(frozen=True, slots=True)
class CategoryAssessment:
    transaction_id: str
    status: str
    category: str | None = None
    review_required: bool = False
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "transaction_id",
            _required_text(self.transaction_id, "transaction_id"),
        )
        status = _required_text(self.status, "category status").upper()
        if status not in {"RESOLVED", "UNRESOLVED"}:
            raise ValueError("category status must be RESOLVED or UNRESOLVED")
        if status == "UNRESOLVED" and self.review_required is not True:
            raise ValueError("unresolved categories require review_required=true")
        if status == "RESOLVED" and not _required_text(self.category, "category"):
            raise ValueError("resolved categories require a category")
        object.__setattr__(self, "status", status)
        if self.category is not None:
            object.__setattr__(
                self, "category", _required_text(self.category, "category")
            )
        if self.reason is not None:
            object.__setattr__(self, "reason", _required_text(self.reason, "reason"))

    def to_dict(self) -> dict[str, object]:
        return {
            "transaction_id": self.transaction_id,
            "status": self.status,
            "category": self.category,
            "review_required": self.review_required,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class FxSnapshot:
    schema_version: int
    snapshot_id: str
    provider: str
    base_currency: str
    quote_currency: str
    observed_at: datetime
    quote_date: date
    quote_basis: str
    rate: Decimal
    precision: int
    max_age_seconds: int
    source_identity: str
    uncertainty: str = "UNKNOWN"

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("FX snapshot schema_version must be 1")
        for field_name in ("snapshot_id", "provider", "source_identity"):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        for field_name in ("base_currency", "quote_currency"):
            currency = _required_text(getattr(self, field_name), field_name).upper()
            if len(currency) != 3:
                raise ValueError(f"{field_name} must be a three-letter currency")
            object.__setattr__(self, field_name, currency)
        observed_at = _utc_datetime(self.observed_at, "observed_at")
        quote_date = _iso_date(self.quote_date, "quote_date")
        if quote_date > observed_at.date():
            raise ValueError("quote_date cannot be after observed_at")
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "quote_date", quote_date)
        basis = _required_text(self.quote_basis, "quote_basis").upper()
        if basis not in {"BASE_PER_QUOTE", "QUOTE_PER_BASE"}:
            raise ValueError("quote_basis must be BASE_PER_QUOTE or QUOTE_PER_BASE")
        object.__setattr__(self, "quote_basis", basis)
        object.__setattr__(self, "rate", _non_negative_money(self.rate, "rate"))
        if self.rate <= 0:
            raise ValueError("rate must be greater than zero")
        if isinstance(self.precision, bool) or not 0 <= self.precision <= 12:
            raise ValueError("precision must be between 0 and 12")
        if isinstance(self.max_age_seconds, bool) or self.max_age_seconds < 1:
            raise ValueError("max_age_seconds must be positive")
        uncertainty = _required_text(self.uncertainty, "uncertainty").upper()
        if uncertainty not in {"KNOWN", "ESTIMATE", "UNKNOWN"}:
            raise ValueError("uncertainty must be KNOWN, ESTIMATE, or UNKNOWN")
        object.__setattr__(self, "uncertainty", uncertainty)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "provider": self.provider,
            "base_currency": self.base_currency,
            "quote_currency": self.quote_currency,
            "observed_at": _iso_datetime(self.observed_at),
            "quote_date": self.quote_date.isoformat(),
            "quote_basis": self.quote_basis,
            "rate": str(self.rate),
            "precision": self.precision,
            "max_age_seconds": self.max_age_seconds,
            "source_identity": self.source_identity,
            "uncertainty": self.uncertainty,
        }


def period_for_timestamp(
    periods: Sequence[CashbackPeriod],
    timestamp: datetime,
    *,
    card_code: str | None = None,
) -> CashbackPeriod | None:
    """Return the period containing ``timestamp`` under the [start, end) rule."""
    instant = _utc_datetime(timestamp, "timestamp")
    selected_card = card_code.upper() if card_code is not None else None
    for period in sorted(periods, key=lambda item: item.period_start):
        if selected_card is not None and period.card_code != selected_card:
            continue
        if period.period_start <= instant < period.period_end:
            return period
    return None


def _cashback_state_schema_path() -> Path:
    return (
        Path(__file__).resolve().parent.parent
        / "config"
        / "cashback-state-schema-v1.json"
    )


def _validate_state_schema(source: Mapping[str, Any]) -> None:
    path = _cashback_state_schema_path()
    if not path.is_file():
        raise ValueError("Cashback state schema is missing")
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Cashback state schema cannot be loaded") from exc
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            source
        ),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "$"
        raise ValueError(
            f"Cashback state schema error at {location}: {errors[0].message}"
        )


def validate_cashback_state(source: Mapping[str, Any]) -> None:
    """Validate a live state payload without treating unknown coverage as zero."""
    if not isinstance(source, Mapping):
        raise ValueError("Cashback state must be an object")
    _validate_state_schema(source)
    receipts = tuple(StatementReceipt(**row) for row in source["receipts"])
    periods = tuple(CashbackPeriod(**row) for row in source["periods"])
    memberships = tuple(CardMembership(**row) for row in source["memberships"])
    accounting = tuple(RewardAccounting(**row) for row in source["accounting"])
    assessments = tuple(
        CategoryAssessment(**row) for row in source["category_assessments"]
    )
    snapshots = tuple(FxSnapshot(**row) for row in source["fx_snapshots"])

    def _unique(values: Sequence[str], label: str) -> None:
        if len(set(values)) != len(values):
            raise ValueError(f"Cashback state contains duplicate {label}")

    _unique([item.receipt_id for item in receipts], "receipt identities")
    _unique([item.period_id for item in periods], "period identities")
    _unique([item.card_code for item in memberships], "card memberships")
    _unique([item.transaction_id for item in assessments], "category assessments")
    _unique([item.snapshot_id for item in snapshots], "FX snapshots")

    period_by_id = {item.period_id: item for item in periods}
    receipt_by_id = {item.receipt_id: item for item in receipts}
    for period in periods:
        if period.status != "CLOSED":
            continue
        receipt_id = period.closed_by_receipt_id
        receipt = receipt_by_id.get(receipt_id)
        if receipt is None:
            raise ValueError(
                f"Closed period {period.period_id} references an unknown receipt"
            )
        if receipt.period_id != period.period_id:
            raise ValueError(
                f"Closed period {period.period_id} receipt does not reference its period"
            )
    for card_code in {item.card_code for item in periods}:
        card_periods = sorted(
            (item for item in periods if item.card_code == card_code),
            key=lambda item: item.period_start,
        )
        for previous, current in zip(card_periods, card_periods[1:]):
            if current.period_start < previous.period_end:
                raise ValueError(f"Cashback periods overlap for card {card_code}")
    for receipt in receipts:
        if receipt.period_id is None:
            continue
        period = period_by_id.get(receipt.period_id)
        if period is None:
            raise ValueError(
                f"Receipt {receipt.receipt_id} references an unknown period"
            )
        if period.card_code != receipt.card_code:
            raise ValueError(
                f"Receipt {receipt.receipt_id} card does not match its period"
            )
        if (
            period.status != "CLOSED"
            or period.closed_by_receipt_id != receipt.receipt_id
        ):
            raise ValueError(f"Receipt {receipt.receipt_id} must close its period")
        if period.period_end != receipt.original_received_at:
            raise ValueError(
                f"Receipt {receipt.receipt_id} must close at its original received timestamp"
            )
    for item in accounting:
        period = period_by_id.get(item.period_id)
        if period is None:
            raise ValueError(
                f"Accounting references an unknown period {item.period_id}"
            )
        if period.card_code != item.card_code:
            raise ValueError(f"Accounting card does not match period {item.period_id}")
    for snapshot in snapshots:
        if snapshot.base_currency == snapshot.quote_currency:
            raise ValueError("FX snapshot base and quote currencies must differ")
