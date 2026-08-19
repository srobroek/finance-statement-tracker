from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping

from .account_completeness import AccountIdentity
from .wealth import FXSnapshot, WealthSnapshot, build_actual_wealth_proposal


def _timestamp(value: Any, context: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{context} is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{context} must be an ISO timestamp") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _minor(value: Any, context: str) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{context} must be a decimal amount") from exc
    return int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _freshness(
    as_of: datetime,
    evaluated_at: datetime,
    stale_after_seconds: int,
) -> str:
    checked = evaluated_at if evaluated_at.tzinfo else evaluated_at.replace(tzinfo=timezone.utc)
    age = (checked - as_of).total_seconds()
    if age < 0:
        return "AS_OF_IN_FUTURE"
    return "STALE" if age > stale_after_seconds else "FRESH"


def build_fab_opening_anchor_proposal(
    capture: Mapping[str, Any],
    *,
    provider_account_id: str,
    account_name: str,
    inventory_complete: bool,
    evaluated_at: datetime,
    stale_after_seconds: int = 604800,
) -> dict[str, Any]:
    source = capture.get("source")
    account = capture.get("account")
    if not isinstance(source, Mapping) or not isinstance(account, Mapping):
        raise ValueError("FAB capture requires source and account objects")
    if str(source.get("provider") or "").strip().casefold() != "fab":
        raise ValueError("FAB proposal received a different provider capture")
    if provider_account_id != "fab:current:2001":
        raise ValueError("Only the evidenced FAB current account 2001 may be proposed")
    if str(account.get("account_last4") or "") != "2001":
        raise ValueError("FAB capture does not evidence current account 2001")
    if str(account.get("currency") or "").upper() != "AED":
        raise ValueError("FAB current account 2001 must be captured in AED")

    as_of = _timestamp(account.get("balance_as_of"), "account.balance_as_of")
    freshness = _freshness(as_of, evaluated_at, stale_after_seconds)
    blockers: list[str] = []
    if not inventory_complete:
        blockers.append("FAB_PORTAL_ACCOUNT_INVENTORY_REQUIRED")
    if freshness == "STALE":
        blockers.append("FAB_BALANCE_SNAPSHOT_STALE")
    elif freshness == "AS_OF_IN_FUTURE":
        blockers.append("FAB_BALANCE_AS_OF_IN_FUTURE")

    rows = capture.get("rows")
    if not isinstance(rows, list):
        raise ValueError("FAB capture rows must be an array")
    net_activity_minor = 0
    transaction_dates: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"FAB capture rows[{index}] must be an object")
        direction = str(row.get("direction") or "").upper()
        if direction not in {"CREDIT", "DEBIT"}:
            raise ValueError(f"FAB capture rows[{index}] has invalid direction")
        amount_minor = _minor(row.get("amount_aed"), f"rows[{index}].amount_aed")
        if amount_minor < 0:
            raise ValueError(f"FAB capture rows[{index}] amount must be unsigned")
        net_activity_minor += amount_minor if direction == "CREDIT" else -amount_minor
        transaction_dates.append(str(row.get("transaction_date") or ""))
    if not transaction_dates or any(not value for value in transaction_dates):
        raise ValueError("FAB capture requires dated activity to derive its opening anchor")
    source_balance_minor = _minor(account.get("balance"), "account.balance")
    opening_balance_minor = source_balance_minor - net_activity_minor
    return {
        "schema_version": 1,
        "mode": "PROPOSAL_ONLY",
        "actual_writes_allowed": False,
        "status": "BLOCKED" if blockers else "READY_FOR_REVIEW",
        "blockers": blockers,
        "inventory_status": "COMPLETE" if inventory_complete else "INCOMPLETE",
        "account": {
            "provider_account_id": provider_account_id,
            "name": account_name,
            "action": "CREATE_OR_VERIFY_FROM_SOURCE_DATED_OPENING_ANCHOR",
            "type": "checking",
            "offbudget": False,
            "currency": "AED",
            "opening_balance_anchor": {
                "balance_minor": opening_balance_minor,
                "before_transaction_date": min(transaction_dates),
                "source_as_of_balance_minor": source_balance_minor,
                "source_as_of": as_of.isoformat(),
                "captured_activity_net_minor": net_activity_minor,
                "derivation": "source_as_of_balance_minor - captured_activity_net_minor",
                "source_identity": f"browser-capture:{capture.get('capture_id')}",
                "source_field": "account.balance",
                "freshness": freshness,
                "stale_after_seconds": stale_after_seconds,
            },
            "transaction_boundary": {
                "captured_rows": "IMPORT_AFTER_OPENING_ANCHOR",
                "captured_row_count": len(rows),
                "covered_through": max(transaction_dates),
            },
            "synthetic_balancing_row_allowed": False,
            "derived_adjustment": None,
            "review_required": True,
        },
    }


def build_sarwa_position_sidecar(
    snapshot: WealthSnapshot,
    *,
    evaluated_at: datetime,
) -> dict[str, Any]:
    freshness = snapshot.freshness_status(evaluated_at)
    blockers: list[str] = []
    if freshness == "STALE":
        blockers.append("WEALTH_SNAPSHOT_STALE")
    elif freshness == "AS_OF_IN_FUTURE":
        blockers.append("WEALTH_AS_OF_IN_FUTURE")
    if snapshot.reconciliation.status != "RECONCILED":
        blockers.append("PORTFOLIO_TOTAL_MISMATCH")
    if any(row.reconciliation.status == "MISMATCH" for row in snapshot.portfolios):
        blockers.append("POSITION_TOTAL_MISMATCH")

    portfolios = []
    for portfolio in snapshot.portfolios:
        portfolios.append({
            "provider_account_id": portfolio.provider_account_id,
            "display_name": portfolio.display_name,
            "product_type": portfolio.product_type,
            "as_of": portfolio.as_of.isoformat(),
            "currency": portfolio.currency,
            "total_value": str(portfolio.total_value),
            "cash_value": str(portfolio.cash_value) if portfolio.cash_value is not None else None,
            "closed": portfolio.closed,
            "include_in_net_worth": portfolio.include_in_net_worth,
            "position_feed_status": (
                "AVAILABLE" if portfolio.positions else "COMPONENTS_UNAVAILABLE"
            ),
            "positions": [
                {
                    "instrument_id": position.instrument_id,
                    "ticker": position.ticker,
                    "name": position.name,
                    "units": str(position.units),
                    "market_value": str(position.market_value),
                    "unit_price": (
                        str(position.unit_price.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))
                        if position.unit_price is not None else None
                    ),
                    "allocation_pct": (
                        str(position.allocation_pct)
                        if position.allocation_pct is not None else None
                    ),
                    "performance_pct": (
                        str(position.performance_pct)
                        if position.performance_pct is not None else None
                    ),
                }
                for position in portfolio.positions
            ],
            "reconciliation": {
                "status": portfolio.reconciliation.status,
                "expected": str(portfolio.reconciliation.expected),
                "actual": (
                    str(portfolio.reconciliation.actual)
                    if portfolio.reconciliation.actual is not None else None
                ),
                "difference": (
                    str(portfolio.reconciliation.difference)
                    if portfolio.reconciliation.difference is not None else None
                ),
                "tolerance": str(portfolio.reconciliation.tolerance),
            },
        })
    return {
        "schema_version": 1,
        "mode": "READ_ONLY_POSITION_SIDECAR",
        "actual_writes_allowed": False,
        "positions_are_ledger_transactions": False,
        "status": "BLOCKED" if blockers else "READY_FOR_REVIEW",
        "blockers": blockers,
        "snapshot_id": snapshot.snapshot_id,
        "source_identity": snapshot.source_identity,
        "captured_at": snapshot.captured_at.isoformat(),
        "as_of": snapshot.as_of.isoformat(),
        "freshness": freshness,
        "stale_after_seconds": snapshot.stale_after_seconds,
        "portfolios": portfolios,
    }


def build_sarwa_account_proposal(
    snapshot: WealthSnapshot,
    fx_snapshot: FXSnapshot | None,
    *,
    evaluated_at: datetime,
) -> dict[str, Any]:
    proposal = build_actual_wealth_proposal(
        snapshot,
        fx_snapshot,
        evaluated_at=evaluated_at,
    )
    proposal["account_model"] = "OFF_BUDGET_VALUATION_ACCOUNTS"
    proposal["position_sidecar_required"] = True
    return proposal


def build_adcb_closed_zero_assertion(
    account: AccountIdentity,
    *,
    issuer_closing_balance_minor: int | None = None,
    actual_balance_minor: int | None = None,
    actual_closed: bool | None = None,
) -> dict[str, Any]:
    if account.provider_id != "adcb" or account.lifecycle_status != "CLOSED":
        raise ValueError("ADCB zero assertion requires the closed historical ADCB account")
    if account.expected_balance_minor != 0 or not account.balance_reconciliation_required:
        raise ValueError("Closed ADCB account contract must require exact zero")

    blockers: list[str] = []
    if issuer_closing_balance_minor is None:
        blockers.append("EVIDENCED_CLOSING_PAYMENT_REQUIRED")
    elif issuer_closing_balance_minor != 0:
        blockers.append("ISSUER_CLOSING_BALANCE_NOT_ZERO")
    if actual_balance_minor is None or actual_closed is None:
        blockers.append("PRODUCTION_READBACK_REQUIRED")
    else:
        if actual_balance_minor != 0:
            blockers.append("ACTUAL_CLOSED_BALANCE_NOT_ZERO")
        if not actual_closed:
            blockers.append("ACTUAL_ACCOUNT_NOT_CLOSED")

    return {
        "schema_version": 1,
        "mode": "READ_ONLY_ASSERTION",
        "actual_writes_allowed": False,
        "status": "BLOCKED" if blockers else "PASS",
        "blockers": blockers,
        "provider_account_id": account.provider_account_id,
        "expected_balance_minor": 0,
        "expected_closed": True,
        "retain_history": account.retain_history,
        "include_in_active_routing": account.include_in_active_routing,
        "issuer_closing_balance_minor": issuer_closing_balance_minor,
        "actual_balance_minor": actual_balance_minor,
        "actual_closed": actual_closed,
        "synthetic_balancing_row_allowed": False,
        "reconciliation_policy": "EVIDENCED_HISTORY_AND_CLOSING_PAYMENT_ONLY",
    }
