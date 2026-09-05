from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_CEILING
from statistics import median
from typing import Any, Iterable


EXCLUDED_BUDGET_CATEGORIES = frozenset(
    {
        "",
        "Card Payments",
        "Cashback & Rewards",
        "Income",
        "Investments",
        "Needs Review",
        "Refunds",
        "Salary",
        "Transfers",
    }
)


def _month(value: str) -> str:
    return str(value)[:7]


def _round_up_minor(value: int, increment_minor: int) -> int:
    if increment_minor <= 0:
        raise ValueError("increment_minor must be positive")
    return int((Decimal(value) / increment_minor).to_integral_value(rounding=ROUND_CEILING)) * increment_minor


def _eligible_expense(row: dict[str, Any]) -> bool:
    return (
        not row.get("tombstone")
        and not row.get("is_parent")
        and not row.get("transfer_id")
        and int(row.get("amount") or 0) < 0
        and str(row.get("category_name") or "") not in EXCLUDED_BUDGET_CATEGORIES
    )


def recommend_category_budgets(
    transactions: Iterable[dict[str, Any]],
    *,
    as_of: date,
    lookback_months: int = 12,
    minimum_months: int = 3,
    buffer_percent: Decimal = Decimal("10"),
    round_to_minor: int = 5000,
) -> list[dict[str, Any]]:
    """Recommend conservative monthly envelope amounts from completed months only."""
    if lookback_months < 1 or minimum_months < 1:
        raise ValueError("lookback_months and minimum_months must be positive")
    rows = list(transactions)
    current_month = as_of.isoformat()[:7]
    months = sorted(
        {
            _month(str(row.get("date") or ""))
            for row in rows
            if _eligible_expense(row) and _month(str(row.get("date") or "")) < current_month
        }
    )[-lookback_months:]
    by_category_month: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        if not _eligible_expense(row):
            continue
        month = _month(str(row.get("date") or ""))
        if month not in months:
            continue
        by_category_month[str(row.get("category_name") or "")][month] += abs(int(row["amount"]))

    recommendations: list[dict[str, Any]] = []
    multiplier = Decimal("1") + buffer_percent / Decimal("100")
    for category, month_values in by_category_month.items():
        active = [value for value in month_values.values() if value > 0]
        if len(active) < minimum_months:
            continue
        observed = [month_values.get(month, 0) for month in months]
        nonzero_median = int(median(active))
        recommended = _round_up_minor(int(Decimal(nonzero_median) * multiplier), round_to_minor)
        active_ratio = Decimal(len(active)) / Decimal(max(1, len(months)))
        volatility = Decimal(max(active) - min(active)) / Decimal(max(1, nonzero_median))
        automation = "fixed"
        if active_ratio >= Decimal("0.75") and volatility >= Decimal("0.75"):
            automation = "refill-to-cap"
        recommendations.append(
            {
                "category": category,
                "recommended_minor": recommended,
                "median_active_month_minor": nonzero_median,
                "mean_all_months_minor": round(sum(observed) / len(months)),
                "active_months": len(active),
                "observed_months": len(months),
                "active_ratio": str(active_ratio.quantize(Decimal("0.01"))),
                "volatility_ratio": str(volatility.quantize(Decimal("0.01"))),
                "automation": automation,
                "confidence": "high" if len(active) >= 6 else "medium",
            }
        )
    return sorted(recommendations, key=lambda item: (-item["recommended_minor"], item["category"]))


@dataclass(frozen=True, slots=True)
class SchedulePolicy:
    name: str
    payee_names: tuple[str, ...]
    category_names: tuple[str, ...] = ()
    minimum_months: int = 3
    amount_mode: str = "between"
    posts_transaction: bool = False


def recommend_schedules(
    transactions: Iterable[dict[str, Any]],
    policies: Iterable[SchedulePolicy],
    *,
    as_of: date,
) -> list[dict[str, Any]]:
    """Infer reviewable monthly schedule ranges from allowlisted recurring families."""
    rows = [row for row in transactions if _eligible_expense(row)]
    results: list[dict[str, Any]] = []
    for policy in policies:
        payees = {name.casefold() for name in policy.payee_names}
        categories = {name.casefold() for name in policy.category_names}
        matched = [
            row
            for row in rows
            if str(row.get("payee_name") or row.get("imported_payee") or "").casefold() in payees
            or (categories and str(row.get("category_name") or "").casefold() in categories)
        ]
        by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in matched:
            by_account[str(row.get("account_name") or row.get("account") or "")].append(row)
        for account, account_rows in by_account.items():
            months = sorted({_month(row["date"]) for row in account_rows})
            if len(months) < policy.minimum_months:
                continue
            latest_by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in account_rows:
                latest_by_month[_month(row["date"])].append(row)
            monthly_amounts = [sum(abs(int(row["amount"])) for row in latest_by_month[month]) for month in months]
            days = sorted(int(row["date"][8:10]) for row in account_rows)
            median_day = int(median(days))
            next_year = as_of.year + (1 if as_of.month == 12 else 0)
            next_month = 1 if as_of.month == 12 else as_of.month + 1
            next_date = date(next_year, next_month, min(median_day, 28)).isoformat()
            minimum = min(monthly_amounts)
            maximum = max(monthly_amounts)
            median_amount = int(median(monthly_amounts))
            results.append(
                {
                    "name": f"{policy.name} - {account}",
                    "account": account,
                    "payee": policy.payee_names[0],
                    "date": {
                        "frequency": "monthly",
                        "interval": 1,
                        "start": next_date,
                        "endMode": "never",
                    },
                    "amount_op": "isbetween" if policy.amount_mode == "between" else "isapprox",
                    "amount_min_minor": minimum,
                    "amount_max_minor": maximum,
                    "amount_minor": median_amount,
                    "posts_transaction": policy.posts_transaction,
                    "evidence": {
                        "months": len(months),
                        "first_month": months[0],
                        "last_month": months[-1],
                        "median_day": median_day,
                        "min_minor": minimum,
                        "median_minor": median_amount,
                        "max_minor": maximum,
                    },
                }
            )
    return sorted(results, key=lambda item: item["name"])
