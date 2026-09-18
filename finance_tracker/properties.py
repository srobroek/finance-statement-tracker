from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .models import Transaction


_OWNERSHIPS = frozenset({"PERSONAL", "JOINT"})


def _identity_key(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _normalise_ownership(value: object) -> str | None:
    normalized = str(value or "").strip().casefold()
    if normalized in {"personal", "private"}:
        return "PERSONAL"
    if normalized in {"joint", "shared"}:
        return "JOINT"
    return None


def _locked(transaction: Transaction, field: str) -> bool:
    return field in set(transaction.metadata.get("locked_fields", []))


def locked_ownership_conflict(transaction: Transaction) -> bool:
    """Return whether locked owner evidence disagrees across fields."""

    locked = set(transaction.metadata.get("locked_fields", []))
    values: set[str] = set()
    if "owner" in locked:
        ownership = _normalise_ownership(transaction.owner)
        if ownership is not None:
            values.add(ownership)
    for field in ("property_ownership", "ownership"):
        if field in locked:
            ownership = _normalise_ownership(transaction.metadata.get(field))
            if ownership is not None:
                values.add(ownership)
    return len(values) > 1


def _add_review_reason(transaction: Transaction, reason: str) -> None:
    reasons = transaction.metadata.setdefault("property_review_reasons", [])
    if not isinstance(reasons, list):
        reasons = list(reasons) if isinstance(reasons, (tuple, set)) else [str(reasons)]
        transaction.metadata["property_review_reasons"] = reasons
    if reason not in reasons:
        reasons.append(reason)


def _require_review(transaction: Transaction, reason: str) -> None:
    _add_review_reason(transaction, reason)
    if not _locked(transaction, "review_required"):
        transaction.review_required = True


def _mark_ownership_conflict(transaction: Transaction) -> None:
    _require_review(transaction, "LOCKED_OWNERSHIP_CONFLICT")
    if not _locked(transaction, "tags"):
        transaction.tags.discard("shared")


@dataclass(frozen=True, slots=True)
class SharedDefaults:
    categories: frozenset[str] = frozenset()
    vendors: frozenset[str] = frozenset()


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
    ownership: str | None = None


class PropertyRegistry:
    def __init__(
        self,
        properties: Iterable[PropertyDefinition],
        *,
        shared_defaults: SharedDefaults | None = None,
    ) -> None:
        self.properties = tuple(properties)
        self.shared_defaults = shared_defaults or SharedDefaults()
        self._by_code: dict[str, PropertyDefinition] = {}
        self._by_unit: dict[str, PropertyDefinition] = {}
        self._by_name: dict[str, PropertyDefinition] = {}
        self._by_reference: dict[tuple[str, str], PropertyDefinition] = {}
        for item in self.properties:
            if item.ownership is not None and item.ownership not in _OWNERSHIPS:
                raise ValueError(f"Property {item.property_code} has invalid ownership")
            code_key = _identity_key(item.property_code)
            if code_key in self._by_code:
                raise ValueError(f"Duplicate property code: {item.property_code}")
            self._by_code[code_key] = item
            name_key = _identity_key(item.display_name)
            if name_key in self._by_name:
                raise ValueError(f"Duplicate property name: {item.display_name}")
            self._by_name[name_key] = item
            if item.rental_unit:
                unit_key = _identity_key(item.rental_unit)
                if unit_key in self._by_unit:
                    raise ValueError(f"Duplicate rental unit: {item.rental_unit}")
                self._by_unit[unit_key] = item
            for account in item.utility_accounts:
                provider = _identity_key(account.provider)
                for reference in (account.account_reference, account.premise_reference):
                    if not reference:
                        continue
                    key = (provider, _identity_key(reference))
                    if key in self._by_reference:
                        raise ValueError(
                            f"Duplicate {account.provider} property reference: {reference}"
                        )
                    self._by_reference[key] = item

    def by_code(self, value: str | None) -> PropertyDefinition | None:
        return None if not value else self._by_code.get(_identity_key(value))

    def by_rental_unit(self, value: str | None) -> PropertyDefinition | None:
        return None if not value else self._by_unit.get(_identity_key(value))

    def by_name(self, value: str | None) -> PropertyDefinition | None:
        return None if not value else self._by_name.get(_identity_key(value))

    def by_display_name(self, value: str | None) -> PropertyDefinition | None:
        return self.by_name(value)

    def by_utility_reference(
        self, provider: str, reference: str
    ) -> PropertyDefinition | None:
        if not provider or not reference:
            return None
        return self._by_reference.get(
            (_identity_key(provider), _identity_key(reference))
        )

    def is_shared_default(self, transaction: Transaction) -> bool:
        """Match only normalized facts already produced by classification."""

        return (
            _identity_key(transaction.category) in self.shared_defaults.categories
            or _identity_key(transaction.vendor) in self.shared_defaults.vendors
        )


def _shared_defaults(payload: dict[str, Any]) -> SharedDefaults:
    raw = payload.get("shared_defaults", {})
    if raw in (None, {}):
        return SharedDefaults()
    if not isinstance(raw, dict):
        raise ValueError("Property config shared_defaults must be an object")

    def values(key: str) -> frozenset[str]:
        entries = raw.get(key, [])
        if isinstance(entries, str):
            entries = [entries]
        if not isinstance(entries, list):
            raise ValueError(f"Property config shared_defaults.{key} must be an array")
        return frozenset(
            _identity_key(entry) for entry in entries if _identity_key(entry)
        )

    return SharedDefaults(categories=values("categories"), vendors=values("vendors"))


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
        ownership = _normalise_ownership(row.get("ownership"))
        rental_unit = str(row.get("rental_unit") or "").strip() or None
        tags = tuple(
            str(tag).strip().casefold()
            for tag in row.get("tags", [])
            if str(tag).strip()
        )
        if (
            not code
            or not name
            or occupancy not in {"RENTAL", "OWNER_OCCUPIED", "OTHER"}
        ):
            raise ValueError(
                "Every property requires code, display name, and valid occupancy"
            )
        if ownership is None and row.get("ownership") not in (None, ""):
            raise ValueError(f"Property {code} has invalid ownership")
        if occupancy == "RENTAL" and not rental_unit:
            raise ValueError(f"Rental property {code} requires rental_unit")
        expected_tags = (
            {"rental", f"rental:{rental_unit.casefold()}"} if rental_unit else set()
        )
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
            PropertyDefinition(
                code,
                name,
                occupancy,
                rental_unit,
                tags,
                tuple(accounts),
                ownership,
            )
        )
    return PropertyRegistry(properties, shared_defaults=_shared_defaults(payload))


def _evidence_objects(transaction: Transaction) -> tuple[dict[str, Any], ...]:
    metadata = transaction.metadata
    objects: list[dict[str, Any]] = []
    for key in ("property_evidence", "property", "utility", "property_identity"):
        value = metadata.get(key)
        if isinstance(value, dict):
            objects.append(value)
    utility_provider = metadata.get("utility_provider")
    if not utility_provider and str(
        metadata.get("provider") or ""
    ).strip().casefold() in {"dewa", "empower"}:
        utility_provider = metadata.get("provider")
    if not utility_provider and str(
        transaction.institution or ""
    ).strip().casefold() in {"dewa", "empower"}:
        utility_provider = transaction.institution
    if utility_provider is not None:
        objects.append(
            {
                "utility_provider": utility_provider,
                "utility_account_reference": metadata.get("utility_account_reference")
                or metadata.get("utility_reference")
                or metadata.get("account_reference")
                or metadata.get("reference"),
                "premise_reference": metadata.get("premise_reference"),
            }
        )
    return tuple(objects)


def _resolve_explicit_property(
    transaction: Transaction, registry: PropertyRegistry
) -> PropertyDefinition | None:
    candidates: list[PropertyDefinition] = []
    unknown = False

    def add_candidate(value: object, lookup: Any) -> None:
        nonlocal unknown
        if value is None or not str(value).strip():
            return
        resolved = lookup(str(value).strip())
        if resolved is None:
            unknown = True
        elif resolved not in candidates:
            candidates.append(resolved)

    add_candidate(transaction.property_code, registry.by_code)
    add_candidate(transaction.rental_unit, registry.by_rental_unit)

    metadata = transaction.metadata
    for key in (
        "property_name",
        "property_display_name",
        "property_identity",
        "property",
    ):
        value = metadata.get(key)
        if isinstance(value, dict):
            add_candidate(value.get("property_code"), registry.by_code)
            add_candidate(value.get("rental_unit"), registry.by_rental_unit)
            add_candidate(
                value.get("display_name") or value.get("name"),
                registry.by_name,
            )
        elif value is not None:
            resolved = registry.by_code(str(value).strip()) or registry.by_name(
                str(value).strip()
            )
            if resolved is None and str(value).strip():
                unknown = True
            elif resolved is not None and resolved not in candidates:
                candidates.append(resolved)

    for evidence in _evidence_objects(transaction):
        add_candidate(evidence.get("property_code"), registry.by_code)
        add_candidate(evidence.get("rental_unit"), registry.by_rental_unit)
        add_candidate(
            evidence.get("property_name")
            or evidence.get("display_name")
            or evidence.get("name"),
            registry.by_name,
        )
        provider = evidence.get("utility_provider") or evidence.get("provider")
        for key in (
            "utility_account_reference",
            "utility_reference",
            "account_reference",
            "premise_reference",
            "reference",
        ):
            reference = evidence.get(key)
            if reference is None or not str(reference).strip():
                continue
            resolved = registry.by_utility_reference(
                str(provider or ""), str(reference)
            )
            if resolved is None:
                unknown = True
            elif resolved not in candidates:
                candidates.append(resolved)

    if len(candidates) > 1:
        if (
            registry.by_code(transaction.property_code)
            and registry.by_rental_unit(transaction.rental_unit)
            and registry.by_code(transaction.property_code)
            != registry.by_rental_unit(transaction.rental_unit)
        ):
            _add_review_reason(transaction, "PROPERTY_CODE_RENTAL_UNIT_CONFLICT")
        _require_review(transaction, "PROPERTY_IDENTITY_CONFLICT")
        return None
    if unknown:
        _require_review(transaction, "UNKNOWN_CONFIGURED_PROPERTY")
        return None
    return candidates[0] if candidates else None


def _manual_ownership(transaction: Transaction) -> str | None:
    if locked_ownership_conflict(transaction):
        return None
    metadata = transaction.metadata
    for field in ("property_ownership", "ownership"):
        if _locked(transaction, field):
            ownership = _normalise_ownership(metadata.get(field))
            if ownership is not None:
                return ownership
    if _locked(transaction, "owner"):
        return _normalise_ownership(transaction.owner)
    return None


def _apply_shared_default(transaction: Transaction, registry: PropertyRegistry) -> None:
    if not registry.is_shared_default(transaction) or _locked(transaction, "tags"):
        return
    if locked_ownership_conflict(transaction):
        _mark_ownership_conflict(transaction)
        return
    if _manual_ownership(transaction) == "PERSONAL":
        return
    transaction.tags.add("shared")


def project_property_tags(
    transaction: Transaction, registry: PropertyRegistry
) -> PropertyDefinition | None:
    """Project explicit property evidence without inferring from spend details.

    Property ownership is a separate dimension from occupancy. A property
    definition can therefore be jointly owned while rented, or privately owned
    while carrying a rental-unit identity. Manual field/tag corrections remain
    authoritative through every projection replay.
    """
    if locked_ownership_conflict(transaction):
        _mark_ownership_conflict(transaction)
        return None

    resolved = _resolve_explicit_property(transaction, registry)
    if resolved is None:
        has_explicit_hints = (
            transaction.property_code
            or transaction.rental_unit
            or bool(_evidence_objects(transaction))
            or any(
                key in transaction.metadata
                for key in (
                    "property_name",
                    "property_display_name",
                    "property_identity",
                    "property",
                    "utility_provider",
                    "utility_account_reference",
                    "utility_reference",
                    "premise_reference",
                )
            )
        )
        if has_explicit_hints:
            _require_review(transaction, "UNKNOWN_CONFIGURED_PROPERTY")
        elif _manual_ownership(transaction) == "PERSONAL" and not _locked(
            transaction, "tags"
        ):
            transaction.tags.discard("shared")
        else:
            _apply_shared_default(transaction, registry)
        return None

    if not transaction.property_code and not _locked(transaction, "property_code"):
        transaction.property_code = resolved.property_code
    if (
        not transaction.rental_unit
        and resolved.rental_unit
        and not _locked(transaction, "rental_unit")
    ):
        transaction.rental_unit = resolved.rental_unit
    ownership = _manual_ownership(transaction) or _normalise_ownership(
        resolved.ownership
    )
    if ownership is not None:
        if not _locked(transaction, "property_ownership"):
            transaction.metadata["property_ownership"] = ownership
        elif (
            _normalise_ownership(transaction.metadata.get("property_ownership"))
            != ownership
        ):
            _require_review(transaction, "LOCKED_OWNERSHIP_CONFLICT")

    if _locked(transaction, "tags"):
        return resolved

    if resolved.occupancy == "RENTAL":
        transaction.tags.discard("home")
        transaction.tags = {
            tag
            for tag in transaction.tags
            if not str(tag).casefold().startswith("rental:")
        }
    elif resolved.occupancy == "OWNER_OCCUPIED":
        transaction.tags = {
            tag
            for tag in transaction.tags
            if str(tag).casefold() != "rental"
            and not str(tag).casefold().startswith("rental:")
        }
    transaction.tags.update(resolved.tags)
    transaction.tags.add("property")
    if ownership == "PERSONAL":
        transaction.tags.discard("shared")
    elif ownership == "JOINT":
        transaction.tags.add("shared")
    return resolved
