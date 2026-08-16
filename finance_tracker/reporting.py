from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Literal

from .models import Transaction


@dataclass(frozen=True, slots=True)
class ReportFilter:
    start: date | None = None
    end: date | None = None
    accounts: frozenset[str] = frozenset()
    owners: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset()
    vendors: frozenset[str] = frozenset()
    transaction_types: frozenset[str] = frozenset()
    tags_any: frozenset[str] = frozenset()
    tags_all: frozenset[str] = frozenset()
    tags_none: frozenset[str] = frozenset()
    tags: frozenset[str] = frozenset()

    def matches(self, transaction: Transaction) -> bool:
        when = transaction.transaction_at.date()
        if self.start and when < self.start:
            return False
        if self.end and when > self.end:
            return False
        checks = (
            (self.accounts, transaction.account or transaction.card),
            (self.owners, transaction.owner),
            (self.categories, transaction.category),
            (self.vendors, transaction.vendor),
            (self.transaction_types, transaction.transaction_type),
        )
        if any(values and value not in values for values, value in checks):
            return False
        transaction_tags = {tag.casefold() for tag in transaction.tags}
        required = {tag.casefold() for tag in (self.tags_all or self.tags)}
        any_tags = {tag.casefold() for tag in self.tags_any}
        excluded = {tag.casefold() for tag in self.tags_none}
        if required and not required.issubset(transaction_tags):
            return False
        if any_tags and not any_tags.intersection(transaction_tags):
            return False
        return not excluded.intersection(transaction_tags)


@dataclass(frozen=True, slots=True)
class Breakdown:
    key: str
    net_aed: Decimal
    spend_aed: Decimal
    count: int
    unique_vendors: int


Dimension = Literal["category", "subcategory", "vendor", "owner", "account", "transaction_type"]


def breakdown(
    transactions: Iterable[Transaction],
    *,
    dimension: Dimension,
    report_filter: ReportFilter = ReportFilter(),
) -> list[Breakdown]:
    rows: dict[str, list[Transaction]] = defaultdict(list)
    for transaction in transactions:
        if not report_filter.matches(transaction):
            continue
        if dimension == "account":
            key = transaction.account or transaction.card
        else:
            key = getattr(transaction, dimension) or "Unassigned"
        rows[str(key)].append(transaction)

    result = []
    for key, grouped in rows.items():
        net = sum((transaction.spend_aed for transaction in grouped), start=Decimal("0"))
        spend = sum((max(Decimal("0"), transaction.spend_aed) for transaction in grouped), start=Decimal("0"))
        vendors = {transaction.vendor for transaction in grouped if transaction.vendor}
        result.append(Breakdown(key, net, spend, len(grouped), len(vendors)))
    return sorted(result, key=lambda item: (-item.spend_aed, item.key.casefold()))
