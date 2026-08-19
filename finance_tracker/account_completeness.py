from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


_STABLE_ID = re.compile(r"^[a-z0-9]+(?::[a-z0-9-]+)+$")
_ACCOUNT_TYPES = {"checking", "savings", "investment", "trade", "credit"}
_INVENTORY_STATUSES = {"COMPLETE", "INCOMPLETE"}


@dataclass(frozen=True, slots=True)
class AccountIdentity:
    provider_id: str
    provider_account_id: str
    display_name: str
    actual_account_name: str | None
    account_type: str
    currency: str
    last4: str | None
    owner: str | None
    lifecycle_status: str
    include_in_actual: bool
    include_in_net_worth: bool
    balance_source: str | None
    balance_as_of: str | None
    active: bool
    retain_history: bool
    include_in_active_routing: bool
    expected_balance_minor: int | None
    balance_reconciliation_required: bool


@dataclass(frozen=True, slots=True)
class ProviderInventory:
    provider_id: str
    inventory_status: str
    discovery_required: bool
    blocker: str | None
    evidence: str | None


@dataclass(frozen=True, slots=True)
class AccountCompletenessManifest:
    schema_version: int
    accounts: tuple[AccountIdentity, ...]
    providers: tuple[ProviderInventory, ...]

    def provider(self, provider_id: str) -> ProviderInventory:
        matches = [row for row in self.providers if row.provider_id == provider_id]
        if len(matches) != 1:
            raise ValueError(f"Provider inventory not found uniquely: {provider_id}")
        return matches[0]


def _load_payload(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return dict(source)
    payload = json.loads(Path(source).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Account completeness manifest must be an object")
    return payload


def load_account_completeness_manifest(
    source: str | Path | Mapping[str, Any],
) -> AccountCompletenessManifest:
    payload = _load_payload(source)
    if payload.get("schema_version") != 1:
        raise ValueError("Account completeness schema_version must be 1")
    raw_accounts = payload.get("accounts")
    raw_providers = payload.get("providers")
    if not isinstance(raw_accounts, list) or not isinstance(raw_providers, list):
        raise ValueError("Account completeness manifest requires accounts and providers")

    accounts: list[AccountIdentity] = []
    identities: set[str] = set()
    for raw in raw_accounts:
        if not isinstance(raw, Mapping):
            raise ValueError("Account entries must be objects")
        provider_id = str(raw.get("provider_id") or "").strip().casefold()
        identity = str(raw.get("provider_account_id") or "").strip()
        display_name = str(raw.get("display_name") or "").strip()
        account_type = str(raw.get("account_type") or "").strip().casefold()
        currency = str(raw.get("currency") or "").strip().upper()
        last4 = str(raw.get("last4") or "").strip() or None
        if not provider_id or not display_name:
            raise ValueError("Account provider_id and display_name are required")
        if not _STABLE_ID.fullmatch(identity) or not identity.startswith(f"{provider_id}:"):
            raise ValueError(f"Unsafe or provider-mismatched account identity: {identity}")
        if identity in identities:
            raise ValueError(f"Duplicate provider_account_id: {identity}")
        if identity == display_name:
            raise ValueError("Display names must not be used as stable account identities")
        if account_type not in _ACCOUNT_TYPES:
            raise ValueError(f"Unsupported account type: {account_type}")
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError(f"Invalid account currency: {currency}")
        if last4 and (len(last4) != 4 or not last4.isdigit()):
            raise ValueError(f"Account last4 must be exactly four digits: {identity}")
        identities.add(identity)
        accounts.append(AccountIdentity(
            provider_id=provider_id,
            provider_account_id=identity,
            display_name=display_name,
            actual_account_name=str(raw.get("actual_account_name") or "").strip() or None,
            account_type=account_type,
            currency=currency,
            last4=last4,
            owner=str(raw.get("owner") or "").strip() or None,
            lifecycle_status=str(raw.get("lifecycle_status") or "ACTIVE").upper(),
            include_in_actual=bool(raw.get("include_in_actual", True)),
            include_in_net_worth=bool(raw.get("include_in_net_worth", True)),
            balance_source=str(raw.get("balance_source") or "").strip() or None,
            balance_as_of=str(raw.get("balance_as_of") or "").strip() or None,
            active=bool(raw.get("active", True)),
            retain_history=bool(raw.get("retain_history", True)),
            include_in_active_routing=bool(raw.get("include_in_active_routing", True)),
            expected_balance_minor=(
                int(raw["expected_balance_minor"])
                if raw.get("expected_balance_minor") is not None else None
            ),
            balance_reconciliation_required=bool(
                raw.get("balance_reconciliation_required", False)
            ),
        ))
        if accounts[-1].lifecycle_status == "CLOSED" and accounts[-1].active:
            raise ValueError(f"Closed account cannot be active: {identity}")
        if accounts[-1].lifecycle_status == "CLOSED" and accounts[-1].include_in_active_routing:
            raise ValueError(f"Closed account cannot participate in active routing: {identity}")
        if (
            accounts[-1].balance_reconciliation_required
            and accounts[-1].expected_balance_minor is None
        ):
            raise ValueError(f"Reconciled account requires expected_balance_minor: {identity}")

    providers: list[ProviderInventory] = []
    provider_ids: set[str] = set()
    for raw in raw_providers:
        if not isinstance(raw, Mapping):
            raise ValueError("Provider inventory entries must be objects")
        provider_id = str(raw.get("provider_id") or "").strip().casefold()
        status = str(raw.get("inventory_status") or "").strip().upper()
        if not provider_id or provider_id in provider_ids:
            raise ValueError(f"Missing or duplicate provider inventory: {provider_id}")
        if status not in _INVENTORY_STATUSES:
            raise ValueError(f"Invalid provider inventory status: {status}")
        provider_ids.add(provider_id)
        providers.append(ProviderInventory(
            provider_id=provider_id,
            inventory_status=status,
            discovery_required=bool(raw.get("discovery_required", False)),
            blocker=str(raw.get("blocker") or "").strip() or None,
            evidence=str(raw.get("evidence") or "").strip() or None,
        ))
    if {row.provider_id for row in accounts} - provider_ids:
        raise ValueError("Every account provider requires a provider inventory entry")
    return AccountCompletenessManifest(1, tuple(accounts), tuple(providers))


def validate_account_completeness(
    manifest: AccountCompletenessManifest,
    *,
    observed_provider_account_ids: set[str],
    provider_id: str,
    observed_balances_minor: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    inventory = manifest.provider(provider_id)
    expected = {
        row.provider_account_id
        for row in manifest.accounts
        if row.provider_id == provider_id
    }
    missing = sorted(expected - observed_provider_account_ids)
    unexpected = sorted(observed_provider_account_ids - expected)
    observed_balances = dict(observed_balances_minor or {})
    balance_mismatches: list[dict[str, Any]] = []
    for row in manifest.accounts:
        if row.provider_id != provider_id or not row.balance_reconciliation_required:
            continue
        observed = observed_balances.get(row.provider_account_id)
        if observed is None or observed != row.expected_balance_minor:
            balance_mismatches.append({
                "provider_account_id": row.provider_account_id,
                "expected_balance_minor": row.expected_balance_minor,
                "observed_balance_minor": observed,
            })
    blockers: list[str] = []
    if inventory.inventory_status != "COMPLETE":
        blockers.append(inventory.blocker or f"{provider_id.upper()}_ACCOUNT_INVENTORY_REQUIRED")
    if missing:
        blockers.append("EXPECTED_ACCOUNTS_MISSING")
    if unexpected:
        blockers.append("UNEXPECTED_ACCOUNTS_PRESENT")
    if balance_mismatches:
        blockers.append("ACCOUNT_BALANCE_RECONCILIATION_FAILED")
    if inventory.inventory_status != "COMPLETE":
        status = "INCOMPLETE_SOURCE_INVENTORY"
    elif missing or unexpected:
        status = "ACCOUNT_SET_MISMATCH"
    elif balance_mismatches:
        status = "ACCOUNT_BALANCE_MISMATCH"
    else:
        status = "COMPLETE"
    return {
        "schema_version": 1,
        "provider_id": provider_id,
        "status": status,
        "production_write_allowed": status == "COMPLETE",
        "expected": sorted(expected),
        "observed": sorted(observed_provider_account_ids),
        "missing": missing,
        "unexpected": unexpected,
        "balance_mismatches": balance_mismatches,
        "blockers": blockers,
        "inventory_evidence": inventory.evidence,
    }
