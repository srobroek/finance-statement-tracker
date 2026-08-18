from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import Transaction


@dataclass(frozen=True, slots=True)
class UtilityAccount:
    provider: str
    account_reference: str
    premise_reference: str | None = None


@dataclass(frozen=True, slots=True)
class PropertyDefinition:
    property_code: str
    display_name: str
    occupancy: str
    rental_unit: str | None
    tags: tuple[str, ...]
    utility_accounts: tuple[UtilityAccount, ...]


class PropertyRegistry:
    def __init__(self, properties: Iterable[PropertyDefinition]) -> None:
        self.properties = tuple(properties)
        self._by_code: dict[str, PropertyDefinition] = {}
        self._by_unit: dict[str, PropertyDefinition] = {}
        self._by_reference: dict[tuple[str, str], PropertyDefinition] = {}
        for item in self.properties:
            code_key = item.property_code.casefold()
            if code_key in self._by_code:
                raise ValueError(f"Duplicate property code: {item.property_code}")
            self._by_code[code_key] = item
            if item.rental_unit:
                unit_key = item.rental_unit.casefold()
                if unit_key in self._by_unit:
                    raise ValueError(f"Duplicate rental unit: {item.rental_unit}")
                self._by_unit[unit_key] = item
            for account in item.utility_accounts:
                provider = account.provider.casefold()
                for reference in (account.account_reference, account.premise_reference):
                    if not reference:
                        continue
                    key = (provider, reference.casefold())
                    if key in self._by_reference:
                        raise ValueError(
                            f"Duplicate {account.provider} property reference: {reference}"
                        )
                    self._by_reference[key] = item

    def by_code(self, value: str | None) -> PropertyDefinition | None:
        return None if not value else self._by_code.get(value.casefold())

    def by_rental_unit(self, value: str | None) -> PropertyDefinition | None:
        return None if not value else self._by_unit.get(value.casefold())

    def by_utility_reference(
        self, provider: str, reference: str
    ) -> PropertyDefinition | None:
        return self._by_reference.get((provider.casefold(), reference.casefold()))


def load_property_registry(path: str | Path) -> PropertyRegistry:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Property config schema_version must be 1")
    rows = payload.get("properties")
    if not isinstance(rows, list):
        raise ValueError("Property config requires a properties array")
    properties: list[PropertyDefinition] = []
    for row in rows:
        code = str(row.get("property_code") or "").strip()
        name = str(row.get("display_name") or "").strip()
        occupancy = str(row.get("occupancy") or "").strip().upper()
        rental_unit = str(row.get("rental_unit") or "").strip() or None
        tags = tuple(str(tag).strip().casefold() for tag in row.get("tags", []) if str(tag).strip())
        if not code or not name or occupancy not in {"RENTAL", "OWNER_OCCUPIED", "OTHER"}:
            raise ValueError("Every property requires code, display name, and valid occupancy")
        if occupancy == "RENTAL" and not rental_unit:
            raise ValueError(f"Rental property {code} requires rental_unit")
        expected_tags = {"rental", f"rental:{rental_unit.casefold()}"} if rental_unit else set()
        if rental_unit and not expected_tags.issubset(tags):
            raise ValueError(
                f"Rental property {code} must include separate #rental and #rental:{rental_unit.casefold()} tags"
            )
        accounts: list[UtilityAccount] = []
        for account in row.get("utility_accounts", []):
            provider = str(account.get("provider") or "").strip().upper()
            reference = str(account.get("account_reference") or "").strip()
            premise = str(account.get("premise_reference") or "").strip() or None
            if not provider or not reference:
                raise ValueError(f"Property {code} has an invalid utility account")
            accounts.append(UtilityAccount(provider, reference, premise))
        properties.append(
            PropertyDefinition(code, name, occupancy, rental_unit, tags, tuple(accounts))
        )
    return PropertyRegistry(properties)


def project_property_tags(
    transaction: Transaction, registry: PropertyRegistry
) -> PropertyDefinition | None:
    """Project configured property tags after evidence/AI resolves an identity.

    This function never guesses from vendor or amount. A property or rental-unit
    identity must already be present from explicit evidence or a manual override.
    """
    by_code = registry.by_code(transaction.property_code)
    by_unit = registry.by_rental_unit(transaction.rental_unit)
    if by_code and by_unit and by_code != by_unit:
        transaction.review_required = True
        transaction.metadata.setdefault("property_review_reasons", []).append(
            "PROPERTY_CODE_RENTAL_UNIT_CONFLICT"
        )
        return None
    resolved = by_code or by_unit
    if resolved is None:
        if transaction.property_code or transaction.rental_unit:
            transaction.review_required = True
            transaction.metadata.setdefault("property_review_reasons", []).append(
                "UNKNOWN_CONFIGURED_PROPERTY"
            )
        return None
    if not transaction.property_code:
        transaction.property_code = resolved.property_code
    if not transaction.rental_unit and resolved.rental_unit:
        transaction.rental_unit = resolved.rental_unit
    transaction.tags.update(resolved.tags)
    return resolved
