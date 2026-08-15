from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from .models import Transaction


@dataclass(frozen=True, slots=True)
class MonthCloseGate:
    eligible: bool
    status: str
    missing_statements: tuple[str, ...] = ()
    grace_period_exceeded: bool = False


def evaluate_month_close(
    as_of: date,
    period_end: date,
    active_cards: Iterable[str],
    statement_status_by_card: dict[str, str],
    grace_days: int = 5,
) -> MonthCloseGate:
    """Keep a period open until it has ended and every required statement is in."""
    if as_of <= period_end:
        return MonthCloseGate(False, "WAITING_FOR_PERIOD_END")
    ready = {"RECEIVED", "NOT_REQUIRED"}
    missing = tuple(
        sorted(
            card
            for card in active_cards
            if statement_status_by_card.get(card, "NOT_CONFIGURED").upper() not in ready
        )
    )
    if missing:
        return MonthCloseGate(
            False,
            "WAITING_FOR_STATEMENTS",
            missing,
            (as_of - period_end).days > grace_days,
        )
    return MonthCloseGate(True, "READY")


def _label(value: str) -> str:
    return value.replace('"', "'").replace("\n", " ").strip()


def month_category_totals(transactions: Iterable[Transaction], month: str) -> dict[str, Decimal]:
    year, month_number = (int(part) for part in month.split("-", 1))
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for transaction in transactions:
        when = transaction.transaction_at
        if when.year != year or when.month != month_number or transaction.spend_aed <= 0:
            continue
        category = transaction.category or "Uncategorised"
        totals[category] += transaction.spend_aed
    return dict(sorted(totals.items(), key=lambda item: item[1], reverse=True))


def month_close_markdown(transactions: Iterable[Transaction], month: str) -> str:
    totals = month_category_totals(transactions, month)
    generated = date.today().isoformat()
    pie_lines = [f'    "{_label(category)}" : {amount.quantize(Decimal("0.01"))}' for category, amount in totals.items()]
    if not pie_lines:
        pie_lines = ['    "No spend" : 1']
    table_lines = ["| Category | Spend (AED) |", "|---|---:|"]
    table_lines.extend(f"| {category} | {amount.quantize(Decimal('0.01'))} |" for category, amount in totals.items())
    return "\n".join(
        [
            f"# Month close: {month}",
            "",
            f"Generated {generated}. This page is a static close snapshot; later transaction corrections require regeneration.",
            "",
            "```mermaid",
            "pie showData",
            f'    title Spending by category — {month}',
            *pie_lines,
            "```",
            "",
            *table_lines,
            "",
        ]
    )
