from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
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
    foreign_only: bool = False
    aed_only: bool = False

    def eligible(self, category: str, channel: str, currency: str) -> bool:
        if self.categories and category.upper() not in self.categories:
            return False
        if self.channels and channel.upper() not in self.channels:
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
        cost = money(intent.amount_aed) * program.fx_cost_rate if intent.currency.upper() != "AED" else Decimal("0")
        value = CardValue(
            card=program.card,
            bucket=bucket.code,
            marginal_reward_aed=after_reward - before_reward,
            estimated_cost_aed=cost,
            net_value_aed=after_reward - before_reward - cost,
            tier_before=program.tier_for(current_total).code,
            tier_after=program.tier_for(after_total).code,
        )
        if best is None or value.net_value_aed > best.net_value_aed:
            best = value
    return best


def recommend(programs: Iterable[CardProgram], transactions: Iterable[Transaction], intent: PaymentIntent) -> Recommendation:
    values = [value for program in programs if (value := evaluate_card(program, transactions, intent)) is not None]
    if not values:
        raise ValueError(f"No eligible card for {intent.category}/{intent.channel}/{intent.currency}")
    ranked = tuple(sorted(values, key=lambda item: (item.net_value_aed, item.card), reverse=True))
    winner = ranked[0]
    alternative = ranked[1].card if len(ranked) > 1 else None
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
        net_value_aed=winner.net_value_aed,
        reason=reason,
        ranked=ranked,
    )


def pace_status(actual: Decimal, safety_target: Decimal, as_of: date) -> PaceStatus:
    days = calendar.monthrange(as_of.year, as_of.month)[1]
    expected = money(safety_target) * Decimal(as_of.day) / Decimal(days)
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


def poc_programs() -> tuple[CardProgram, ...]:
    """POC assumptions from the supplied brainstorm; verify before production."""
    return (
        CardProgram(
            card="RAK_WORLD",
            name="RAKBANK World",
            safety_target=Decimal("10300"),
            tiers=(
                RewardTier("BASE", Decimal("0"), {"RAK_STANDARD": Decimal("0.01")}),
                RewardTier(
                    "ENHANCED",
                    Decimal("10000"),
                    {
                        "RAK_GROCERY": Decimal("0.10"),
                        "RAK_DINING": Decimal("0.10"),
                        "RAK_TRAVEL": Decimal("0.10"),
                        "RAK_STANDARD": Decimal("0.01"),
                        "RAK_EWALLET": Decimal("0.03"),
                    },
                ),
            ),
            buckets=(
                RewardBucket("RAK_GROCERY", Decimal("300"), frozenset({"GROCERY"})),
                RewardBucket("RAK_DINING", Decimal("300"), frozenset({"DINING"})),
                RewardBucket("RAK_TRAVEL", Decimal("400"), frozenset({"TRAVEL", "HOTEL", "AIRLINE"})),
                RewardBucket("RAK_EWALLET", Decimal("150"), channels=frozenset({"APPLE_PAY_POS"})),
                RewardBucket("RAK_STANDARD", Decimal("100")),
            ),
        ),
        CardProgram(
            card="SC_PLATINUM_X",
            name="Standard Chartered Platinum X",
            safety_target=Decimal("15300"),
            tiers=(
                RewardTier("BELOW_MIN", Decimal("0"), {}),
                RewardTier("TIER_3", Decimal("2500"), {"SC_ONLINE": Decimal("0.03"), "SC_WALLET": Decimal("0.03"), "SC_FOREIGN": Decimal("0.03")}),
                RewardTier("TIER_5", Decimal("7500"), {"SC_ONLINE": Decimal("0.05"), "SC_WALLET": Decimal("0.05"), "SC_FOREIGN": Decimal("0.05")}),
                RewardTier("TIER_10", Decimal("15000"), {"SC_ONLINE": Decimal("0.10"), "SC_WALLET": Decimal("0.10"), "SC_FOREIGN": Decimal("0.10")}),
            ),
            buckets=(
                RewardBucket("SC_ONLINE", Decimal("400"), channels=frozenset({"ONLINE"}), aed_only=True),
                RewardBucket("SC_WALLET", Decimal("200"), channels=frozenset({"APPLE_PAY_POS"}), aed_only=True),
                RewardBucket("SC_FOREIGN", Decimal("400"), foreign_only=True),
            ),
            fx_cost_rate=Decimal("0.035"),
        ),
        CardProgram(
            card="EI_AMAZON",
            name="Emirates Islamic Amazon",
            safety_target=None,
            tiers=(RewardTier("STANDARD", Decimal("0"), {"EI_AMAZON": Decimal("0.06")}),),
            buckets=(RewardBucket("EI_AMAZON", None, frozenset({"AMAZON"}), frozenset({"ONLINE"})),),
        ),
    )
