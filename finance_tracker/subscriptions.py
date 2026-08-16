from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median
from typing import Iterable

from .models import Transaction


@dataclass(frozen=True, slots=True)
class RecurringCandidate:
    vendor: str
    frequency: str
    expected_amount_aed: Decimal
    last_paid: date
    occurrences: int
    confidence: Decimal


def _frequency(days: float) -> str | None:
    if 25 <= days <= 35:
        return "Monthly"
    if 80 <= days <= 100:
        return "Quarterly"
    if 350 <= days <= 380:
        return "Annual"
    return None


def detect_recurring_subscriptions(
    transactions: Iterable[Transaction],
    *,
    minimum_occurrences: int = 3,
) -> list[RecurringCandidate]:
    """Find non-utility recurring spend, including rule/AI subscription flags."""
    grouped: dict[str, list[Transaction]] = defaultdict(list)
    for transaction in transactions:
        category = (transaction.category or "").casefold()
        if category == "utilities" or transaction.spend_aed <= 0:
            continue
        if not transaction.is_subscription and category != "subscriptions":
            continue
        grouped[transaction.vendor or transaction.merchant_raw].append(transaction)

    candidates: list[RecurringCandidate] = []
    for vendor, rows in grouped.items():
        rows.sort(key=lambda item: item.transaction_at)
        if len(rows) < minimum_occurrences:
            continue
        intervals = [
            (right.transaction_at.date() - left.transaction_at.date()).days
            for left, right in zip(rows, rows[1:])
        ]
        cadence = _frequency(float(median(intervals)))
        if cadence is None:
            continue
        amounts = [abs(row.spend_aed) for row in rows]
        expected = sum(amounts, start=Decimal("0")) / Decimal(len(amounts))
        spread = max(amounts) - min(amounts)
        amount_score = max(Decimal("0"), Decimal("1") - spread / max(expected, Decimal("1")))
        interval_spread = Decimal(max(intervals) - min(intervals))
        cadence_score = max(Decimal("0"), Decimal("1") - interval_spread / Decimal("20"))
        confidence = (amount_score + cadence_score) / Decimal("2")
        candidates.append(
            RecurringCandidate(
                vendor,
                cadence,
                expected.quantize(Decimal("0.01")),
                rows[-1].transaction_at.date(),
                len(rows),
                confidence.quantize(Decimal("0.01")),
            )
        )
    return sorted(candidates, key=lambda item: (-item.confidence, item.vendor.casefold()))
