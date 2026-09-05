from __future__ import annotations

import calendar
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from jsonschema import Draft202012Validator, FormatChecker

from .models import Transaction, money
from .transaction_semantics import CASHBACK_TOPICS


@dataclass(frozen=True, slots=True)
class TierRequirement:
    metric: str
    operator: str
    value: Decimal
    bucket: str | None = None

    def met(self, total_spend: Decimal, buckets: dict[str, Decimal]) -> bool:
        actual = (
            total_spend
            if self.metric == "TOTAL_SPEND"
            else buckets.get(str(self.bucket), Decimal("0"))
        )
        return {
            "GTE": actual >= self.value,
            "GT": actual > self.value,
            "LTE": actual <= self.value,
            "LT": actual < self.value,
            "EQ": actual == self.value,
        }[self.operator]


@dataclass(frozen=True, slots=True)
class RewardTier:
    code: str
    minimum_spend: Decimal
    rates: dict[str, Decimal]
    cashback_caps_aed: dict[str, Decimal] = field(default_factory=dict)
    requirements: tuple[TierRequirement, ...] = ()

    def qualifies(self, total_spend: Decimal, buckets: dict[str, Decimal]) -> bool:
        return total_spend >= self.minimum_spend and all(
            requirement.met(total_spend, buckets) for requirement in self.requirements
        )

    def cashback_cap(self, bucket: str, fallback: Decimal | None) -> Decimal | None:
        return self.cashback_caps_aed.get(bucket, fallback)


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
    base_currency_only: bool = False
    base_currency: str = "AED"
    spend_cap_aed: Decimal | None = None
    assignment_categories: frozenset[str] = frozenset()
    assignment_channels: frozenset[str] = frozenset()
    assignment_currencies: frozenset[str] = frozenset()
    assignment_foreign_only: bool = False
    assignment_base_currency_only: bool = False
    assignment_fallback: bool = False

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
        if self.foreign_only and currency.upper() == self.base_currency:
            return False
        if self.base_currency_only and currency.upper() != self.base_currency:
            return False
        return True

    def matches_assignment(self, category: str, channel: str, currency: str) -> bool:
        if self.assignment_categories and category.upper() not in self.assignment_categories:
            return False
        if self.assignment_channels and channel.upper() not in self.assignment_channels:
            return False
        if self.assignment_currencies and currency.upper() not in self.assignment_currencies:
            return False
        if self.assignment_foreign_only and currency.upper() == self.base_currency:
            return False
        if self.assignment_base_currency_only and currency.upper() != self.base_currency:
            return False
        return bool(
            self.assignment_categories
            or self.assignment_channels
            or self.assignment_currencies
            or self.assignment_foreign_only
            or self.assignment_base_currency_only
        )


@dataclass(frozen=True, slots=True)
class PacePolicy:
    basis: str = "WEEKLY"
    routing_basis: str = "CYCLE"
    week_length_days: int = 7
    tolerance_ratio: Decimal = Decimal("0.05")
    minimum_tolerance_aed: Decimal = Decimal("250")


@dataclass(frozen=True, slots=True)
class AlertPolicy:
    bucket_near_full_ratio: Decimal = Decimal("0.90")
    minimum_risk_after_week: int = 3
    close_warning_days: int = 7
    close_critical_days: int = 3


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
    short_name: str | None = None
    tracking_mode: str = "LIVE"
    position_mode: str = "SPEND"
    position_headline: str | None = None
    position_detail: str | None = None
    provenance_authority: str = "NON_AUTHORITATIVE"
    provenance_reason: str | None = None
    pace_policy: PacePolicy = PacePolicy()
    alert_policy: AlertPolicy = AlertPolicy()
    base_currency: str = "AED"
    target_tier_code: str | None = None

    def tier_for(
        self,
        total_spend: Decimal,
        buckets: dict[str, Decimal] | None = None,
    ) -> RewardTier:
        bucket_spend = buckets or {}
        eligible = [
            tier for tier in self.tiers if tier.qualifies(total_spend, bucket_spend)
        ]
        return max(eligible, key=lambda tier: tier.minimum_spend) if eligible else min(
            self.tiers, key=lambda tier: tier.minimum_spend
        )

    def target_tier(self, total_spend: Decimal, buckets: dict[str, Decimal]) -> RewardTier:
        if self.target_tier_code:
            return next(tier for tier in self.tiers if tier.code == self.target_tier_code)
        return self.tier_for(total_spend, buckets)


@dataclass(frozen=True, slots=True)
class PaymentIntent:
    category: str
    amount_aed: Decimal
    currency: str = "AED"
    channel: str = "UNKNOWN"
    conditional: bool = False

    def __post_init__(self) -> None:
        amount = money(self.amount_aed)
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Payment intent amount must be a finite positive value")
        category = str(self.category or "").strip().upper()
        currency = str(self.currency or "").strip().upper()
        channel = str(self.channel or "").strip().upper()
        if not category:
            raise ValueError("Payment intent category is required")
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("Payment intent currency must be a three-letter code")
        if not channel:
            raise ValueError("Payment intent channel is required")
        object.__setattr__(self, "amount_aed", amount)
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "channel", channel)


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
    target_tier: str
    target_rate: Decimal
    card_spend_before_aed: Decimal
    tier_threshold_aed: Decimal
    tier_remaining_aed: Decimal
    bucket_spend_before_aed: Decimal
    bucket_spend_cap_aed: Decimal | None
    bucket_remaining_aed: Decimal | None


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
    basis: str
    actual_aed: Decimal
    safety_target_aed: Decimal
    expected_to_date_aed: Decimal
    variance_aed: Decimal
    status: str
    cycle_expected_to_date_aed: Decimal
    cycle_variance_aed: Decimal
    cycle_status: str
    routing_status: str
    week_number: int
    week_start: date
    week_end: date
    weekly_spend_aed: Decimal
    weekly_target_aed: Decimal


def _as_of_date(value: date | datetime | None) -> date:
    """Resolve one explicit UTC boundary date for provenance validation.

    Callers that evaluate a historical or boundary fixture can inject ``as_of``
    directly.  The default is deliberately UTC rather than the host's local
    timezone, so a Dubai process and a UTC process validate the same interval.
    """
    if value is None:
        return datetime.now(UTC).date()
    if isinstance(value, datetime):
        normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return normalized.astimezone(UTC).date()
    if isinstance(value, date):
        return value
    raise TypeError("as_of must be a date, datetime, or None")


def _period_transactions(transactions: Iterable[Transaction], card: str) -> list[Transaction]:
    return [transaction for transaction in transactions if transaction.card == card]


def total_spend(transactions: Iterable[Transaction], card: str) -> Decimal:
    return sum(
        (
            transaction.spend_aed
            for transaction in transactions
            if transaction.card == card
            and transaction.transaction_type in CASHBACK_TOPICS
        ),
        Decimal("0"),
    )


def bucket_spend(transactions: Iterable[Transaction], card: str) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for transaction in transactions:
        if (
            transaction.card != card
            or transaction.transaction_type not in CASHBACK_TOPICS
            or not transaction.reward_bucket
        ):
            continue
        result[transaction.reward_bucket] = result.get(transaction.reward_bucket, Decimal("0")) + transaction.spend_aed
    return result


def reward_total(program: CardProgram, total: Decimal, buckets: dict[str, Decimal]) -> Decimal:
    tier = program.tier_for(total, buckets)
    bucket_defs = {bucket.code: bucket for bucket in program.buckets}
    reward = Decimal("0")
    for code, spend in buckets.items():
        rate = tier.rates.get(code, Decimal("0"))
        earned = max(spend, Decimal("0")) * rate
        fallback_cap = bucket_defs.get(code).cap_aed if code in bucket_defs else None
        cap = tier.cashback_cap(code, fallback_cap)
        reward += min(earned, cap) if cap is not None else earned
    if program.rounding_behavior == "CURRENCY_MINOR_UNIT":
        return reward.quantize(Decimal("0.01"))
    if program.rounding_behavior != "NONE":
        raise ValueError(f"Unsupported reward rounding behavior: {program.rounding_behavior}")
    return reward


def evaluate_card(
    program: CardProgram,
    transactions: Iterable[Transaction],
    intent: PaymentIntent,
    *,
    bucket_code: str | None = None,
) -> CardValue | None:
    existing = list(transactions)
    current_total = total_spend(existing, program.card)
    current_buckets = bucket_spend(existing, program.card)
    eligible = [
        bucket
        for bucket in program.buckets
        if bucket.eligible(intent.category, intent.channel, intent.currency)
        and (bucket_code is None or bucket.code == bucket_code)
    ]
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
        target_tier = program.target_tier(target_total, after_buckets)
        target_rate = target_tier.rates.get(bucket.code, Decimal("0"))
        current_bucket_spend = max(current_buckets.get(bucket.code, Decimal("0")), Decimal("0"))
        spend_capacity = None
        if bucket.spend_cap_aed is not None:
            spend_capacity = bucket.spend_cap_aed
            eligible_progress_spend = min(
                money(intent.amount_aed),
                max(spend_capacity - current_bucket_spend, Decimal("0")),
            )
        else:
            target_cap = target_tier.cashback_cap(bucket.code, bucket.cap_aed)
            spend_capacity = (
                target_cap / target_rate
                if target_cap is not None and target_rate > 0
                else None
            )
            eligible_progress_spend = (
                money(intent.amount_aed)
                if spend_capacity is None
                else min(
                    money(intent.amount_aed),
                    max(spend_capacity - current_bucket_spend, Decimal("0")),
                )
            )
        bucket_remaining = (
            None
            if spend_capacity is None
            else max(spend_capacity - current_bucket_spend, Decimal("0"))
        )
        strategic_reward = eligible_progress_spend * target_rate
        actual_marginal_reward = after_reward - before_reward
        decision_reward = max(actual_marginal_reward, strategic_reward)
        cost = (
            money(intent.amount_aed) * program.fx_cost_rate
            if intent.currency.upper() != program.base_currency
            else Decimal("0")
        )
        value = CardValue(
            card=program.card,
            bucket=bucket.code,
            marginal_reward_aed=actual_marginal_reward,
            strategic_reward_aed=strategic_reward,
            estimated_cost_aed=cost,
            net_value_aed=decision_reward - cost,
            tier_before=program.tier_for(current_total, current_buckets).code,
            tier_after=program.tier_for(after_total, after_buckets).code,
            target_tier=target_tier.code,
            target_rate=target_rate,
            card_spend_before_aed=current_total,
            tier_threshold_aed=target_tier.minimum_spend,
            tier_remaining_aed=max(target_tier.minimum_spend - current_total, Decimal("0")),
            bucket_spend_before_aed=current_bucket_spend,
            bucket_spend_cap_aed=spend_capacity,
            bucket_remaining_aed=bucket_remaining,
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
    base_currency = next(
        (program.base_currency for program in program_list if program.card == winner.card),
        intent.currency,
    )
    reason = (
        f"{winner.card} has the highest estimated marginal value of {base_currency} "
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
    *,
    weekly_actual: Decimal | None = None,
    policy: PacePolicy | None = None,
) -> PaceStatus:
    selected_policy = policy or PacePolicy()
    if selected_policy.basis not in {"WEEKLY", "DAILY"}:
        raise ValueError("pace basis must be WEEKLY or DAILY")
    if selected_policy.routing_basis not in {"WEEKLY", "CYCLE"}:
        raise ValueError("pace routing_basis must be WEEKLY or CYCLE")
    if selected_policy.week_length_days < 1 or selected_policy.week_length_days > 31:
        raise ValueError("pace week_length_days must be between 1 and 31")
    start = period_start or as_of.replace(day=1)
    end = period_end or as_of.replace(day=calendar.monthrange(as_of.year, as_of.month)[1])
    if not start <= as_of <= end:
        raise ValueError("as_of must fall inside the cashback period")
    days = (end - start).days + 1
    elapsed = (as_of - start).days + 1
    cycle_expected = money(safety_target) * Decimal(elapsed) / Decimal(days)
    cycle_variance = money(actual) - cycle_expected
    week_index = (elapsed - 1) // selected_policy.week_length_days
    week_start = start + timedelta(days=week_index * selected_policy.week_length_days)
    week_end = min(end, week_start + timedelta(days=selected_policy.week_length_days - 1))
    week_days = (week_end - week_start).days + 1
    week_elapsed = (as_of - week_start).days + 1
    weekly_target = money(safety_target) * Decimal(week_days) / Decimal(days)
    weekly_expected = weekly_target * Decimal(week_elapsed) / Decimal(week_days)
    current_week_actual = money(actual if weekly_actual is None else weekly_actual)
    use_weekly_status = selected_policy.basis == "WEEKLY" and weekly_actual is not None
    if use_weekly_status:
        expected = weekly_expected
        variance = current_week_actual - expected
        tolerance_base = weekly_target
    else:
        expected = cycle_expected
        variance = cycle_variance
        tolerance_base = money(safety_target)
    threshold = max(
        tolerance_base * selected_policy.tolerance_ratio,
        selected_policy.minimum_tolerance_aed,
    )
    def classify(value: Decimal, difference: Decimal, tolerance: Decimal) -> str:
        if value >= safety_target:
            return "SECURED"
        if difference < -tolerance:
            return "UNDER"
        if difference > tolerance:
            return "OVER"
        return "ON_PACE"

    status = classify(money(actual), variance, threshold)
    cycle_threshold = max(
        money(safety_target) * selected_policy.tolerance_ratio,
        selected_policy.minimum_tolerance_aed,
    )
    cycle_status = classify(money(actual), cycle_variance, cycle_threshold)
    routing_status = status if selected_policy.routing_basis == "WEEKLY" else cycle_status
    return PaceStatus(
        selected_policy.basis,
        money(actual),
        money(safety_target),
        expected,
        variance,
        status,
        cycle_expected,
        cycle_variance,
        cycle_status,
        routing_status,
        week_index + 1,
        week_start,
        week_end,
        current_week_actual,
        weekly_target,
    )


def _optional_decimal(value: object) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))


def _configured_value(source: dict[str, object], generic: str, legacy: str) -> object:
    return source.get(generic) if generic in source else source.get(legacy)


def _iso_date(value: object) -> date | None:
    return None if value in (None, "") else date.fromisoformat(str(value))


def _merged_policy(
    source: dict[str, object],
    item: dict[str, object],
    key: str,
) -> dict[str, object]:
    defaults = source.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ValueError("cashback defaults must be an object")
    base = defaults.get(key) or {}
    override = item.get(key) or {}
    if not isinstance(base, dict) or not isinstance(override, dict):
        raise ValueError(f"cashback {key} policy must be an object")
    return {**base, **override}


def _provenance_claim_paths(program: dict[str, object]) -> dict[str, str]:
    paths = {"programme": "PROGRAMME"}
    for tier in program.get("tiers") or []:
        if not isinstance(tier, dict):
            continue
        code = str(tier.get("code") or "")
        if not code:
            continue
        paths[f"tiers.{code}"] = "TIER"
        for bucket in (tier.get("rates") or {}):
            paths[f"tiers.{code}.rates.{bucket}"] = "RATE"
        for bucket, value in (tier.get("cashback_caps_aed") or {}).items():
            if value is not None:
                paths[f"tiers.{code}.cashback_caps_aed.{bucket}"] = "CAP"
    for bucket in program.get("buckets") or []:
        if not isinstance(bucket, dict):
            continue
        code = str(bucket.get("code") or "")
        if not code:
            continue
        for field in ("cashback_cap", "cashback_cap_aed", "spend_cap", "spend_cap_aed"):
            if bucket.get(field) is not None:
                paths[f"buckets.{code}.{field}"] = "CAP"
        for field in ("excluded_categories", "excluded_channels"):
            for index, value in enumerate(bucket.get(field) or []):
                if value:
                    paths[f"buckets.{code}.{field}[{index}]"] = "EXCLUSION"
    for index, value in enumerate(program.get("exclusions") or []):
        if value:
            paths[f"exclusions[{index}]"] = "EXCLUSION"
    return paths


def _provenance_interval_covers(
    reference_start: date,
    reference_end: date | None,
    claim_start: date,
    claim_end: date | None,
) -> bool:
    return (
        reference_start <= claim_start
        and (reference_end is None or (claim_end is not None and claim_end <= reference_end))
    )


def _validate_provenance_references(
    card: str,
    references: object,
) -> dict[str, dict[str, object]]:
    if not isinstance(references, list):
        raise ValueError(f"Cashback program {card} source_references must be a list")
    references_by_id: dict[str, dict[str, object]] = {}
    for reference in references:
        if not isinstance(reference, dict):
            raise ValueError(f"Cashback program {card} contains invalid provenance evidence")
        reference_id = str(reference.get("id") or "")
        if not reference_id or reference_id in references_by_id:
            raise ValueError(f"Cashback program {card} contains duplicate provenance reference ids")
        reference_authority = str(reference.get("authority") or "")
        if reference_authority not in {"AUTHORITATIVE", "NON_AUTHORITATIVE"}:
            raise ValueError(f"Cashback program {card} has invalid evidence authority")
        if "effective_start" not in reference or "effective_end" not in reference:
            raise ValueError(f"Cashback program {card} evidence requires an effective interval")
        try:
            reference_start = _iso_date(reference.get("effective_start"))
            reference_end = _iso_date(reference.get("effective_end"))
        except ValueError as exc:
            raise ValueError(f"Cashback program {card} evidence has invalid dates") from exc
        if reference_start and reference_end and reference_end < reference_start:
            raise ValueError(f"Cashback program {card} evidence has an invalid date range")
        references_by_id[reference_id] = reference
    return references_by_id


def _validate_provenance_interval_coverage(
    *,
    card: str,
    authority: str,
    claim: dict[str, object],
    program_start: date | None,
    configured_program_end: date | None,
    program_end: date,
    references_by_id: dict[str, dict[str, object]],
) -> None:
    claim_start = _iso_date(claim.get("effective_start"))
    claim_end = _iso_date(claim.get("effective_end"))
    path = str(claim.get("path") or "")
    if claim_start is None:
        raise ValueError(f"Cashback program {card} claim {path} is undated")
    if claim_end and claim_end < claim_start:
        raise ValueError(f"Cashback program {card} claim {path} has an invalid date range")
    if program_start and claim_start < program_start:
        raise ValueError(f"Cashback program {card} claim {path} starts before the programme")
    if program_end and claim_start > program_end:
        raise ValueError(f"Cashback program {card} claim {path} exceeds the programme interval")
    if configured_program_end and (claim_end is None or claim_end > configured_program_end):
        raise ValueError(f"Cashback program {card} claim {path} exceeds the programme interval")
    if not configured_program_end and claim_end and claim_end > program_end:
        raise ValueError(f"Cashback program {card} claim {path} exceeds the programme interval")
    claim_coverage_end = claim_end
    if authority == "AUTHORITATIVE":
        if claim_start != program_start:
            raise ValueError(f"Cashback program {card} claim {path} does not span the programme interval")
        if configured_program_end:
            if claim_end != configured_program_end:
                raise ValueError(f"Cashback program {card} claim {path} does not span the programme interval")
        elif claim_end not in (None, program_end):
            raise ValueError(f"Cashback program {card} claim {path} does not span the programme interval")
        claim_coverage_end = claim_end or program_end
    reference_ids = claim.get("reference_ids")
    if not isinstance(reference_ids, list) or not reference_ids:
        raise ValueError(f"Cashback program {card} claim {path} requires evidence references")
    covered = False
    for reference_id in reference_ids:
        reference = references_by_id.get(str(reference_id))
        if reference is None:
            raise ValueError(f"Cashback program {card} claim {path} references unknown evidence")
        if authority == "AUTHORITATIVE" and reference.get("authority") != "AUTHORITATIVE":
            raise ValueError(f"Cashback program {card} claim {path} uses non-authoritative evidence")
        reference_start = _iso_date(reference.get("effective_start"))
        reference_end = _iso_date(reference.get("effective_end"))
        if reference_start and reference_end and reference_end < reference_start:
            raise ValueError(f"Cashback program {card} evidence has an invalid date range")
        if reference_start and _provenance_interval_covers(
            reference_start, reference_end, claim_start, claim_coverage_end
        ):
            covered = True
    if authority == "AUTHORITATIVE" and not covered:
        raise ValueError(f"Cashback program {card} evidence does not cover claim interval {path}")


def _validate_provenance_claims(
    *,
    card: str,
    authority: str,
    program: dict[str, object],
    source: dict[str, object],
    claims: object,
    references_by_id: dict[str, dict[str, object]],
    as_of: date,
) -> None:
    if not isinstance(claims, list):
        raise ValueError(f"Cashback program {card} provenance claims must be a list")
    expected_paths = _provenance_claim_paths(program)
    if authority == "AUTHORITATIVE":
        if not claims:
            raise ValueError(f"Cashback program {card} requires authoritative provenance claims")
        actual_paths = {str(claim.get("path") or "") for claim in claims if isinstance(claim, dict)}
        if len(actual_paths) != len(claims):
            raise ValueError(f"Cashback program {card} contains duplicate provenance claims")
        if actual_paths != set(expected_paths):
            missing = ", ".join(sorted(set(expected_paths) - actual_paths))
            extra = ", ".join(sorted(actual_paths - set(expected_paths)))
            detail = (f"; missing={missing}" if missing else "") + (f"; extra={extra}" if extra else "")
            raise ValueError(f"Cashback program {card} has incomplete provenance claims{detail}")
    program_start = _iso_date(program.get("effective_start") or source.get("effective_from"))
    configured_program_end = _iso_date(program.get("effective_end") or source.get("effective_end"))
    # An open-ended current programme is only applicable through this validation
    # instant. Without this boundary, a current seed could attest to arbitrary
    # future rates or issuer evidence that has not been observed yet.
    program_end = configured_program_end or as_of
    if program_start and program_end and program_end < program_start:
        raise ValueError(f"Cashback program {card} has an invalid effective interval")
    if authority == "AUTHORITATIVE" and program_start is None:
        raise ValueError(f"Cashback program {card} requires an effective programme start")
    for claim in claims:
        if not isinstance(claim, dict):
            raise ValueError(f"Cashback program {card} contains invalid provenance claim")
        path = str(claim.get("path") or "")
        if path not in expected_paths:
            raise ValueError(f"Cashback program {card} references an unknown provenance path {path}")
        if str(claim.get("kind") or "") != expected_paths[path]:
            raise ValueError(f"Cashback program {card} claim {path} has an invalid kind")
        _validate_provenance_interval_coverage(
            card=card,
            authority=authority,
            claim=claim,
            program_start=program_start,
            configured_program_end=configured_program_end,
            program_end=program_end,
            references_by_id=references_by_id,
        )


def _validate_provenance_fixture_digests(
    card: str,
    references: list[object],
    evidence_root: Path,
) -> None:
    for reference in references:
        if reference.get("authority") != "AUTHORITATIVE":
            continue
        reference_start = _iso_date(reference.get("effective_start"))
        if reference_start is None:
            raise ValueError(f"Cashback program {card} authoritative evidence is undated")
        sha256 = str(reference.get("sha256") or "")
        fixture = str(reference.get("fixture") or "")
        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise ValueError(f"Cashback program {card} authoritative evidence requires a SHA-256")
        if not fixture:
            raise ValueError(f"Cashback program {card} authoritative evidence requires content")
        fixture_path = (evidence_root / fixture).resolve()
        try:
            fixture_path.relative_to(evidence_root.resolve())
        except ValueError as exc:
            raise ValueError(f"Cashback program {card} evidence fixture escapes repository") from exc
        if not fixture_path.is_file():
            raise ValueError(f"Cashback program {card} evidence fixture is missing")
        observed = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
        if observed != sha256:
            raise ValueError(f"Cashback program {card} evidence digest drift for {reference['id']}")


def _profile_schema_path(schema_version: int) -> Path:
    return Path(__file__).resolve().parent.parent / "config" / f"cashback-profile-schema-v{schema_version}.json"


def _validate_profile_schema(source: dict[str, object], schema_version: int) -> None:
    schema_path = _profile_schema_path(schema_version)
    if not schema_path.is_file():
        raise ValueError(f"Cashback profile schema is missing for version {schema_version}")
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cashback profile schema cannot be loaded for version {schema_version}") from exc
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(source), key=lambda error: list(error.absolute_path))
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "$"
        raise ValueError(f"Cashback profile schema error at {location}: {errors[0].message}")


def validate_program_provenance(
    source: dict[str, object],
    *,
    as_of: date | datetime | None = None,
) -> None:
    validation_date = _as_of_date(as_of)
    if int(source.get("schema_version", 1)) < 2:
        return
    programs = source.get("programs") or []
    evidence_root = Path(__file__).resolve().parent.parent
    for item in programs:
        if not isinstance(item, dict):
            continue
        card = str(item.get("card") or "")
        provenance = item.get("provenance")
        if not isinstance(provenance, dict):
            raise ValueError(f"Cashback program {card} requires provenance in schema version 2")
        authority = str(provenance.get("authority") or "")
        if authority not in {"AUTHORITATIVE", "NON_AUTHORITATIVE"}:
            raise ValueError(f"Cashback program {card} has invalid provenance authority")
        references = item.get("source_references") or []
        references_by_id = _validate_provenance_references(card, references)
        claims = provenance.get("claims")
        _validate_provenance_claims(
            card=card,
            authority=authority,
            program=item,
            source=source,
            claims=claims,
            references_by_id=references_by_id,
            as_of=validation_date,
        )
        _validate_provenance_fixture_digests(card, references, evidence_root)


def validate_program_configuration(
    source: dict[str, object],
    *,
    as_of: date | datetime | None = None,
) -> None:
    validate_program_provenance(source, as_of=as_of)
    programs = source.get("programs") or []
    if not isinstance(programs, list) or not programs:
        raise ValueError("Cashback configuration must define at least one program")
    buckets_by_card: dict[str, set[str]] = {}
    for item in programs:
        if not isinstance(item, dict):
            raise ValueError("Each cashback program must be an object")
        card = str(item.get("card") or "").strip()
        if not card:
            raise ValueError("Each cashback program requires a card code")
        buckets = item.get("buckets") or []
        tiers = item.get("tiers") or []
        if not isinstance(buckets, list) or not buckets or not isinstance(tiers, list) or not tiers:
            raise ValueError(f"Cashback program {card} must define tiers and buckets")
        bucket_codes = [str(bucket.get("code") or "") for bucket in buckets if isinstance(bucket, dict)]
        if len(bucket_codes) != len(buckets) or any(not code for code in bucket_codes):
            raise ValueError(f"Cashback program {card} contains an invalid bucket")
        if len(set(bucket_codes)) != len(bucket_codes):
            raise ValueError(f"Cashback program {card} contains duplicate bucket codes")
        buckets_by_card.setdefault(card, set()).update(bucket_codes)
        for tier in tiers:
            if not isinstance(tier, dict):
                raise ValueError(f"Cashback program {card} contains an invalid tier")
            unknown = set((tier.get("rates") or {}).keys()) - set(bucket_codes)
            if unknown:
                raise ValueError(
                    f"Cashback program {card} tier {tier.get('code')} references unknown buckets: "
                    + ", ".join(sorted(unknown))
                )
            unknown_caps = set((tier.get("cashback_caps_aed") or {}).keys()) - set(bucket_codes)
            if unknown_caps:
                raise ValueError(
                    f"Cashback program {card} tier {tier.get('code')} caps reference unknown buckets: "
                    + ", ".join(sorted(unknown_caps))
                )
            for requirement in tier.get("requirements") or []:
                if not isinstance(requirement, dict):
                    raise ValueError(f"Cashback program {card} contains an invalid tier requirement")
                metric = str(requirement.get("metric") or "").upper()
                operator = str(requirement.get("operator") or "GTE").upper()
                if metric not in {"TOTAL_SPEND", "BUCKET_SPEND"}:
                    raise ValueError(f"Cashback program {card} uses unsupported tier metric {metric}")
                if operator not in {"GTE", "GT", "LTE", "LT", "EQ"}:
                    raise ValueError(f"Cashback program {card} uses unsupported tier operator {operator}")
                bucket = str(requirement.get("bucket") or "")
                if metric == "BUCKET_SPEND" and bucket not in bucket_codes:
                    raise ValueError(
                        f"Cashback program {card} tier requirement references unknown bucket {bucket}"
                    )
        target_tier = str(item.get("target_tier") or "")
        tier_codes = {str(tier.get("code") or "") for tier in tiers if isinstance(tier, dict)}
        if target_tier and target_tier not in tier_codes:
            raise ValueError(f"Cashback program {card} references unknown target tier {target_tier}")
        pace = _merged_policy(source, item, "pace")
        basis = str(pace.get("basis") or "WEEKLY").upper()
        if basis not in {"WEEKLY", "DAILY"}:
            raise ValueError(f"Cashback program {card} pace basis must be WEEKLY or DAILY")
        routing_basis = str(pace.get("routing_basis") or "CYCLE").upper()
        if routing_basis not in {"WEEKLY", "CYCLE"}:
            raise ValueError(f"Cashback program {card} pace routing_basis must be WEEKLY or CYCLE")
        week_length = int(pace.get("week_length_days", 7))
        if week_length < 1 or week_length > 31:
            raise ValueError(f"Cashback program {card} pace week_length_days must be between 1 and 31")
        alerts = _merged_policy(source, item, "alerts")
        near_full = Decimal(str(alerts.get("bucket_near_full_ratio", "0.90")))
        if not Decimal("0") < near_full < Decimal("1"):
            raise ValueError(f"Cashback program {card} bucket_near_full_ratio must be between 0 and 1")
        close_warning = int(alerts.get("close_warning_days", 7))
        close_critical = int(alerts.get("close_critical_days", 3))
        if close_warning < 0 or close_critical < 0 or close_critical > close_warning:
            raise ValueError(f"Cashback program {card} close alert days are invalid")

    route_policies = source.get("route_policies") or {}
    if not isinstance(route_policies, dict):
        raise ValueError("route_policies must be an object")
    allowed_checks = {
        "bucket_open",
        "bucket_fits_purchase",
        "target_rate_positive",
        "net_value_positive",
        "target_unmet",
        "target_met",
        "pace_in",
        "pace_not_in",
    }
    for code, policy in route_policies.items():
        if not isinstance(policy, dict):
            raise ValueError(f"Routing policy {code} must be an object")
        when = policy.get("when") or {}
        ranking = policy.get("ranking") or {}
        reasons = policy.get("reasons") or {}
        if not isinstance(when, dict) or not isinstance(ranking, dict) or not isinstance(reasons, dict):
            raise ValueError(f"Routing policy {code} must define object policies")
        unknown = set(when) - allowed_checks
        if unknown:
            raise ValueError(
                f"Routing policy {code} uses unknown checks: " + ", ".join(sorted(unknown))
            )
        groups = ranking.get("groups_by_pace") or {}
        if not isinstance(groups, dict) or not groups:
            raise ValueError(f"Routing policy {code} requires groups_by_pace")
        for rank in groups.values():
            int(rank)

    routing_profiles = source.get("routing_profiles") or []
    if routing_profiles and not route_policies:
        raise ValueError("routing_profiles require explicit route_policies")
    for profile in routing_profiles:
        if not isinstance(profile, dict):
            raise ValueError("Each routing profile must be an object")
        for route in profile.get("routes") or []:
            if not isinstance(route, dict):
                raise ValueError(f"Routing profile {profile.get('code')} contains an invalid route")
            card = str(route.get("card") or "")
            bucket = str(route.get("bucket") or "")
            policy = str(route.get("policy") or route.get("purpose") or "")
            if not policy:
                raise ValueError(
                    f"Routing profile {profile.get('code')} contains a route without a policy"
                )
            if policy not in route_policies:
                raise ValueError(f"Routing profile {profile.get('code')} references unknown policy {policy}")
            if card not in buckets_by_card:
                raise ValueError(f"Routing profile {profile.get('code')} references unknown card {card}")
            if bucket not in buckets_by_card[card]:
                raise ValueError(
                    f"Routing profile {profile.get('code')} references unknown bucket {card}/{bucket}"
                )


def purchase_type_from_config(
    source: dict[str, object],
    category: str | None,
    merchant: str = "",
) -> str:
    normalization = source.get("normalization") or {}
    if not isinstance(normalization, dict):
        raise ValueError("cashback normalization must be an object")
    upper_merchant = merchant.upper()
    for rule in normalization.get("merchant_purchase_types") or []:
        if not isinstance(rule, dict):
            continue
        contains = str(rule.get("contains") or "").upper()
        if contains and contains in upper_merchant:
            return str(rule["purchase_type"]).upper()
    mapping = normalization.get("actual_category_purchase_types") or {}
    if not isinstance(mapping, dict):
        raise ValueError("actual_category_purchase_types must be an object")
    return str(mapping.get(category or "") or normalization.get("default_purchase_type") or "GENERAL").upper()


def channel_from_config(
    source: dict[str, object],
    tags: Iterable[str],
    merchant: str = "",
    card: str = "",
) -> str:
    for tag in tags:
        if str(tag).casefold().startswith("channel-"):
            return str(tag)[8:].replace("-", "_").upper()
    normalization = source.get("normalization") or {}
    if not isinstance(normalization, dict):
        raise ValueError("cashback normalization must be an object")
    upper_merchant = merchant.upper()
    for rule in normalization.get("merchant_channels") or []:
        if not isinstance(rule, dict):
            continue
        contains = str(rule.get("contains") or "").upper()
        if contains and contains in upper_merchant:
            return str(rule["channel"]).upper()
    card_defaults = normalization.get("card_default_channels") or {}
    if not isinstance(card_defaults, dict):
        raise ValueError("cashback card_default_channels must be an object")
    if card and str(card_defaults.get(card) or "").strip():
        return str(card_defaults[card]).upper()
    return str(normalization.get("default_channel") or "UNKNOWN").upper()


def configured_reward_bucket(
    programs: Iterable[CardProgram],
    card: str,
    category: str,
    channel: str,
    currency: str,
) -> str | None:
    program = next((item for item in programs if item.card == card), None)
    if program is None:
        return None
    specific = [
        bucket
        for bucket in program.buckets
        if not bucket.assignment_fallback
        and bucket.matches_assignment(category, channel, currency)
    ]
    if specific:
        return specific[0].code
    if channel.upper() == "UNKNOWN":
        return None
    fallback = next((bucket for bucket in program.buckets if bucket.assignment_fallback), None)
    return None if fallback is None else fallback.code


def programs_from_config(
    source: dict[str, object],
    period_date: date | None = None,
    *,
    as_of: date | datetime | None = None,
) -> tuple[CardProgram, ...]:
    validate_program_configuration(source, as_of=as_of)
    base_currency = str(source.get("currency") or "AED").upper()
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
                minimum_spend=Decimal(
                    str(_configured_value(tier, "minimum_spend", "minimum_spend_aed") or "0")
                ),
                rates={code: Decimal(str(rate)) for code, rate in tier.get("rates", {}).items()},
                cashback_caps_aed={
                    code: Decimal(str(cap))
                    for code, cap in (tier.get("cashback_caps_aed") or {}).items()
                },
                requirements=tuple(
                    TierRequirement(
                        metric=str(requirement["metric"]).upper(),
                        operator=str(requirement.get("operator") or "GTE").upper(),
                        value=Decimal(str(requirement["value"])),
                        bucket=(
                            str(requirement.get("bucket"))
                            if requirement.get("bucket") is not None
                            else None
                        ),
                    )
                    for requirement in tier.get("requirements") or []
                ),
            )
            for tier in item.get("tiers", [])
        )
        buckets = tuple(
            RewardBucket(
                code=str(bucket["code"]),
                cap_aed=_optional_decimal(
                    _configured_value(bucket, "cashback_cap", "cashback_cap_aed")
                ),
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
                base_currency_only=bool(
                    bucket.get("base_currency_only", bucket.get("aed_only", False))
                ),
                base_currency=base_currency,
                spend_cap_aed=_optional_decimal(
                    _configured_value(bucket, "spend_cap", "spend_cap_aed")
                ),
                assignment_categories=frozenset(
                    str(value).upper()
                    for value in (bucket.get("assignment") or {}).get("categories", [])
                ),
                assignment_channels=frozenset(
                    str(value).upper()
                    for value in (bucket.get("assignment") or {}).get("channels", [])
                ),
                assignment_currencies=frozenset(
                    str(value).upper()
                    for value in (bucket.get("assignment") or {}).get("currencies", [])
                ),
                assignment_foreign_only=bool(
                    (bucket.get("assignment") or {}).get("foreign_only", False)
                ),
                assignment_base_currency_only=bool(
                    (bucket.get("assignment") or {}).get(
                        "base_currency_only",
                        (bucket.get("assignment") or {}).get("aed_only", False),
                    )
                ),
                assignment_fallback=bool(
                    (bucket.get("assignment") or {}).get("fallback", False)
                ),
            )
            for bucket in item.get("buckets", [])
        )
        if not tiers or not buckets:
            raise ValueError(f"Cashback program {item.get('card')} must define tiers and buckets")
        pace = _merged_policy(source, item, "pace")
        alerts = _merged_policy(source, item, "alerts")
        tracking = item.get("tracking") or {}
        provenance = item.get("provenance") or {}
        if not isinstance(tracking, dict):
            raise ValueError(f"Cashback program {item.get('card')} tracking must be an object")
        tracking_mode = str(tracking.get("mode") or "LIVE").upper()
        position_mode = str(tracking.get("position_mode") or "SPEND").upper()
        if tracking_mode not in {"LIVE", "STATEMENT_ONLY"}:
            raise ValueError(f"Cashback program {item.get('card')} has invalid tracking mode")
        if position_mode not in {"SPEND", "UNLIMITED"}:
            raise ValueError(f"Cashback program {item.get('card')} has invalid position mode")
        programs.append(
            CardProgram(
                card=str(item["card"]),
                name=str(item["name"]),
                safety_target=_optional_decimal(
                    _configured_value(item, "safety_target", "safety_target_aed")
                ),
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
                short_name=str(item.get("short_name") or item["name"]),
                tracking_mode=tracking_mode,
                position_mode=position_mode,
                position_headline=str(tracking.get("headline") or "") or None,
                position_detail=str(tracking.get("detail") or "") or None,
                provenance_authority=str(provenance.get("authority") or "NON_AUTHORITATIVE"),
                provenance_reason=str(provenance.get("reason") or "") or None,
                pace_policy=PacePolicy(
                    basis=str(pace.get("basis") or "WEEKLY").upper(),
                    routing_basis=str(pace.get("routing_basis") or "CYCLE").upper(),
                    week_length_days=int(pace.get("week_length_days", 7)),
                    tolerance_ratio=Decimal(str(pace.get("tolerance_percent", "0.05"))),
                    minimum_tolerance_aed=Decimal(
                        str(pace.get("minimum_tolerance", pace.get("minimum_tolerance_aed", "250")))
                    ),
                ),
                alert_policy=AlertPolicy(
                    bucket_near_full_ratio=Decimal(
                        str(alerts.get("bucket_near_full_ratio", "0.90"))
                    ),
                    minimum_risk_after_week=int(
                        alerts.get("minimum_risk_after_week", 3)
                    ),
                    close_warning_days=int(alerts.get("close_warning_days", 7)),
                    close_critical_days=int(alerts.get("close_critical_days", 3)),
                ),
                base_currency=base_currency,
                target_tier_code=str(item.get("target_tier") or "") or None,
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
            amount_aed=Decimal(
                str(_configured_value(item, "decision_amount", "decision_amount_aed") or "100")
            ),
            currency=str(item.get("currency") or "AED"),
            channel=str(item.get("channel") or "UNKNOWN"),
            conditional=bool(item.get("conditional", False)),
        )
        for item in source.get("payment_intents", [])
    )


def load_program_configuration(
    path: Path | None = None,
    *,
    as_of: date | datetime | None = None,
) -> dict[str, object]:
    resolved = path or Path(__file__).resolve().parent.parent / "config" / "cashback-programs.json"
    source = json.loads(resolved.read_text(encoding="utf-8"))
    try:
        schema_version = int(source.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("Unsupported cashback program schema version") from exc
    if schema_version not in {1, 2}:
        raise ValueError("Unsupported cashback program schema version")
    _validate_profile_schema(source, schema_version)
    validate_program_configuration(source, as_of=as_of)
    return source


def configured_programs(period_date: date | None = None) -> tuple[CardProgram, ...]:
    """Load the versioned card-programme configuration; verify seed assumptions before production use."""
    return programs_from_config(
        load_program_configuration(as_of=period_date),
        period_date,
        as_of=period_date,
    )
