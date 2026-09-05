from __future__ import annotations

import re
import calendar
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Iterable

from .cashback import (
    PaymentIntent,
    bucket_spend,
    channel_from_config,
    configured_reward_bucket,
    evaluate_card,
    load_program_configuration,
    pace_status,
    programs_from_config,
    purchase_type_from_config,
    recommend,
    reward_total,
    total_spend,
)
from .models import Transaction
from .actual_pipeline import account_maps, account_owner_map
from .transaction_semantics import REFUND_TOPICS, TOPIC_BY_TAG


_TAG = re.compile(r"(?:^|\s)#([A-Za-z0-9_:-]+)")
_CURRENCY = re.compile(
    r"(?:^|\s)(?:currency:|FX:?\s+)([A-Z]{3})(?:\s|$)",
    re.I,
)


def _tags(notes: str) -> set[str]:
    return {match.group(1) for match in _TAG.finditer(notes or "")}


def _canonical_topic(tags: set[str]) -> str | None:
    topics = {
        TOPIC_BY_TAG[tag.casefold()]
        for tag in tags
        if tag.casefold() in TOPIC_BY_TAG
    }
    # Older rows used #refund for reversals; prefer the durable canonical tag.
    if "REVERSAL" in topics:
        topics.discard("REFUND")
    if len(topics) > 1:
        raise ValueError("Conflicting canonical Actual topic tags: " + ", ".join(sorted(topics)))
    return next(iter(topics), None)


def _plain(value: Decimal) -> str:
    return format(value, "f")


def _reward_bucket(
    programs: Iterable[Any],
    card: str,
    category: str,
    channel: str,
    currency: str,
    tags: set[str],
) -> str | None:
    tagged_buckets = sorted({
        tag[9:].replace("-", "_").upper()
        for tag in tags
        if tag.casefold().startswith("cashback-")
    })
    if len(tagged_buckets) > 1:
        raise ValueError(
            "Conflicting cashback bucket tags: " + ", ".join(tagged_buckets)
        )
    if tagged_buckets:
        return tagged_buckets[0]
    return configured_reward_bucket(programs, card, category, channel, currency)


def transactions_from_actual_snapshot(
    snapshot: dict[str, Any],
    config: dict[str, Any],
    cashback_config: dict[str, Any] | None = None,
) -> list[Transaction]:
    cashback_source = cashback_config or load_program_configuration()
    base_currency = str(cashback_source.get("currency") or config.get("currency") or "XXX").upper()
    programs = programs_from_config(cashback_source)
    retired_config = config.get("retired_accounts", [])
    if not isinstance(retired_config, list) or any(
        not isinstance(name, str) or not name.strip() for name in retired_config
    ):
        raise ValueError("Actual snapshot retired_accounts must be a list of strings")
    retired_accounts = {name.casefold() for name in retired_config}

    disabled_accounts: set[str] = set()
    active_accounts: list[dict[str, Any]] = []
    for account in config["accounts"]:
        enabled = account.get("enabled", True)
        if type(enabled) is not bool:
            raise ValueError("Actual snapshot account enabled must be a boolean")
        name = str(account["name"])
        if not enabled:
            disabled_accounts.add(name.casefold())
            disabled_accounts.update(
                str(alias).casefold() for alias in account.get("aliases", [])
            )
            continue
        active_accounts.append(account)

    active_config = {"accounts": active_accounts}
    _, account_by_card = account_maps(active_config)
    owner_by_card = account_owner_map(active_config)
    active_account_by_card = account_by_card

    card_by_account: dict[str, str] = {}

    def bind_account_identifier(identifier: str, card: str) -> None:
        key = identifier.casefold()
        previous = card_by_account.get(key)
        if previous is not None and previous != card:
            raise ValueError(
                f"Actual snapshot account alias {identifier!r} maps to multiple cards: "
                f"{previous}, {card}"
            )
        card_by_account[key] = card

    for card, name in active_account_by_card.items():
        bind_account_identifier(name, card)
    for account in active_accounts:
        card = str(account.get("card_code") or account["name"]).upper()
        for alias in account.get("aliases", []):
            bind_account_identifier(str(alias), card)

    unknown_accounts = sorted({
        str(row["account_name"])
        for row in snapshot.get("transactions", [])
        if str(row["account_name"]).casefold() not in card_by_account
        and str(row["account_name"]).casefold() not in retired_accounts
        and str(row["account_name"]).casefold() not in disabled_accounts
    })
    if unknown_accounts:
        raise ValueError(
            "Unknown active Actual snapshot accounts: " + ", ".join(unknown_accounts)
        )

    result: list[Transaction] = []
    for row in snapshot.get("transactions", []):
        account_name = str(row["account_name"])
        card = card_by_account.get(account_name.casefold())
        if card is None:
            # Retired, disabled, and non-reward accounts stay outside replay.
            continue
        notes = str(row.get("notes") or "")
        tags = _tags(notes)
        merchant = str(row.get("imported_payee") or row.get("payee_name") or "Unknown")
        currency_match = _CURRENCY.search(notes)
        currency = currency_match.group(1).upper() if currency_match else base_currency
        category = purchase_type_from_config(cashback_source, row.get("category_name"), merchant)
        channel = channel_from_config(cashback_source, tags, merchant, card)
        amount_minor = int(row["amount"])
        canonical_topic = _canonical_topic(tags)
        transfer = bool(row.get("transfer_id"))
        card_payment = row.get("category_name") == "Card Payments" or any(
            tag.casefold() == "card-payment" for tag in tags
        )
        income_category = row.get("category_name") in {"Cashback & Rewards", "Other Income"}
        transaction_type = (
            canonical_topic
            if canonical_topic is not None
            else "TRANSFER" if transfer or card_payment else
            "REWARD_CREDIT" if amount_minor > 0 and income_category else
            "REVERSAL" if amount_minor > 0 and "reversal" in {tag.casefold() for tag in tags} else
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
                source_direction="CREDIT" if amount_minor > 0 else "DEBIT",
                currency=currency,
                channel=channel,
                source_type="actual_snapshot",
                category=category,
                subcategory=row.get("category_name"),
                transaction_type=transaction_type,
                reward_bucket=_reward_bucket(programs, card, category, channel, currency, tags),
                tags={
                    tag
                    for tag in tags
                    if not tag.casefold().startswith(("channel-", "cashback-", "owner-"))
                },
                review_required=row.get("category_name") is None or bool({"review", "needs-review"} & tags),
                is_refund=transaction_type in REFUND_TOPICS,
                metadata={
                    "actual_id": row["id"],
                    "cleared": bool(row.get("cleared")),
                    "reconciled": bool(row.get("reconciled")),
                },
            )
        )
    return result


def _bucket_state(
    program: Any,
    buckets: dict[str, Decimal],
    target_tier: Any,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    bucket_rows = []
    alerts = []
    for bucket in program.buckets:
        bucket_actual = buckets.get(bucket.code, Decimal("0"))
        rate = target_tier.rates.get(bucket.code, Decimal("0"))
        spend_cap = bucket.spend_cap_aed
        if spend_cap is None:
            cashback_cap = target_tier.cashback_cap(bucket.code, bucket.cap_aed)
            spend_cap = None if cashback_cap is None or rate <= 0 else cashback_cap / rate
        bucket_ratio = None if spend_cap in (None, Decimal("0")) else bucket_actual / spend_cap
        bucket_status = (
            "FULL" if bucket_ratio is not None and bucket_ratio >= 1
            else "NEAR_FULL" if (
                bucket_ratio is not None
                and bucket_ratio >= program.alert_policy.bucket_near_full_ratio
            )
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
    return bucket_rows, alerts


def _pace_state(
    program: Any,
    card_transactions: list[Transaction],
    spend: Decimal,
    period: tuple[date, date] | None,
    as_of: date,
    base_currency: str,
) -> tuple[dict[str, Any] | None, str, Any, list[dict[str, object]]]:
    if program.safety_target is None:
        return None, "CURRENT_TIER", program, []

    period_start, period_end = period or (
        as_of.replace(day=1),
        as_of.replace(day=calendar.monthrange(as_of.year, as_of.month)[1]),
    )
    elapsed_days = (as_of - period_start).days + 1
    cycle_days = (period_end - period_start).days + 1
    week_index = (elapsed_days - 1) // program.pace_policy.week_length_days
    current_week_start = period_start + timedelta(
        days=week_index * program.pace_policy.week_length_days
    )
    current_week_end = min(
        period_end,
        current_week_start + timedelta(days=program.pace_policy.week_length_days - 1),
    )
    weekly_spend = total_spend(
        [
            row
            for row in card_transactions
            if current_week_start <= row.transaction_at.date() <= current_week_end
        ],
        program.card,
    )
    pace = asdict(
        pace_status(
            spend,
            program.safety_target,
            as_of,
            period_start,
            period_end,
            weekly_actual=weekly_spend,
            policy=program.pace_policy,
        )
    )
    pace = {
        key: (
            _plain(value) if isinstance(value, Decimal)
            else value.isoformat() if isinstance(value, date)
            else value
        )
        for key, value in pace.items()
    }
    risk_after_days = (
        program.pace_policy.week_length_days
        * program.alert_policy.minimum_risk_after_week
    )
    alerts = []
    # An under-target card remains a valid destination for unavoidable eligible
    # spend through cycle close.  The warning below communicates that the target
    # is at risk; routing must not erase the target and collapse every candidate
    # to a zero-rate current tier merely because projected spend is low.
    routing_mode = "TARGET_TIER"
    routing_program = program
    days_remaining = (period_end - as_of).days
    near_close = 0 <= days_remaining <= program.alert_policy.close_warning_days
    # safety_target includes configured buffers and may target a higher tier;
    # it is not the issuer's minimum spend. Close warnings supersede early
    # pace warnings so the same target gap appears once per card.
    if elapsed_days >= risk_after_days and spend < program.safety_target and not near_close:
        alerts.append({
            "key": f"minimum:{program.card}:{period_start}:{period_end}",
            "severity": "warning",
            "title": f"{program.name} configured spend target is at risk",
            "detail": (
                f"{base_currency} {_plain(program.safety_target - spend)} remains to the configured "
                f"{base_currency} {_plain(program.safety_target)} spend target after week "
                f"{program.alert_policy.minimum_risk_after_week} of the cycle. Route existing "
                "eligible spend here when appropriate; do not spend extra to chase the threshold."
            ),
        })
    if (
        near_close
        and spend < program.safety_target
    ):
        alerts.append({
            "key": f"close:{program.card}:{period_start}:{period_end}",
            "severity": (
                "critical"
                if days_remaining <= program.alert_policy.close_critical_days
                else "warning"
            ),
            "title": f"{program.name} configured spend target is not reached",
            "detail": (
                f"{base_currency} {_plain(program.safety_target - spend)} remains to the configured "
                f"{base_currency} {_plain(program.safety_target)} spend target, with "
                f"{days_remaining} day{'s' if days_remaining != 1 else ''} until cycle close."
            ),
        })
    return pace, routing_mode, routing_program, alerts


def _build_card_state(
    programs: Iterable[Any],
    rows: list[Transaction],
    as_of: date,
    periods_by_card: dict[str, tuple[date, date]] | None,
    base_currency: str,
) -> tuple[list[dict[str, Any]], list[Any], list[dict[str, object]]]:
    routing_programs = []
    program_rows: list[dict[str, Any]] = []
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
        target_tier = program.target_tier(program.safety_target or spend, buckets)
        bucket_rows, bucket_alerts = _bucket_state(program, buckets, target_tier)
        alerts.extend(bucket_alerts)
        pace, routing_mode, routing_program, pace_alerts = _pace_state(
            program,
            card_transactions,
            spend,
            period,
            as_of,
            base_currency,
        )
        routing_programs.append(routing_program)
        alerts.extend(pace_alerts)
        program_rows.append({
            "card": program.card,
            "name": program.name,
            "short_name": program.short_name or program.name,
            "tracking_mode": program.tracking_mode,
            "position_mode": program.position_mode,
            "position_headline": program.position_headline,
            "position_detail": program.position_detail,
            "provenance_authority": program.provenance_authority,
            "provenance_reason": program.provenance_reason,
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
            "tier": program.tier_for(spend, buckets).code,
            "reward_eligibility_verified": program.reward_eligibility_verified,
            "expected_cashback_aed": (_plain(reward_total(program, spend, buckets)) if program.reward_eligibility_verified else None),
            "tiers": [
                {
                    "code": tier.code,
                    "minimum_spend_aed": _plain(tier.minimum_spend),
                    "met": tier.qualifies(spend, buckets),
                    "remaining_aed": _plain(max(tier.minimum_spend - spend, Decimal("0"))),
                    "requirements": [
                        {
                            "metric": requirement.metric,
                            "operator": requirement.operator,
                            "value": _plain(requirement.value),
                            "bucket": requirement.bucket,
                            "met": requirement.met(spend, buckets),
                        }
                        for requirement in tier.requirements
                    ],
                }
                for tier in program.tiers
            ],
            "routing_mode": routing_mode,
            "pace": pace,
            "transaction_count": len(card_transactions),
            "refund_effect_aed": _plain(
                sum((-row.spend_aed for row in card_transactions if row.spend_aed < 0), Decimal("0"))
            ),
            "buckets": bucket_rows,
        })
    return program_rows, routing_programs, alerts


def _build_recommendations(
    routing_programs: list[Any],
    rows: list[Transaction],
    intents: Iterable[PaymentIntent],
) -> list[dict[str, object]]:
    programs_by_card = {program.card: program for program in routing_programs}
    threshold_actionable = any(
        program.safety_target is not None
        and total_spend(rows, program.card) < program.safety_target
        for program in routing_programs
    )
    recommendations = []
    for intent in intents:
        item = recommend(routing_programs, rows, intent)
        ranked_cards = []
        for index, candidate in enumerate(item.ranked):
            program = programs_by_card[candidate.card]
            current_tier = next(
                tier for tier in program.tiers if tier.code == candidate.tier_before
            )
            current_tier_rate = current_tier.rates.get(
                candidate.bucket, Decimal("0")
            )
            ranked_cards.append({
                "order": index + 1,
                "status": (
                    "PREFERRED" if index == 0
                    else "AVOID" if candidate.net_value_aed <= 0
                    else "NEXT"
                ),
                "card": candidate.card,
                "bucket": candidate.bucket,
                "tier_before": candidate.tier_before,
                "tier_after": candidate.tier_after,
                "target_tier": candidate.target_tier,
                "target_rate_percent": _plain(candidate.target_rate * Decimal("100")),
                "estimated_net_value_aed": _plain(candidate.net_value_aed),
                "current_state_marginal_reward_aed": _plain(
                    candidate.marginal_reward_aed
                ),
                "current_state_marginal_return_percent": _plain(
                    candidate.marginal_reward_aed
                    / intent.amount_aed
                    * Decimal("100")
                    if intent.amount_aed
                    else Decimal("0")
                ),
                "current_tier_rate_percent": _plain(
                    current_tier_rate * Decimal("100")
                ),
                "configured_fx_fee_percent": _plain(
                    program.fx_cost_rate * Decimal("100")
                    if intent.currency.upper() != program.base_currency else Decimal("0")
                ),
                "conditional_target_reward_aed": _plain(
                    candidate.strategic_reward_aed
                ),
                "conditional_target_rate_percent": _plain(
                    candidate.target_rate * Decimal("100")
                ),
                "estimate_basis": (
                    "CONDITIONAL_TARGET_TIER"
                    if candidate.strategic_reward_aed > candidate.marginal_reward_aed
                    else "CURRENT_TIER"
                ),
                "estimated_net_return_percent": _plain(
                    candidate.net_value_aed / intent.amount_aed * Decimal("100")
                    if intent.amount_aed
                    else Decimal("0")
                ),
                "card_spend_aed": _plain(candidate.card_spend_before_aed),
                "tier_threshold_aed": _plain(candidate.tier_threshold_aed),
                "tier_remaining_aed": _plain(candidate.tier_remaining_aed),
                "bucket_spend_aed": _plain(candidate.bucket_spend_before_aed),
                "bucket_cap_aed": (
                    None if candidate.bucket_spend_cap_aed is None
                    else _plain(candidate.bucket_spend_cap_aed)
                ),
                "bucket_remaining_aed": (
                    None if candidate.bucket_remaining_aed is None
                    else _plain(candidate.bucket_remaining_aed)
                ),
            })
        recommendations.append({
            "purchase_type": intent.category,
            "channel": intent.channel,
            "currency": intent.currency,
            "use_card": item.primary_card,
            "avoid_cards": list(item.avoid_cards),
            "guidance": item.guidance,
            "reason": item.reason,
            "decision_amount_aed": _plain(intent.amount_aed),
            "estimated_net_value_aed": _plain(item.net_value_aed),
            "estimated_net_return_percent": _plain(
                item.net_value_aed / intent.amount_aed * Decimal("100")
                if intent.amount_aed
                else Decimal("0")
            ),
            "conditional": intent.conditional,
            "active": not intent.conditional or threshold_actionable,
            "ranked_cards": ranked_cards,
        })
    return recommendations


def _build_routing_graphs(
    routing_programs: list[Any],
    program_rows: list[dict[str, Any]],
    rows: list[Transaction],
    routing_profiles: Iterable[dict[str, object]] | None,
    route_policies: dict[str, dict[str, object]] | None,
) -> list[dict[str, object]]:
    routing_programs_by_card = {program.card: program for program in routing_programs}
    routing_state_by_card = {str(row["card"]): row for row in program_rows}
    active_route_policies = route_policies or {}
    routing_graphs = []
    for profile in routing_profiles or ():
        amount = Decimal(
            str(profile.get("decision_amount", profile.get("decision_amount_aed")) or "100")
        )
        category = str(profile["category"])
        currency = str(profile.get("currency") or "AED")
        route_candidates: dict[tuple[str, str, str], dict[str, object]] = {}
        for route in profile.get("routes") or ():
            card = str(route["card"])
            program = routing_programs_by_card.get(card)
            if program is None:
                continue
            channel = str(route["channel"])
            intent = PaymentIntent(category, amount, currency, channel)
            candidate = evaluate_card(
                program,
                rows,
                intent,
                bucket_code=str(route["bucket"]),
            )
            if candidate is None:
                continue
            purpose = str(route.get("purpose") or route.get("policy") or "")
            policy_code = str(route.get("policy") or purpose)
            policy = active_route_policies.get(policy_code)
            if not isinstance(policy, dict):
                raise ValueError(f"Unknown routing policy: {policy_code}")
            when = policy.get("when") or {}
            ranking = policy.get("ranking") or {}
            reasons = policy.get("reasons") or {}
            if not isinstance(when, dict) or not isinstance(ranking, dict) or not isinstance(reasons, dict):
                raise ValueError(f"Routing policy {policy_code} must define object policies")
            bucket_open = candidate.bucket_remaining_aed is None or candidate.bucket_remaining_aed > 0
            bucket_fits_purchase = (
                candidate.bucket_remaining_aed is None
                or candidate.bucket_remaining_aed >= amount
            )
            target_remaining = (
                None if program.safety_target is None
                else max(program.safety_target - candidate.card_spend_before_aed, Decimal("0"))
            )
            card_state = routing_state_by_card.get(card) or {}
            current_tier = next(
                tier for tier in program.tiers if tier.code == candidate.tier_before
            )
            current_tier_rate = current_tier.rates.get(
                candidate.bucket, Decimal("0")
            )
            pace = card_state.get("pace") or {}
            pace_status_value = str(pace.get("routing_status") or pace.get("status") or "OPEN")
            checks = {
                "bucket_open": bucket_open,
                "bucket_fits_purchase": bucket_fits_purchase,
                "target_rate_positive": candidate.target_rate > 0,
                "net_value_positive": candidate.net_value_aed > 0,
                "target_unmet": target_remaining is not None and target_remaining > 0,
                "target_met": target_remaining == 0,
            }
            unknown_checks = set(when) - set(checks) - {"pace_in", "pace_not_in"}
            if unknown_checks:
                raise ValueError(
                    f"Routing policy {policy_code} uses unknown checks: "
                    + ", ".join(sorted(unknown_checks))
                )
            active = all(not required or checks[name] for name, required in when.items() if name in checks)
            pace_in = {str(value).upper() for value in when.get("pace_in", [])}
            pace_not_in = {str(value).upper() for value in when.get("pace_not_in", [])}
            active = active and (not pace_in or pace_status_value in pace_in)
            active = active and pace_status_value not in pace_not_in
            condition = str(reasons.get(pace_status_value) or reasons.get("*") or route.get("reason") or policy_code.replace("_", " ").title())
            if not active:
                continue
            groups_by_pace = ranking.get("groups_by_pace") or {"*": 100}
            if not isinstance(groups_by_pace, dict):
                raise ValueError(f"Routing policy {policy_code} groups_by_pace must be an object")
            strategy_rank = int(groups_by_pace.get(pace_status_value, groups_by_pace.get("*", 100)))
            row = {
                "card": candidate.card,
                "bucket": candidate.bucket,
                "payment_channel": channel,
                "purpose": purpose,
                "policy": policy_code,
                "policy_priority": int(route.get("priority") or 100),
                "pace_status": pace_status_value,
                "strategy_rank": strategy_rank,
                "condition": condition,
                "tracking_mode": program.tracking_mode,
                "position_mode": program.position_mode,
                "tier_before": candidate.tier_before,
                "tier_after": candidate.tier_after,
                "target_tier": candidate.target_tier,
                "target_rate_percent": _plain(candidate.target_rate * Decimal("100")),
                "estimated_net_value_aed": _plain(candidate.net_value_aed),
                "current_state_marginal_reward_aed": _plain(
                    candidate.marginal_reward_aed
                ),
                "current_state_marginal_return_percent": _plain(
                    candidate.marginal_reward_aed / amount * Decimal("100")
                    if amount
                    else Decimal("0")
                ),
                "current_tier_rate_percent": _plain(
                    current_tier_rate * Decimal("100")
                ),
                "configured_fx_fee_percent": _plain(
                    program.fx_cost_rate * Decimal("100")
                    if currency.upper() != program.base_currency else Decimal("0")
                ),
                "conditional_target_reward_aed": _plain(
                    candidate.strategic_reward_aed
                ),
                "conditional_target_rate_percent": _plain(
                    candidate.target_rate * Decimal("100")
                ),
                "estimate_basis": (
                    "CONDITIONAL_TARGET_TIER"
                    if candidate.strategic_reward_aed > candidate.marginal_reward_aed
                    else "CURRENT_TIER"
                ),
                "estimated_net_return_percent": _plain(
                    candidate.net_value_aed / amount * Decimal("100") if amount else Decimal("0")
                ),
                "card_spend_aed": _plain(candidate.card_spend_before_aed),
                "card_target_aed": None if program.safety_target is None else _plain(program.safety_target),
                "card_target_remaining_aed": None if target_remaining is None else _plain(target_remaining),
                "tier_threshold_aed": _plain(candidate.tier_threshold_aed),
                "tier_remaining_aed": _plain(candidate.tier_remaining_aed),
                "bucket_spend_aed": _plain(candidate.bucket_spend_before_aed),
                "bucket_cap_aed": None if candidate.bucket_spend_cap_aed is None else _plain(candidate.bucket_spend_cap_aed),
                "bucket_remaining_aed": None if candidate.bucket_remaining_aed is None else _plain(candidate.bucket_remaining_aed),
            }
            identity = (card, candidate.bucket, channel)
            existing = route_candidates.get(identity)
            if existing is None or int(row["policy_priority"]) < int(existing["policy_priority"]):
                route_candidates[identity] = row
        ranked_routes = sorted(
            route_candidates.values(),
            key=lambda candidate: (
                int(candidate["strategy_rank"]),
                int(candidate["policy_priority"]),
                -Decimal(str(candidate["estimated_net_value_aed"])),
                str(candidate["card"]),
            ),
        )
        for index, candidate in enumerate(ranked_routes):
            candidate["order"] = index + 1
            candidate["status"] = "PREFERRED" if index == 0 else "NEXT"
        routing_graphs.append({
            "code": str(profile.get("code") or category),
            "label": str(profile.get("label") or category.replace("_", " ").title()),
            "purchase_type": category,
            "currency": currency,
            "conditional": bool(profile.get("conditional")),
            "active": bool(ranked_routes),
            "methods": list(dict.fromkeys(str(candidate["payment_channel"]) for candidate in ranked_routes)),
            "use_card": None if not ranked_routes else ranked_routes[0]["card"],
            "avoid_cards": list(dict.fromkeys(
                str(candidate["card"])
                for candidate in ranked_routes[1:]
                if candidate["card"] != ranked_routes[0]["card"]
            )),
            "estimated_net_return_percent": None if not ranked_routes else ranked_routes[0]["estimated_net_return_percent"],
            "reason": None if not ranked_routes else ranked_routes[0]["condition"],
            "ranked_cards": ranked_routes,
        })
    return routing_graphs


def cashback_dashboard(
    programs: Iterable[Any],
    transactions: Iterable[Transaction],
    as_of: date,
    intents: Iterable[PaymentIntent],
    periods_by_card: dict[str, tuple[date, date]] | None = None,
    routing_profiles: Iterable[dict[str, object]] | None = None,
    route_policies: dict[str, dict[str, object]] | None = None,
    base_currency: str = "AED",
) -> dict[str, object]:
    rows = list(transactions)
    program_rows, routing_programs, alerts = _build_card_state(
        programs,
        rows,
        as_of,
        periods_by_card,
        base_currency,
    )
    recommendations = _build_recommendations(routing_programs, rows, intents)
    routing_graphs = _build_routing_graphs(
        routing_programs,
        program_rows,
        rows,
        routing_profiles,
        route_policies,
    )
    return {
        "schema_version": 1,
        "as_of": as_of.isoformat(),
        "currency": base_currency,
        "reward_estimate": {
            "label": "Estimated rewards based on configured terms",
            "authority": (
                "AUTHORITATIVE"
                if program_rows and all(card["provenance_authority"] == "AUTHORITATIVE" for card in program_rows)
                else "NON_AUTHORITATIVE"
            ),
        },
        "cards": program_rows,
        "recommendations": recommendations,
        "routing_graphs": routing_graphs,
        "alerts": alerts,
    }
