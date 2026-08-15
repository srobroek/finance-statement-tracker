from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from .models import Transaction


_NOTION_TRANSACTION_TYPES = {
    "PURCHASE": "PURCHASE",
    "REFUND": "REFUND",
    "PAYMENT": "TRANSFER",
    "TRANSFER": "TRANSFER",
    "REWARD_CREDIT": "INCOME",
    "INCOME": "INCOME",
    "FEE": "FEE",
}

_EVIDENCE_STATUSES = {
    "NOT_REQUESTED": "Not started",
    "REQUESTED": "Not started",
    "IN_PROGRESS": "In progress",
    "DONE": "Done",
}


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def signed_amount(transaction: Transaction) -> Decimal:
    """Return a ledger amount suitable for Notion sums and charts."""
    amount = abs(transaction.amount_aed)
    if transaction.transaction_type in {"REFUND", "PAYMENT", "REWARD_CREDIT"}:
        return -amount
    return amount


def decision_hash(transaction: Transaction) -> str:
    payload = {
        "transaction_id": transaction.transaction_id,
        "card": transaction.card,
        "vendor": transaction.vendor,
        "category": transaction.category,
        "subcategory": transaction.subcategory,
        "tags": sorted(transaction.tags),
        "evidence_policy": transaction.evidence_policy,
        "review_required": transaction.review_required,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def transaction_properties(
    transaction: Transaction,
    *,
    received_at: datetime | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Serialize one canonical transaction to the current Notion schema."""
    notion_type = _NOTION_TRANSACTION_TYPES.get(transaction.transaction_type, "PURCHASE")
    properties: dict[str, Any] = {
        "Description": transaction.vendor or transaction.merchant_raw,
        "Transaction ID": transaction.transaction_id,
        "date:Transaction At:start": transaction.transaction_at.isoformat(),
        "date:Transaction At:is_datetime": 1,
        "Amount AED": _number(signed_amount(transaction)),
        "Card Code": transaction.card,
        "Merchant Raw": transaction.merchant_raw,
        "Currency": transaction.currency,
        "Channel": transaction.channel,
        "Source Type": transaction.source_type,
        "Transaction Type": notion_type,
        "Is Refund": "__YES__" if transaction.is_refund else "__NO__",
        "Reconciled": "__NO__",
        "Review Required": "__YES__" if transaction.review_required else "__NO__",
        "Evidence Status": _EVIDENCE_STATUSES.get(transaction.evidence_status, "Not started"),
        "Decision Hash": decision_hash(transaction),
    }
    optional = {
        "Source Message ID": transaction.source_message_id,
        "Vendor": transaction.vendor,
        "Category": transaction.category,
        "Subcategory": transaction.subcategory,
        "Reward Bucket": transaction.reward_bucket,
        "Evidence Policy": transaction.evidence_policy,
        "Amount Original": _number(transaction.amount_original),
        "Notes": notes,
    }
    properties.update({key: value for key, value in optional.items() if value is not None})
    if transaction.tags:
        properties["Tags"] = sorted(transaction.tags)
    if received_at is not None:
        properties["date:Received At:start"] = received_at.isoformat()
        properties["date:Received At:is_datetime"] = 1
    return properties


def card_period_properties(
    *,
    title: str,
    card_page_url: str,
    period: str,
    period_start: str | None,
    period_end: str | None,
    statement_date: str | None,
    payment_due_date: str | None,
    actual_spend_aed: Decimal,
) -> dict[str, Any]:
    """Serialize a provisional, statement-received card period."""
    properties: dict[str, Any] = {
        "Card Month": title,
        "Card": [card_page_url],
        "Period": period,
        "Actual Spend AED": float(actual_spend_aed),
        "Statement Status": "RECEIVED",
        "Reconciliation Status": "NOT_STARTED",
        "Cashback Finalized": "__NO__",
        "Cashback Source": "LIVE_TRANSACTIONS",
        "Month Close Eligible": "__NO__",
        "Payment Status": "NOT_CONFIGURED",
    }
    for property_name, value in (
        ("Period Start", period_start),
        ("Period End", period_end),
        ("Statement Date", statement_date),
        ("Payment Due Date", payment_due_date),
    ):
        if value:
            properties[f"date:{property_name}:start"] = value
            properties[f"date:{property_name}:is_datetime"] = 0
    return properties
