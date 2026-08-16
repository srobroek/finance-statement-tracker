from __future__ import annotations

import re
import calendar
from dataclasses import asdict, replace
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Iterable

from .cashback import PaymentIntent, bucket_spend, pace_status, recommend, reward_total, total_spend
from .models import Transaction
from .actual_pipeline import account_maps, account_owner_map


_TAG = re.compile(r"(?:^|\s)#([A-Za-z0-9_-]+)")
_CURRENCY = re.compile(r"(?:^|\s)currency:([A-Z]{3})(?:\s|$)", re.I)


def _tags(notes: str) -> set[str]:
    return {match.group(1) for match in _TAG.finditer(notes or "")}


def _plain(value: Decimal) -> str:
    return format(value, "f")


def _category_code(category: str | None, merchant: str) -> str:
    if "AMAZON" in merchant.upper():
        return "AMAZON"
    mapping = {
        "Groceries": "GROCERY",
        "Dining Out": "DINING",
        "Food Delivery": "DINING",
        "Flights": "AIRLINE",
        "Accommodation": "HOTEL",
        "Travel Transport": "TRAVEL",
        "Travel Activities": "TRAVEL",
        "Online Shopping": "GENERAL",
    }
    return mapping.get(category or "", "GENERAL")


def _channel(tags: set[str], merchant: str) -> str:
    for tag in tags:
        if tag.casefold().startswith("channel-"):
            return tag[8:].replace("-", "_").upper()
    upper = merchant.upper()
    if any(token in upper for token in ("AMAZON", "APPLE.COM/BILL", "DEWA", "EMPOWER")):
        return "ONLINE"
    return "UNKNOWN"


def _reward_bucket(card: str, category: str, channel: str, currency: str, tags: set[str]) -> str | None:
    for tag in tags:
        if tag.casefold().startswith("cashback-"):
            return tag[9:].replace("-", "_").upper()
    if card == "EI_AMAZON" and category == "AMAZON":
        return "EI_AMAZON"
    if card == "SC_PLATINUM_X":
        if currency != "AED":
            return "SC_FOREIGN"
        if channel == "APPLE_PAY_POS":
            return "SC_WALLET"
        if channel == "ONLINE":
            return "SC_ONLINE"
    if card == "RAK_WORLD":
        if category == "GROCERY":
            return "RAK_GROCERY"
        if category == "DINING":
            return "RAK_DINING"
        if category in {"TRAVEL", "HOTEL", "AIRLINE"}:
            return "RAK_TRAVEL"
        if channel == "APPLE_PAY_POS":
            return "RAK_EWALLET"
        return "RAK_STANDARD"
    return None


def transactions_from_actual_snapshot(
    snapshot: dict[str, Any],
    config: dict[str, Any],
) -> list[Transaction]:
    _, account_by_card = account_maps(config)
    owner_by_card = account_owner_map(config)
    card_by_account = {name.casefold(): card for card, name in account_by_card.items()}
    for account in config["accounts"]:
        card = str(account.get("card_code") or account["name"]).upper()
        for alias in account.get("aliases", []):
            card_by_account[str(alias).casefold()] = card

    result: list[Transaction] = []
    for row in snapshot.get("transactions", []):
        account_name = str(row["account_name"])
        card = card_by_account.get(account_name.casefold())
        if card is None:
            continue
        notes = str(row.get("notes") or "")
        tags = _tags(notes)
        merchant = str(row.get("imported_payee") or row.get("payee_name") or "Unknown")
        currency_match = _CURRENCY.search(notes)
        currency = currency_match.group(1).upper() if currency_match else "AED"
        category = _category_code(row.get("category_name"), merchant)
        channel = _channel(tags, merchant)
        amount_minor = int(row["amount"])
        transfer = bool(row.get("transfer_id"))
        card_payment = row.get("category_name") == "Card Payments" or any(
            tag.casefold() == "card-payment" for tag in tags
        )
        income_category = row.get("category_name") in {"Cashback & Rewards", "Other Income"}
        transaction_type = (
            "TRANSFER" if transfer or card_payment else
            "REWARD_CREDIT" if amount_minor > 0 and income_category else
            "REFUND" if amount_minor > 0 else
            "PURCHASE"
        )
        result.append(
            Transaction(
                transaction_id=str(row.get("imported_id") or f"actual:{row['id']}"),
                transaction_at=datetime.combine(date.fromisoformat(str(row["date"])), time.min),
                card=card,
                account=account_name,
                owner=owner_by_card.get(card),
                merchant_raw=merchant,
                vendor=row.get("payee_name"),
                amount_aed=Decimal(abs(amount_minor)) / Decimal("100"),
                currency=currency,
                channel=channel,
                source_type="actual_snapshot",
                category=category,
                subcategory=row.get("category_name"),
                transaction_type=transaction_type,
                reward_bucket=_reward_bucket(card, category, channel, currency, tags),
                tags={
                    tag
                    for tag in tags
                    if not tag.casefold().startswith(("channel-", "cashback-", "owner-"))
                },
                review_required=row.get("category_name") is None or bool({"review", "needs-review"} & tags),
                is_refund=transaction_type == "REFUND",
                metadata={
                    "actual_id": row["id"],
                    "cleared": bool(row.get("cleared")),
                    "reconciled": bool(row.get("reconciled")),
                },
            )
        )
    return result


def cashback_dashboard(
    programs: Iterable[Any],
    transactions: Iterable[Transaction],
    as_of: date,
    intents: Iterable[PaymentIntent],
    periods_by_card: dict[str, tuple[date, date]] | None = None,
) -> dict[str, object]:
    rows = list(transactions)
    routing_programs = []
    program_rows = []
    alerts = []
    for program in programs:
        period = (periods_by_card or {}).get(program.card)
        card_transactions = [
            row
            for row in rows
            if row.card == program.card
            and (
                period is None
                or period[0] <= row.transaction_at.date() <= period[1]
            )
        ]
        spend = total_spend(card_transactions, program.card)
        buckets = bucket_spend(card_transactions, program.card)
        target_tier = program.tier_for(program.safety_target or spend)
        bucket_rows = []
        for bucket in program.buckets:
            bucket_actual = buckets.get(bucket.code, Decimal("0"))
            rate = target_tier.rates.get(bucket.code, Decimal("0"))
            spend_cap = bucket.spend_cap_aed
            if spend_cap is None:
                spend_cap = None if bucket.cap_aed is None or rate <= 0 else bucket.cap_aed / rate
            bucket_ratio = None if spend_cap in (None, Decimal("0")) else bucket_actual / spend_cap
            bucket_status = (
                "FULL" if bucket_ratio is not None and bucket_ratio >= 1
                else "NEAR_FULL" if bucket_ratio is not None and bucket_ratio >= Decimal("0.90")
                else "OPEN"
            )
            bucket_rows.append({
                "code": bucket.code,
                "spend_aed": _plain(bucket_actual),
                "spend_cap_aed": None if spend_cap is None else _plain(spend_cap),
                "headroom_aed": None if spend_cap is None else _plain(max(spend_cap - bucket_actual, Decimal("0"))),
                "status": bucket_status,
            })
            if bucket_status in {"FULL", "NEAR_FULL"}:
                alerts.append({
                    "key": f"bucket:{program.card}:{bucket.code}:{bucket_status.lower()}",
                    "severity": "warning" if bucket_status == "NEAR_FULL" else "critical",
                    "title": f"{program.name} {bucket.code.replace('_', ' ').title()} is {bucket_status.replace('_', ' ').lower()}",
                    "detail": (
                        "Route new eligible spend to the next recommended card."
                        if bucket_status == "FULL"
                        else "Headroom is below 10%; check routing before the next large payment."
                    ),
                })
        pace = None
        routing_mode = "CURRENT_TIER"
        if program.safety_target is not None:
            period_start, period_end = period or (
                as_of.replace(day=1),
                as_of.replace(day=calendar.monthrange(as_of.year, as_of.month)[1]),
            )
            elapsed_days = (as_of - period_start).days + 1
            cycle_days = (period_end - period_start).days + 1
            pace = asdict(
                pace_status(
                    spend,
                    program.safety_target,
                    as_of,
                    period_start,
                    period_end,
                )
            )
            pace = {key: _plain(value) if isinstance(value, Decimal) else value for key, value in pace.items()}
            projected = spend / Decimal(max(elapsed_days, 1)) * Decimal(cycle_days)
            if elapsed_days < 21 or pace["status"] != "UNDER" or projected >= program.safety_target:
                routing_mode = "TARGET_TIER"
                routing_programs.append(program)
            else:
                routing_programs.append(replace(program, safety_target=None))
            if elapsed_days >= 21 and spend < program.safety_target:
                alerts.append({
                    "key": f"minimum:{program.card}:{period_start}:{period_end}",
                    "severity": "warning",
                    "title": f"{program.name} minimum is at risk",
                    "detail": f"AED {_plain(program.safety_target - spend)} remains after the third week of the cycle.",
                })
        else:
            routing_programs.append(program)
        program_rows.append({
            "card": program.card,
            "name": program.name,
            "programme_version": program.programme_version,
            "effective_start": None if program.effective_start is None else program.effective_start.isoformat(),
            "effective_end": None if program.effective_end is None else program.effective_end.isoformat(),
            "statement_close_day": program.statement_close_day,
            "reward_cycle_basis": program.reward_cycle_basis,
            "payment_due_forecast_days": program.payment_due_forecast_days,
            "period_start": None if period is None else period[0].isoformat(),
            "period_end": None if period is None else period[1].isoformat(),
            "total_spend_aed": _plain(spend),
            "safety_target_aed": None if program.safety_target is None else _plain(program.safety_target),
            "tier": program.tier_for(spend).code,
            "expected_cashback_aed": _plain(reward_total(program, spend, buckets)),
            "tiers": [
                {
                    "code": tier.code,
                    "minimum_spend_aed": _plain(tier.minimum_spend),
                    "met": spend >= tier.minimum_spend,
                    "remaining_aed": _plain(max(tier.minimum_spend - spend, Decimal("0"))),
                }
                for tier in program.tiers
            ],
            "routing_mode": routing_mode,
            "pace": pace,
            "provisional_event_count": sum(
                row.metadata.get("cashback_status") == "PROVISIONAL" for row in card_transactions
            ),
            "confirmed_event_count": sum(
                row.metadata.get("cashback_status") == "CONFIRMED" for row in card_transactions
            ),
            "refund_effect_aed": _plain(
                sum((-row.spend_aed for row in card_transactions if row.spend_aed < 0), Decimal("0"))
            ),
            "buckets": bucket_rows,
        })
    recommendations = []
    for intent in intents:
        item = recommend(routing_programs, rows, intent)
        recommendations.append({
            "purchase_type": intent.category,
            "channel": intent.channel,
            "currency": intent.currency,
            "use_card": item.primary_card,
            "avoid_cards": list(item.avoid_cards),
            "guidance": item.guidance,
            "reason": item.reason,
        })
    return {
        "schema_version": 1,
        "as_of": as_of.isoformat(),
        "cards": program_rows,
        "recommendations": recommendations,
        "alerts": alerts,
        "review_count": sum(row.review_required for row in rows),
    }
