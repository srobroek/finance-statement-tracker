from __future__ import annotations

import calendar
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from .models import Transaction, money


@dataclass(frozen=True, slots=True)
class RewardTier:
    code: str
    minimum_spend: Decimal
    rates: dict[str, Decimal]


@dataclass(frozen=True, slots=True)
class RewardBucket:
    code: str
    cap_aed: Decimal | None
    categories: frozenset[str] = frozenset()
    channels: frozenset[str] = frozenset()
    currencies: frozenset[str] = frozenset()
    excluded_categories: frozenset[str] = frozenset()
    excluded_channels: frozenset[str] = frozenset()
    foreign_only: bool = False
    aed_only: bool = False
    spend_cap_aed: Decimal | None = None

    def eligible(self, category: str, channel: str, currency: str) -> bool:
        if category.upper() in self.excluded_categories:
            return False
        if channel.upper() in self.excluded_channels:
            return False
        if self.categories and category.upper() not in self.categories:
            return False
        if self.channels and channel.upper() not in self.channels:
            return False
        if self.currencies and currency.upper() not in self.currencies:
            return False
        if self.foreign_only and currency.upper() == "AED":
            return False
        if self.aed_only and currency.upper() != "AED":
            return False
        return True


@dataclass(frozen=True, slots=True)
class CardProgram:
    card: str
    name: str
    safety_target: Decimal | None
    tiers: tuple[RewardTier, ...]
    buckets: tuple[RewardBucket, ...]
    fx_cost_rate: Decimal = Decimal("0")
    programme_version: str = "1"
    effective_start: date | None = None
    effective_end: date | None = None
    statement_close_day: int | str = "LAST_DAY"
    statement_ingest_delay_days: int = 1
    reward_cycle_basis: str = "STATEMENT_CYCLE"
    payment_due_forecast_days: int = 30
    refund_behavior: str = "REDUCE_CURRENT_CYCLE"
    rounding_behavior: str = "CURRENCY_MINOR_UNIT"
    routing_priority: int = 100
    exclusions: tuple[str, ...] = ()

    def tier_for(self, total_spend: Decimal) -> RewardTier:
        eligible = [tier for tier in self.tiers if total_spend >= tier.minimum_spend]
        return max(eligible, key=lambda tier: tier.minimum_spend) if eligible else min(
            self.tiers, key=lambda tier: tier.minimum_spend
        )


@dataclass(frozen=True, slots=True)
class PaymentIntent:
    category: str
    amount_aed: Decimal
    currency: str = "AED"
    channel: str = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CardValue:
    card: str
    bucket: str
    marginal_reward_aed: Decimal
    strategic_reward_aed: Decimal
    estimated_cost_aed: Decimal
    net_value_aed: Decimal
    tier_before: str
    tier_after: str


@dataclass(frozen=True, slots=True)
class Recommendation:
    category: str
    primary_card: str
    primary_bucket: str
    alternative_card: str | None
    avoid_cards: tuple[str, ...]
    urgency: str
    guidance: str
    net_value_aed: Decimal
    reason: str
    ranked: tuple[CardValue, ...]


@dataclass(frozen=True, slots=True)
class PaceStatus:
    actual_aed: Decimal
    safety_target_aed: Decimal
    expected_to_date_aed: Decimal
    variance_aed: Decimal
    status: str


def _period_transactions(transactions: Iterable[Transaction], card: str) -> list[Transaction]:
    return [transaction for transaction in transactions if transaction.card == card]


def total_spend(transactions: Iterable[Transaction], card: str) -> Decimal:
    return sum(
        (
            transaction.spend_aed
            for transaction in transactions
            if transaction.card == card and transaction.transaction_type in {"PURCHASE", "REFUND"}
        ),
        Decimal("0"),
    )


def bucket_spend(transactions: Iterable[Transaction], card: str) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for transaction in transactions:
        if (
            transaction.card != card
            or transaction.transaction_type not in {"PURCHASE", "REFUND"}
            or not transaction.reward_bucket
        ):
            continue
        result[transaction.reward_bucket] = result.get(transaction.reward_bucket, Decimal("0")) + transaction.spend_aed
    return result


def reward_total(program: CardProgram, total: Decimal, buckets: dict[str, Decimal]) -> Decimal:
    tier = program.tier_for(total)
    bucket_defs = {bucket.code: bucket for bucket in program.buckets}
    reward = Decimal("0")
    for code, spend in buckets.items():
        rate = tier.rates.get(code, Decimal("0"))
        earned = max(spend, Decimal("0")) * rate
        cap = bucket_defs.get(code).cap_aed if code in bucket_defs else None
        reward += min(earned, cap) if cap is not None else earned
    if program.rounding_behavior == "CURRENCY_MINOR_UNIT":
        return reward.quantize(Decimal("0.01"))
    if program.rounding_behavior != "NONE":
        raise ValueError(f"Unsupported reward rounding behavior: {program.rounding_behavior}")
    return reward


def evaluate_card(program: CardProgram, transactions: Iterable[Transaction], intent: PaymentIntent) -> CardValue | None:
    existing = list(transactions)
    current_total = total_spend(existing, program.card)
    current_buckets = bucket_spend(existing, program.card)
    eligible = [bucket for bucket in program.buckets if bucket.eligible(intent.category, intent.channel, intent.currency)]
    if not eligible:
        return None
    before_reward = reward_total(program, current_total, current_buckets)
    best: CardValue | None = None
    for bucket in eligible:
        after_buckets = dict(current_buckets)
        after_buckets[bucket.code] = after_buckets.get(bucket.code, Decimal("0")) + money(intent.amount_aed)
        after_total = current_total + money(intent.amount_aed)
        after_reward = reward_total(program, after_total, after_buckets)
        target_total = program.safety_target if program.safety_target is not None else after_total
        target_tier = program.tier_for(target_total)
        target_rate = target_tier.rates.get(bucket.code, Decimal("0"))
        current_bucket_spend = max(current_buckets.get(bucket.code, Decimal("0")), Decimal("0"))
        if bucket.spend_cap_aed is not None:
            spend_capacity = bucket.spend_cap_aed
            eligible_progress_spend = min(
                money(intent.amount_aed),
                max(spend_capacity - current_bucket_spend, Decimal("0")),
            )
        elif bucket.cap_aed is not None and target_rate > 0:
            spend_capacity = bucket.cap_aed / target_rate
            eligible_progress_spend = min(
                money(intent.amount_aed),
                max(spend_capacity - current_bucket_spend, Decimal("0")),
            )
        else:
            eligible_progress_spend = money(intent.amount_aed)
        strategic_reward = eligible_progress_spend * target_rate
        actual_marginal_reward = after_reward - before_reward
        decision_reward = max(actual_marginal_reward, strategic_reward)
        cost = money(intent.amount_aed) * program.fx_cost_rate if intent.currency.upper() != "AED" else Decimal("0")
        value = CardValue(
            card=program.card,
            bucket=bucket.code,
            marginal_reward_aed=actual_marginal_reward,
            strategic_reward_aed=strategic_reward,
            estimated_cost_aed=cost,
            net_value_aed=decision_reward - cost,
            tier_before=program.tier_for(current_total).code,
            tier_after=program.tier_for(after_total).code,
        )
        if best is None or value.net_value_aed > best.net_value_aed:
            best = value
    return best


def recommend(programs: Iterable[CardProgram], transactions: Iterable[Transaction], intent: PaymentIntent) -> Recommendation:
    program_list = tuple(programs)
    names = {program.card: program.name for program in program_list}
    values = [value for program in program_list if (value := evaluate_card(program, transactions, intent)) is not None]
    if not values:
        raise ValueError(f"No eligible card for {intent.category}/{intent.channel}/{intent.currency}")
    priorities = {program.card: program.routing_priority for program in program_list}
    ranked = tuple(
        sorted(
            values,
            key=lambda item: (item.net_value_aed, -priorities.get(item.card, 100), item.card),
            reverse=True,
        )
    )
    winner = ranked[0]
    alternative = ranked[1].card if len(ranked) > 1 else None
    avoid_cards = tuple(value.card for value in ranked[1:] if value.net_value_aed <= 0)
    urgency = "PREFER_NOW" if winner.net_value_aed >= 0 else "AVOID"
    friendly_card = names.get(winner.card, winner.card)
    guidance = f"Use {friendly_card} for {intent.category.replace('_', ' ').title()}."
    tier_note = (
        f" and moves {winner.card} from {winner.tier_before} to {winner.tier_after}"
        if winner.tier_before != winner.tier_after
        else ""
    )
    reason = (
        f"{winner.card} has the highest estimated marginal value of AED "
        f"{winner.net_value_aed.quantize(Decimal('0.01'))}{tier_note}."
    )
    return Recommendation(
        category=intent.category,
        primary_card=winner.card,
        primary_bucket=winner.bucket,
        alternative_card=alternative,
        avoid_cards=avoid_cards,
        urgency=urgency,
        guidance=guidance,
        net_value_aed=winner.net_value_aed,
        reason=reason,
        ranked=ranked,
    )


def statement_period(as_of: date, close_day: int | str = "LAST_DAY") -> tuple[date, date]:
    """Return the statement-cycle boundaries containing ``as_of``."""

    def close_for(year: int, month: int) -> date:
        last_day = calendar.monthrange(year, month)[1]
        if str(close_day).upper() == "LAST_DAY":
            day = last_day
        else:
            try:
                configured = int(close_day)
            except (TypeError, ValueError) as error:
                raise ValueError("statement close_day must be LAST_DAY or an integer from 1 to 31") from error
            if configured < 1 or configured > 31:
                raise ValueError("statement close_day must be LAST_DAY or an integer from 1 to 31")
            day = min(configured, last_day)
        return date(year, month, day)

    this_close = close_for(as_of.year, as_of.month)
    if as_of <= this_close:
        period_end = this_close
        previous_month = (as_of.replace(day=1) - timedelta(days=1))
        previous_end = close_for(previous_month.year, previous_month.month)
    else:
        next_month = (as_of.replace(day=28) + timedelta(days=4)).replace(day=1)
        period_end = close_for(next_month.year, next_month.month)
        previous_end = this_close
    return previous_end + timedelta(days=1), period_end


def pace_status(
    actual: Decimal,
    safety_target: Decimal,
    as_of: date,
    period_start: date | None = None,
    period_end: date | None = None,
) -> PaceStatus:
    start = period_start or as_of.replace(day=1)
    end = period_end or as_of.replace(day=calendar.monthrange(as_of.year, as_of.month)[1])
    if not start <= as_of <= end:
        raise ValueError("as_of must fall inside the cashback period")
    days = (end - start).days + 1
    elapsed = (as_of - start).days + 1
    expected = money(safety_target) * Decimal(elapsed) / Decimal(days)
    variance = money(actual) - expected
    threshold = max(money(safety_target) * Decimal("0.05"), Decimal("250"))
    if actual >= safety_target:
        status = "SECURED"
    elif variance < -threshold:
        status = "UNDER"
    elif variance > threshold:
        status = "OVER"
    else:
        status = "ON_PACE"
    return PaceStatus(money(actual), money(safety_target), expected, variance, status)


def _optional_decimal(value: object) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))


def _iso_date(value: object) -> date | None:
    return None if value in (None, "") else date.fromisoformat(str(value))


def programs_from_config(
    source: dict[str, object],
    period_date: date | None = None,
) -> tuple[CardProgram, ...]:
    programs = []
    for item in source.get("programs", []):
        effective_start = _iso_date(item.get("effective_start") or source.get("effective_from"))
        effective_end = _iso_date(item.get("effective_end") or source.get("effective_end"))
        if effective_start and effective_end and effective_end < effective_start:
            raise ValueError(f"Cashback program {item.get('card')} has an invalid effective range")
        if period_date is not None and (
            (effective_start is not None and period_date < effective_start)
            or (effective_end is not None and period_date > effective_end)
        ):
            continue
        tiers = tuple(
            RewardTier(
                code=str(tier["code"]),
                minimum_spend=Decimal(str(tier["minimum_spend_aed"])),
                rates={code: Decimal(str(rate)) for code, rate in tier.get("rates", {}).items()},
            )
            for tier in item.get("tiers", [])
        )
        buckets = tuple(
            RewardBucket(
                code=str(bucket["code"]),
                cap_aed=_optional_decimal(bucket.get("cashback_cap_aed")),
                categories=frozenset(str(value).upper() for value in bucket.get("categories", [])),
                channels=frozenset(str(value).upper() for value in bucket.get("channels", [])),
                currencies=frozenset(str(value).upper() for value in bucket.get("currencies", [])),
                excluded_categories=frozenset(
                    str(value).upper() for value in bucket.get("excluded_categories", [])
                ),
                excluded_channels=frozenset(
                    str(value).upper() for value in bucket.get("excluded_channels", [])
                ),
                foreign_only=bool(bucket.get("foreign_only", False)),
                aed_only=bool(bucket.get("aed_only", False)),
                spend_cap_aed=_optional_decimal(bucket.get("spend_cap_aed")),
            )
            for bucket in item.get("buckets", [])
        )
        if not tiers or not buckets:
            raise ValueError(f"Cashback program {item.get('card')} must define tiers and buckets")
        programs.append(
            CardProgram(
                card=str(item["card"]),
                name=str(item["name"]),
                safety_target=_optional_decimal(item.get("safety_target_aed")),
                tiers=tiers,
                buckets=buckets,
                fx_cost_rate=Decimal(str(item.get("fx_cost_rate") or "0")),
                programme_version=str(item.get("programme_version") or "1"),
                effective_start=effective_start,
                effective_end=effective_end,
                statement_close_day=item.get("statement_cycle", {}).get("close_day", "LAST_DAY"),
                statement_ingest_delay_days=int(
                    item.get("statement_cycle", {}).get("ingest_delay_days", 1)
                ),
                reward_cycle_basis=str(
                    item.get("reward_cycle", {}).get("basis", "STATEMENT_CYCLE")
                ).upper(),
                payment_due_forecast_days=int(
                    item.get("payment_due", {}).get("forecast_offset_days", 30)
                ),
                refund_behavior=str(item.get("refund_behavior") or "REDUCE_CURRENT_CYCLE").upper(),
                rounding_behavior=str(item.get("rounding_behavior") or "CURRENCY_MINOR_UNIT").upper(),
                routing_priority=int(item.get("routing_priority", 100)),
                exclusions=tuple(str(value) for value in item.get("exclusions", [])),
            )
        )
    codes = [program.card for program in programs]
    if not programs:
        when = f" for {period_date.isoformat()}" if period_date else ""
        raise ValueError(f"Cashback configuration contains no active programs{when}")
    if len(set(codes)) != len(codes):
        raise ValueError(
            "Cashback configuration has overlapping versions for a card; select an unambiguous period"
        )
    return tuple(programs)


def payment_intents_from_config(source: dict[str, object]) -> tuple[PaymentIntent, ...]:
    return tuple(
        PaymentIntent(
            category=str(item["category"]),
            amount_aed=Decimal(str(item.get("decision_amount_aed") or "100")),
            currency=str(item.get("currency") or "AED"),
            channel=str(item.get("channel") or "UNKNOWN"),
        )
        for item in source.get("payment_intents", [])
    )


def load_program_configuration(path: Path | None = None) -> dict[str, object]:
    resolved = path or Path(__file__).resolve().parent.parent / "config" / "cashback-programs.json"
    source = json.loads(resolved.read_text(encoding="utf-8"))
    if int(source.get("schema_version", 0)) != 1:
        raise ValueError("Unsupported cashback program schema version")
    return source


def poc_programs(period_date: date | None = None) -> tuple[CardProgram, ...]:
    """Load the versioned POC programme assumptions; verify before production use."""
    return programs_from_config(load_program_configuration(), period_date)
