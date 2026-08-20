from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping

from .account_completeness import AccountCompletenessManifest, AccountIdentity
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


def build_fab_inventory_proposal(
    inventory_capture: Mapping[str, Any],
    manifest: AccountCompletenessManifest,
    *,
    evaluated_at: datetime,
    stale_after_seconds: int = 604800,
) -> dict[str, Any]:
    source = inventory_capture.get("source")
    raw_accounts = inventory_capture.get("accounts")
    if not isinstance(source, Mapping) or not isinstance(raw_accounts, list):
        raise ValueError("FAB inventory capture requires source and accounts")
    if str(source.get("provider") or "").strip().casefold() != "fab":
        raise ValueError("FAB inventory proposal received a different provider capture")
    if inventory_capture.get("inventory_complete") is not True:
        raise ValueError("FAB portal inventory is not marked complete")

    expected = {
        row.provider_account_id: row
        for row in manifest.accounts if row.provider_id == "fab"
    }
    observed: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(raw_accounts):
        if not isinstance(raw, Mapping):
            raise ValueError(f"FAB inventory accounts[{index}] must be an object")
        identity = str(raw.get("provider_account_id") or "")
        if not identity.startswith("fab:") or identity in observed:
            raise ValueError(f"Unsafe or duplicate FAB inventory identity: {identity}")
        observed[identity] = raw
    if set(expected) != set(observed):
        raise ValueError("FAB manifest and complete portal inventory account sets differ")

    accounts: list[dict[str, Any]] = []
    blockers: list[str] = []
    for identity in sorted(expected):
        contract = expected[identity]
        raw = observed[identity]
        if str(raw.get("last4") or "") != (contract.last4 or ""):
            raise ValueError(f"FAB last4 mismatch for {identity}")
        if str(raw.get("currency") or "").upper() != contract.currency:
            raise ValueError(f"FAB currency mismatch for {identity}")
        if str(raw.get("balance_sign") or "").upper() != contract.balance_sign:
            raise ValueError(f"FAB balance sign mismatch for {identity}")
        as_of = _timestamp(raw.get("balance_as_of"), f"{identity}.balance_as_of")
        freshness = _freshness(as_of, evaluated_at, stale_after_seconds)
        if freshness == "STALE":
            blockers.append(f"FAB_BALANCE_SNAPSHOT_STALE:{identity}")
        elif freshness == "AS_OF_IN_FUTURE":
            blockers.append(f"FAB_BALANCE_AS_OF_IN_FUTURE:{identity}")
        if contract.balance_sign == "LIABILITY_NEGATIVE":
            source_unsigned = int(raw.get("observed_outstanding_minor"))
            signed_balance = int(raw.get("actual_signed_balance_minor"))
            if source_unsigned < 0 or signed_balance != -source_unsigned:
                raise ValueError(f"FAB liability sign is invalid for {identity}")
        else:
            signed_balance = int(raw.get("observed_balance_minor"))
        if signed_balance != contract.expected_balance_minor:
            raise ValueError(f"FAB evidenced balance differs from manifest for {identity}")
        accounts.append({
            "provider_account_id": identity,
            "name": contract.actual_account_name or contract.display_name,
            "action": "CREATE_OR_VERIFY_FROM_SOURCE_DATED_OPENING_ANCHOR",
            "type": contract.account_type,
            "offbudget": contract.actual_offbudget,
            "currency": contract.currency,
            "opening_balance_anchor": {
                "balance_minor": signed_balance,
                "source_as_of": as_of.isoformat(),
                "source_identity": f"browser-capture:{inventory_capture.get('capture_id')}",
                "freshness": freshness,
                "stale_after_seconds": stale_after_seconds,
                "balance_sign": contract.balance_sign,
            },
            "history_policy": "NO_HISTORY_REQUIRED_FOR_CURRENT_BALANCE",
            "reconciliation_method": "ACTUAL_NATIVE_RECONCILIATION_ADJUSTMENT",
            "reconciliation_adjustment_allowed": True,
            "synthetic_balancing_row_allowed": False,
            "fx_snapshot_required": contract.currency != "AED" and signed_balance != 0,
            "include_in_net_worth": contract.include_in_net_worth,
            "review_required": True,
        })
    return {
        "schema_version": 1,
        "mode": "PROPOSAL_ONLY",
        "actual_writes_allowed": False,
        "status": "BLOCKED" if blockers else "READY_FOR_REVIEW",
        "blockers": list(dict.fromkeys(blockers)),
        "inventory_status": "COMPLETE",
        "inventory_capture_id": inventory_capture.get("capture_id"),
        "accounts": accounts,
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
    issuer_evidence_id: str | None = None,
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
    if issuer_closing_balance_minor is not None and not str(issuer_evidence_id or "").strip():
        blockers.append("ISSUER_CLOSING_EVIDENCE_ID_REQUIRED")
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
        "issuer_evidence_id": issuer_evidence_id,
        "actual_balance_minor": actual_balance_minor,
        "actual_closed": actual_closed,
        "reconciliation_adjustment_allowed": True,
        "synthetic_balancing_row_allowed": False,
        "reconciliation_policy": "ACTUAL_NATIVE_RECONCILIATION_ADJUSTMENT",
    }
