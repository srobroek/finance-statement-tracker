from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


def money(value: Decimal | str | int | float | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


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
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.amount_aed = money(self.amount_aed)
        if self.amount_original is not None:
            self.amount_original = money(self.amount_original)
        self.currency = self.currency.upper()
        self.card = self.card.upper()
        self.channel = self.channel.upper()

    @property
    def spend_aed(self) -> Decimal:
        amount = abs(self.amount_aed)
        return -amount if self.is_refund or self.transaction_type == "REFUND" else amount

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
            "metadata": self.metadata,
        }
