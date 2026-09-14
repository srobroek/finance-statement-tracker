"""Closed, immutable proposal-only schemas for RuleAnalysisWorkbench.

This module deliberately contains data contracts and validation only.  It has no
provider, network, ledger, Actual, SQLite, or persistence integration.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Final

SCHEMA_VERSION: Final[int] = 1

CANDIDATE_KINDS: Final[tuple[str, ...]] = (
    "DETERMINISTIC_RULE",
    "AGENT_POLICY",
)
TARGET_KINDS: Final[tuple[str, ...]] = (
    "DETERMINISTIC_WRITABLE",
    "SUGGESTION_ONLY",
)
DECISIONS: Final[tuple[str, ...]] = ("ACCEPTED", "REJECTED", "REVIEW_ONLY")

DETERMINISTIC_WRITABLE_FIELDS: Final[tuple[str, ...]] = (
    "vendor",
    "category",
    "subcategory",
    "tags",
    "evidence_policy",
    "review_required",
    "category_recommendation",
    "is_subscription",
    "property_code",
    "rental_unit",
    "rule_recommendation",
)
SUGGESTION_ONLY_FIELDS: Final[tuple[str, ...]] = ("channel", "reward_bucket")

PROTECTED_IDENTITY_FIELDS: Final[tuple[str, ...]] = (
    "transaction_id",
    "transaction_at",
    "card",
    "account",
    "institution",
    "account_last4",
    "merchant_raw",
    "source_type",
    "source_message_id",
)
PROTECTED_ECONOMIC_FIELDS: Final[tuple[str, ...]] = (
    "amount_aed",
    "amount_original",
    "currency",
    "source_direction",
)
PROTECTED_ACTUAL_STRUCTURAL_FIELDS: Final[tuple[str, ...]] = (
    "transaction_type",
    "is_refund",
    "reconciliation_status",
    "existing_link",
    "actual_transaction_id",
    "deduplication_key",
)
PROTECTED_FIELDS: Final[tuple[str, ...]] = (
    *PROTECTED_IDENTITY_FIELDS,
    *PROTECTED_ECONOMIC_FIELDS,
    *PROTECTED_ACTUAL_STRUCTURAL_FIELDS,
)

FORBIDDEN_SIDE_EFFECT_CAPABILITIES: Final[tuple[str, ...]] = (
    "APPLY_ACTUAL",
    "PROMOTE_PROPOSAL",
    "MUTATE_LEDGER",
    "NETWORK",
    "PROVIDER_CALL",
    "SQLITE_WRITE",
    "PERSIST",
    "SEND_NOTIFICATION",
)

_OBSERVATION_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "observation_id",
        "observation_hash",
        "transaction_id",
        "transaction_at",
        "observed_at",
        "card",
        "account",
        "institution",
        "account_last4",
        "merchant_raw",
        "amount_aed",
        "amount_original",
        "currency",
        "source_direction",
        "source_type",
        "source_message_id",
        "transaction_type",
        "is_refund",
        "reconciliation_status",
        "vendor",
        "category",
        "subcategory",
        "owner",
        "property_code",
        "rental_unit",
        "channel",
        "reward_bucket",
        "tags",
        "evidence_policy",
        "evidence_status",
        "review_required",
        "is_subscription",
        "manual_locked_fields",
        "existing_link",
        "evidence_refs",
        "redacted_metadata",
    }
)
_CANDIDATE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "candidate_id",
        "candidate_hash",
        "candidate_kind",
        "observation_id",
        "observation_hash",
        "scope_hash",
        "input_hash",
        "target_fields",
        "reason_code",
        "confidence",
        "evidence_refs",
        "payload",
        "proposal_only",
    }
)
_PROPOSAL_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "proposal_id",
        "proposal_hash",
        "candidate_id",
        "candidate_hash",
        "candidate_kind",
        "observation_id",
        "observation_hash",
        "scope_hash",
        "input_hash",
        "field",
        "target_kind",
        "value",
        "existing_value",
        "reason_code",
        "confidence",
        "evidence_refs",
        "proposal_only",
    }
)
_EFFECT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "effect_id",
        "effect_hash",
        "proposal_id",
        "proposal_hash",
        "observation_id",
        "observation_hash",
        "field",
        "target_kind",
        "value",
        "proposal_only",
        "direct_commit_allowed",
        "capabilities",
    }
)
_FEEDBACK_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "feedback_id",
        "feedback_hash",
        "proposal_id",
        "proposal_hash",
        "observation_id",
        "observation_hash",
        "decision",
        "reason_code",
        "actor_id",
        "sequence",
        "previous_feedback_hash",
        "append_only",
    }
)

_HASH_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_REASON_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z0-9_:-]{1,128}$")
_SECRET_KEY_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key|client[_-]?secret|private[_-]?key|cvv|pin)",
    re.IGNORECASE,
)


def _strict_mapping(
    value: Any, allowed: frozenset[str], context: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{context} must be an object")
    keys = set(value)
    if any(not isinstance(key, str) for key in keys):
        raise ValueError(f"{context} keys must be strings")
    unknown = keys - allowed
    if unknown:
        raise ValueError(
            f"{context} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    return value


def _required(value: Mapping[str, Any], name: str, context: str) -> Any:
    if name not in value:
        raise ValueError(f"{context}.{name} is required")
    return value[name]


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _hash(value: Any, name: str) -> str:
    parsed = _identifier(value, name)
    if not _HASH_RE.fullmatch(parsed):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return parsed


def _reason(value: Any, name: str = "reason_code") -> str:
    parsed = _identifier(value, name).upper()
    if not _REASON_RE.fullmatch(parsed):
        raise ValueError(f"{name} must match {_REASON_RE.pattern}")
    return parsed


def _decimal_string(value: Any, name: str, *, positive: bool = False) -> str:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"{name} must be finite")
    if positive and parsed <= 0:
        raise ValueError(f"{name} must be positive")
    if parsed == 0:
        return "0"
    return format(parsed.normalize(), "f")


def _timestamp(value: Any, name: str) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return _identifier(value, name)


def _confidence(value: Any) -> float:
    if isinstance(value, bool):
        raise TypeError("confidence must be a finite number between 0 and 1")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence must be a finite number between 0 and 1") from exc
    if not math.isfinite(parsed) or not 0 <= parsed <= 1:
        raise ValueError("confidence must be a finite number between 0 and 1")
    return parsed


def _unresolved(value: Any) -> bool:
    return value is None or (
        isinstance(value, str)
        and value.strip().upper() in {"", "UNKNOWN", "UNRESOLVED", "MISSING"}
    )


def _freeze(value: Any, context: str = "value") -> Any:
    """Convert JSON-like values to recursively immutable values."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{context} must not contain NaN or infinity")
        return value
    if isinstance(value, Decimal):
        return _decimal_string(value, context)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{context} object keys must be strings")
            if _SECRET_KEY_RE.search(key):
                raise ValueError(
                    f"{context} contains forbidden secret-like field {key!r}"
                )
            frozen[key] = _freeze(item, f"{context}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, f"{context}[]") for item in value)
    if isinstance(value, (set, frozenset)):
        items = tuple(_freeze(item, f"{context}[]") for item in value)
        return tuple(sorted(items, key=lambda item: canonical_json(item)))
    raise TypeError(f"{context} is not JSON-safe: {type(value).__name__}")


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_value(value[key]) for key in sorted(value)}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def _payload(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    return value


def canonical_json(value: Any) -> str:
    """Return compact, sorted, JSON-safe serialization for a contract value."""
    frozen = _freeze(_payload(value))
    return json.dumps(
        _json_value(frozen),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def sha256(value: Any) -> str:
    """Hash canonical JSON with UTF-8 SHA-256."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256(value)


def _refs(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise TypeError(f"{name} must be an array of strings")
    result = tuple(dict.fromkeys(_identifier(item, name) for item in value))
    return result


def _fields(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise TypeError(f"{name} must be an array of strings")
    result = tuple(dict.fromkeys(_identifier(item, name) for item in value))
    return result


def _reject_protected_fields(field_names: Any, context: str) -> None:
    protected = set(field_names) & set(PROTECTED_FIELDS)
    if protected:
        raise ValueError(
            f"{context} cannot alter protected fields: {', '.join(sorted(protected))}"
        )


def _reject_protected_payload(value: Any, context: str) -> None:
    if not isinstance(value, Mapping):
        return
    protected = set(value) & set(PROTECTED_FIELDS)
    if protected:
        raise ValueError(
            f"{context} contains protected fields: {', '.join(sorted(protected))}"
        )
    for key, item in value.items():
        if isinstance(item, Mapping):
            _reject_protected_payload(item, f"{context}.{key}")


@dataclass(frozen=True, slots=True)
class EffectPolicy:
    """One closed Policy A target permission record."""

    field: str
    effect_kind: str
    writable: bool
    suggestion_only: bool
    unresolved_only: bool
    direct_commit_allowed: bool
    validator_required: bool
    protection_class: str
    forbidden_capabilities: tuple[str, ...] = FORBIDDEN_SIDE_EFFECT_CAPABILITIES

    def __post_init__(self) -> None:
        _identifier(self.field, "field")
        if self.effect_kind not in {
            "DETERMINISTIC_WRITE",
            "SUGGESTION_ONLY",
            "FORBIDDEN",
        }:
            raise ValueError("illegal effect_kind")
        if self.protection_class not in {
            "NONE",
            "IDENTITY",
            "ECONOMIC",
            "ACTUAL_STRUCTURAL",
            "PROPOSAL",
        }:
            raise ValueError("illegal protection_class")
        if self.direct_commit_allowed or set(self.forbidden_capabilities) - set(
            FORBIDDEN_SIDE_EFFECT_CAPABILITIES
        ):
            raise ValueError(
                "Policy A effect capabilities are closed and proposal-only"
            )
        object.__setattr__(
            self, "forbidden_capabilities", tuple(self.forbidden_capabilities)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "effect_kind": self.effect_kind,
            "writable": self.writable,
            "suggestion_only": self.suggestion_only,
            "unresolved_only": self.unresolved_only,
            "direct_commit_allowed": self.direct_commit_allowed,
            "validator_required": self.validator_required,
            "protection_class": self.protection_class,
            "forbidden_capabilities": list(self.forbidden_capabilities),
        }


def _build_effect_matrix() -> Mapping[str, EffectPolicy]:
    records: dict[str, EffectPolicy] = {}
    for field in DETERMINISTIC_WRITABLE_FIELDS:
        records[field] = EffectPolicy(
            field=field,
            effect_kind="DETERMINISTIC_WRITE",
            writable=True,
            suggestion_only=False,
            unresolved_only=False,
            direct_commit_allowed=False,
            validator_required=True,
            protection_class="NONE",
        )
    for field in SUGGESTION_ONLY_FIELDS:
        records[field] = EffectPolicy(
            field=field,
            effect_kind="SUGGESTION_ONLY",
            writable=False,
            suggestion_only=True,
            unresolved_only=True,
            direct_commit_allowed=False,
            validator_required=True,
            protection_class="PROPOSAL",
        )
    for field in PROTECTED_IDENTITY_FIELDS:
        records[field] = EffectPolicy(
            field, "FORBIDDEN", False, False, False, False, True, "IDENTITY"
        )
    for field in PROTECTED_ECONOMIC_FIELDS:
        records[field] = EffectPolicy(
            field, "FORBIDDEN", False, False, False, False, True, "ECONOMIC"
        )
    for field in PROTECTED_ACTUAL_STRUCTURAL_FIELDS:
        records[field] = EffectPolicy(
            field, "FORBIDDEN", False, False, False, False, True, "ACTUAL_STRUCTURAL"
        )
    return MappingProxyType(records)


POLICY_A_EFFECT_MATRIX: Final[Mapping[str, EffectPolicy]] = _build_effect_matrix()


@dataclass(frozen=True, slots=True)
class LedgerObservation:
    """Immutable, redacted source plus derived observation for analysis."""

    observation_id: str
    transaction_id: str
    transaction_at: str
    card: str
    merchant_raw: str
    amount_aed: str | Decimal | int | float
    currency: str
    observation_hash: str | None = None
    schema_version: int = SCHEMA_VERSION
    observed_at: str | None = None
    account: str | None = None
    institution: str | None = None
    account_last4: str | None = None
    amount_original: str | Decimal | int | float | None = None
    source_direction: str | None = None
    source_type: str = "unknown"
    source_message_id: str | None = None
    transaction_type: str = "PURCHASE"
    is_refund: bool = False
    reconciliation_status: str = "UNMATCHED"
    vendor: str | None = None
    category: str | None = None
    subcategory: str | None = None
    owner: str | None = None
    property_code: str | None = None
    rental_unit: str | None = None
    channel: str = "UNKNOWN"
    reward_bucket: str | None = None
    tags: tuple[str, ...] = ()
    evidence_policy: str | None = None
    evidence_status: str = "NOT_REQUESTED"
    review_required: bool = False
    is_subscription: bool = False
    manual_locked_fields: tuple[str, ...] = ()
    existing_link: str | None = None
    evidence_refs: tuple[str, ...] = ()
    redacted_metadata: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported LedgerObservation schema_version")
        for name in (
            "observation_id",
            "transaction_id",
            "card",
            "merchant_raw",
            "source_type",
        ):
            _identifier(getattr(self, name), name)
        object.__setattr__(
            self, "transaction_at", _timestamp(self.transaction_at, "transaction_at")
        )
        if self.observed_at is not None:
            object.__setattr__(
                self, "observed_at", _timestamp(self.observed_at, "observed_at")
            )
        object.__setattr__(self, "card", self.card.strip().upper())
        object.__setattr__(
            self,
            "amount_aed",
            _decimal_string(self.amount_aed, "amount_aed", positive=True),
        )
        if self.amount_original is not None:
            object.__setattr__(
                self,
                "amount_original",
                _decimal_string(self.amount_original, "amount_original", positive=True),
            )
        currency = _identifier(self.currency, "currency").upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("currency must be a three-letter code")
        object.__setattr__(self, "currency", currency)
        if self.source_direction is not None:
            direction = _identifier(self.source_direction, "source_direction").upper()
            if direction not in {"CREDIT", "DEBIT"}:
                raise ValueError("source_direction must be CREDIT or DEBIT")
            object.__setattr__(self, "source_direction", direction)
        object.__setattr__(
            self,
            "transaction_type",
            _identifier(self.transaction_type, "transaction_type").upper(),
        )
        object.__setattr__(
            self,
            "reconciliation_status",
            _identifier(self.reconciliation_status, "reconciliation_status").upper(),
        )
        object.__setattr__(
            self, "channel", _identifier(self.channel, "channel").upper()
        )
        if self.reward_bucket is not None:
            object.__setattr__(
                self, "reward_bucket", _identifier(self.reward_bucket, "reward_bucket")
            )
        for name in (
            "account",
            "institution",
            "account_last4",
            "source_message_id",
            "vendor",
            "category",
            "subcategory",
            "owner",
            "property_code",
            "rental_unit",
            "evidence_policy",
            "evidence_status",
            "existing_link",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _identifier(value, name))
        tags = _fields(self.tags, "tags")
        object.__setattr__(self, "tags", tuple(sorted(tags)))
        locks = _fields(self.manual_locked_fields, "manual_locked_fields")
        unknown_locks = set(locks) - (
            _OBSERVATION_FIELDS
            | set(DETERMINISTIC_WRITABLE_FIELDS)
            | set(SUGGESTION_ONLY_FIELDS)
        )
        if unknown_locks:
            raise ValueError(
                f"manual_locked_fields contains unknown fields: {', '.join(sorted(unknown_locks))}"
            )
        object.__setattr__(self, "manual_locked_fields", tuple(sorted(locks)))
        object.__setattr__(
            self, "evidence_refs", _refs(self.evidence_refs, "evidence_refs")
        )
        metadata = _freeze(self.redacted_metadata, "redacted_metadata")
        if not isinstance(metadata, Mapping):
            raise TypeError("redacted_metadata must be an object")
        object.__setattr__(self, "redacted_metadata", metadata)
        expected = sha256(self._without_hash())
        if (
            self.observation_hash is not None
            and _hash(self.observation_hash, "observation_hash") != expected
        ):
            raise ValueError(
                "observation_hash does not match immutable observation facts"
            )
        object.__setattr__(self, "observation_hash", expected)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> LedgerObservation:
        data = _strict_mapping(value, _OBSERVATION_FIELDS, "LedgerObservation")
        required = (
            "observation_id",
            "transaction_id",
            "transaction_at",
            "card",
            "merchant_raw",
            "amount_aed",
            "currency",
        )
        for name in required:
            _required(data, name, "LedgerObservation")
        return cls(**dict(data))

    def _without_hash(self) -> dict[str, Any]:
        data = self.to_dict()
        data.pop("observation_hash", None)
        return data

    def to_dict(self) -> dict[str, Any]:
        return _json_value(
            {
                "schema_version": self.schema_version,
                "observation_id": self.observation_id,
                "observation_hash": self.observation_hash,
                "transaction_id": self.transaction_id,
                "transaction_at": self.transaction_at,
                "observed_at": self.observed_at,
                "card": self.card,
                "account": self.account,
                "institution": self.institution,
                "account_last4": self.account_last4,
                "merchant_raw": self.merchant_raw,
                "amount_aed": self.amount_aed,
                "amount_original": self.amount_original,
                "currency": self.currency,
                "source_direction": self.source_direction,
                "source_type": self.source_type,
                "source_message_id": self.source_message_id,
                "transaction_type": self.transaction_type,
                "is_refund": self.is_refund,
                "reconciliation_status": self.reconciliation_status,
                "vendor": self.vendor,
                "category": self.category,
                "subcategory": self.subcategory,
                "owner": self.owner,
                "property_code": self.property_code,
                "rental_unit": self.rental_unit,
                "channel": self.channel,
                "reward_bucket": self.reward_bucket,
                "tags": self.tags,
                "evidence_policy": self.evidence_policy,
                "evidence_status": self.evidence_status,
                "review_required": self.review_required,
                "is_subscription": self.is_subscription,
                "manual_locked_fields": self.manual_locked_fields,
                "existing_link": self.existing_link,
                "evidence_refs": self.evidence_refs,
                "redacted_metadata": self.redacted_metadata,
            }
        )


@dataclass(frozen=True, slots=True)
class CandidateEnvelope:
    """Immutable candidate generated by a deterministic rule or agent policy."""

    candidate_id: str
    candidate_kind: str
    observation_id: str
    observation_hash: str
    scope_hash: str
    input_hash: str
    target_fields: tuple[str, ...]
    reason_code: str
    confidence: float
    payload: Mapping[str, Any]
    candidate_hash: str | None = None
    evidence_refs: tuple[str, ...] = ()
    proposal_only: bool = True
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported CandidateEnvelope schema_version")
        _identifier(self.candidate_id, "candidate_id")
        if self.candidate_kind not in CANDIDATE_KINDS:
            raise ValueError("illegal candidate_kind")
        _identifier(self.observation_id, "observation_id")
        _hash(self.observation_hash, "observation_hash")
        _hash(self.scope_hash, "scope_hash")
        _hash(self.input_hash, "input_hash")
        fields = _fields(self.target_fields, "target_fields")
        if not fields:
            raise ValueError("target_fields must not be empty")
        unknown = set(fields) - set(POLICY_A_EFFECT_MATRIX)
        if unknown:
            raise ValueError(f"illegal target fields: {', '.join(sorted(unknown))}")
        _reject_protected_fields(fields, "CandidateEnvelope")
        if self.candidate_kind == "AGENT_POLICY":
            deterministic_targets = set(fields) - set(SUGGESTION_ONLY_FIELDS)
            if deterministic_targets:
                raise ValueError(
                    "AGENT_POLICY candidates may target only suggestion-only fields: "
                    + ", ".join(sorted(deterministic_targets))
                )
        object.__setattr__(self, "target_fields", tuple(sorted(fields)))
        object.__setattr__(self, "reason_code", _reason(self.reason_code))
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        payload = _freeze(self.payload, "payload")
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be an object")
        _reject_protected_payload(payload, "CandidateEnvelope.payload")
        unknown_payload = set(payload) - set(fields)
        if unknown_payload:
            raise ValueError(
                "payload contains fields not listed in target_fields: "
                + ", ".join(sorted(unknown_payload))
            )
        missing_payload = set(fields) - set(payload)
        if missing_payload:
            raise ValueError(
                "payload is missing target fields: "
                + ", ".join(sorted(missing_payload))
            )
        object.__setattr__(self, "payload", payload)
        object.__setattr__(
            self, "evidence_refs", _refs(self.evidence_refs, "evidence_refs")
        )
        if self.proposal_only is not True:
            raise ValueError("CandidateEnvelope is proposal-only")
        expected = sha256(self._without_hash())
        if (
            self.candidate_hash is not None
            and _hash(self.candidate_hash, "candidate_hash") != expected
        ):
            raise ValueError("candidate_hash does not match immutable candidate facts")
        object.__setattr__(self, "candidate_hash", expected)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> CandidateEnvelope:
        data = _strict_mapping(value, _CANDIDATE_FIELDS, "CandidateEnvelope")
        required = (
            "candidate_id",
            "candidate_kind",
            "observation_id",
            "observation_hash",
            "scope_hash",
            "input_hash",
            "target_fields",
            "reason_code",
            "confidence",
            "payload",
        )
        for name in required:
            _required(data, name, "CandidateEnvelope")
        return cls(**dict(data))

    def _without_hash(self) -> dict[str, Any]:
        data = self.to_dict()
        data.pop("candidate_hash", None)
        return data

    def to_dict(self) -> dict[str, Any]:
        return _json_value(
            {
                "schema_version": self.schema_version,
                "candidate_id": self.candidate_id,
                "candidate_hash": self.candidate_hash,
                "candidate_kind": self.candidate_kind,
                "observation_id": self.observation_id,
                "observation_hash": self.observation_hash,
                "scope_hash": self.scope_hash,
                "input_hash": self.input_hash,
                "target_fields": self.target_fields,
                "reason_code": self.reason_code,
                "confidence": self.confidence,
                "evidence_refs": self.evidence_refs,
                "payload": self.payload,
                "proposal_only": self.proposal_only,
            }
        )


@dataclass(frozen=True, slots=True)
class ProposalEnvelope:
    """One validated proposal; it can never represent a direct write."""

    proposal_id: str
    candidate_id: str
    candidate_hash: str
    candidate_kind: str
    observation_id: str
    observation_hash: str
    scope_hash: str
    input_hash: str
    field: str
    target_kind: str
    value: Any
    reason_code: str
    confidence: float
    existing_value: Any = None
    evidence_refs: tuple[str, ...] = ()
    proposal_hash: str | None = None
    proposal_only: bool = True
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported ProposalEnvelope schema_version")
        for name in ("proposal_id", "candidate_id", "observation_id"):
            _identifier(getattr(self, name), name)
        for name in ("candidate_hash", "observation_hash", "scope_hash", "input_hash"):
            _hash(getattr(self, name), name)
        if self.candidate_kind not in CANDIDATE_KINDS:
            raise ValueError("illegal candidate_kind")
        field = _identifier(self.field, "field")
        policy = POLICY_A_EFFECT_MATRIX.get(field)
        if policy is None:
            raise ValueError(f"illegal proposal field: {field}")
        if policy.effect_kind == "FORBIDDEN":
            raise ValueError(f"proposal cannot alter protected field {field}")
        if self.target_kind not in TARGET_KINDS:
            raise ValueError("illegal target_kind")
        expected_kind = (
            "SUGGESTION_ONLY" if policy.suggestion_only else "DETERMINISTIC_WRITABLE"
        )
        if self.target_kind != expected_kind:
            raise ValueError(f"target_kind does not match Policy A for {field}")
        if policy.unresolved_only and not _unresolved(self.existing_value):
            raise ValueError(
                f"{field} proposals are allowed only while the current value is unresolved"
            )
        object.__setattr__(self, "field", field)
        object.__setattr__(self, "reason_code", _reason(self.reason_code))
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        value = _freeze(self.value, "value")
        object.__setattr__(self, "value", value)
        object.__setattr__(
            self, "existing_value", _freeze(self.existing_value, "existing_value")
        )
        _reject_protected_payload(value, "ProposalEnvelope.value")
        object.__setattr__(
            self, "evidence_refs", _refs(self.evidence_refs, "evidence_refs")
        )
        if self.proposal_only is not True:
            raise ValueError("ProposalEnvelope is proposal-only")
        expected = sha256(self._without_hash())
        if (
            self.proposal_hash is not None
            and _hash(self.proposal_hash, "proposal_hash") != expected
        ):
            raise ValueError("proposal_hash does not match immutable proposal facts")
        object.__setattr__(self, "proposal_hash", expected)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ProposalEnvelope:
        data = _strict_mapping(value, _PROPOSAL_FIELDS, "ProposalEnvelope")
        required = (
            "proposal_id",
            "candidate_id",
            "candidate_hash",
            "candidate_kind",
            "observation_id",
            "observation_hash",
            "scope_hash",
            "input_hash",
            "field",
            "target_kind",
            "value",
            "reason_code",
            "confidence",
        )
        for name in required:
            _required(data, name, "ProposalEnvelope")
        return cls(**dict(data))

    def _without_hash(self) -> dict[str, Any]:
        data = self.to_dict()
        data.pop("proposal_hash", None)
        return data

    def to_dict(self) -> dict[str, Any]:
        return _json_value(
            {
                "schema_version": self.schema_version,
                "proposal_id": self.proposal_id,
                "proposal_hash": self.proposal_hash,
                "candidate_id": self.candidate_id,
                "candidate_hash": self.candidate_hash,
                "candidate_kind": self.candidate_kind,
                "observation_id": self.observation_id,
                "observation_hash": self.observation_hash,
                "scope_hash": self.scope_hash,
                "input_hash": self.input_hash,
                "field": self.field,
                "target_kind": self.target_kind,
                "value": self.value,
                "existing_value": self.existing_value,
                "reason_code": self.reason_code,
                "confidence": self.confidence,
                "evidence_refs": self.evidence_refs,
                "proposal_only": self.proposal_only,
            }
        )


@dataclass(frozen=True, slots=True)
class EffectEnvelope:
    """Validated proposal effect descriptor with no executable capability."""

    effect_id: str
    proposal_id: str
    proposal_hash: str
    observation_id: str
    observation_hash: str
    field: str
    target_kind: str
    value: Any
    effect_hash: str | None = None
    proposal_only: bool = True
    direct_commit_allowed: bool = False
    capabilities: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported EffectEnvelope schema_version")
        for name in ("effect_id", "proposal_id", "observation_id"):
            _identifier(getattr(self, name), name)
        _hash(self.proposal_hash, "proposal_hash")
        _hash(self.observation_hash, "observation_hash")
        policy = POLICY_A_EFFECT_MATRIX.get(_identifier(self.field, "field"))
        if policy is None or policy.effect_kind == "FORBIDDEN":
            raise ValueError("effect field is not permitted by Policy A")
        if self.target_kind != (
            "SUGGESTION_ONLY" if policy.suggestion_only else "DETERMINISTIC_WRITABLE"
        ):
            raise ValueError("illegal target_kind for effect field")
        if self.proposal_only is not True or self.direct_commit_allowed is not False:
            raise ValueError("effects are proposal-only and cannot commit directly")
        capabilities = _refs(self.capabilities, "capabilities")
        if capabilities:
            raise ValueError("effects cannot carry executable capabilities")
        object.__setattr__(self, "field", policy.field)
        object.__setattr__(self, "value", _freeze(self.value, "value"))
        object.__setattr__(self, "capabilities", capabilities)
        _reject_protected_payload(self.value, "EffectEnvelope.value")
        expected = sha256(self._without_hash())
        if (
            self.effect_hash is not None
            and _hash(self.effect_hash, "effect_hash") != expected
        ):
            raise ValueError("effect_hash does not match immutable effect facts")
        object.__setattr__(self, "effect_hash", expected)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> EffectEnvelope:
        data = _strict_mapping(value, _EFFECT_FIELDS, "EffectEnvelope")
        required = (
            "effect_id",
            "proposal_id",
            "proposal_hash",
            "observation_id",
            "observation_hash",
            "field",
            "target_kind",
            "value",
        )
        for name in required:
            _required(data, name, "EffectEnvelope")
        return cls(**dict(data))

    def _without_hash(self) -> dict[str, Any]:
        data = self.to_dict()
        data.pop("effect_hash", None)
        return data

    def to_dict(self) -> dict[str, Any]:
        return _json_value(
            {
                "schema_version": self.schema_version,
                "effect_id": self.effect_id,
                "effect_hash": self.effect_hash,
                "proposal_id": self.proposal_id,
                "proposal_hash": self.proposal_hash,
                "observation_id": self.observation_id,
                "observation_hash": self.observation_hash,
                "field": self.field,
                "target_kind": self.target_kind,
                "value": self.value,
                "proposal_only": self.proposal_only,
                "direct_commit_allowed": self.direct_commit_allowed,
                "capabilities": self.capabilities,
            }
        )


@dataclass(frozen=True, slots=True)
class FeedbackEnvelope:
    """One append-only review input; it cannot mutate prior feedback."""

    feedback_id: str
    proposal_id: str
    proposal_hash: str
    observation_id: str
    observation_hash: str
    decision: str
    reason_code: str
    actor_id: str
    sequence: int
    previous_feedback_hash: str | None = None
    feedback_hash: str | None = None
    append_only: bool = True
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported FeedbackEnvelope schema_version")
        for name in ("feedback_id", "proposal_id", "observation_id", "actor_id"):
            _identifier(getattr(self, name), name)
        for name in ("proposal_hash", "observation_hash"):
            _hash(getattr(self, name), name)
        if self.previous_feedback_hash is not None:
            _hash(self.previous_feedback_hash, "previous_feedback_hash")
        if self.decision not in DECISIONS:
            raise ValueError("illegal feedback decision")
        object.__setattr__(self, "reason_code", _reason(self.reason_code))
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
        ):
            raise ValueError("sequence must be a positive integer")
        if self.append_only is not True:
            raise ValueError("feedback is append-only")
        expected = sha256(self._without_hash())
        if (
            self.feedback_hash is not None
            and _hash(self.feedback_hash, "feedback_hash") != expected
        ):
            raise ValueError("feedback_hash does not match immutable feedback facts")
        object.__setattr__(self, "feedback_hash", expected)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> FeedbackEnvelope:
        data = _strict_mapping(value, _FEEDBACK_FIELDS, "FeedbackEnvelope")
        required = (
            "feedback_id",
            "proposal_id",
            "proposal_hash",
            "observation_id",
            "observation_hash",
            "decision",
            "reason_code",
            "actor_id",
            "sequence",
        )
        for name in required:
            _required(data, name, "FeedbackEnvelope")
        return cls(**dict(data))

    def _without_hash(self) -> dict[str, Any]:
        data = self.to_dict()
        data.pop("feedback_hash", None)
        return data

    def to_dict(self) -> dict[str, Any]:
        return _json_value(
            {
                "schema_version": self.schema_version,
                "feedback_id": self.feedback_id,
                "feedback_hash": self.feedback_hash,
                "proposal_id": self.proposal_id,
                "proposal_hash": self.proposal_hash,
                "observation_id": self.observation_id,
                "observation_hash": self.observation_hash,
                "decision": self.decision,
                "reason_code": self.reason_code,
                "actor_id": self.actor_id,
                "sequence": self.sequence,
                "previous_feedback_hash": self.previous_feedback_hash,
                "append_only": self.append_only,
            }
        )


def validate_policy_a_target(
    field: str, existing_value: Any = None, target_kind: str | None = None
) -> EffectPolicy:
    """Validate one target against Policy A and return its immutable policy record."""
    policy = POLICY_A_EFFECT_MATRIX.get(field)
    if policy is None or policy.effect_kind == "FORBIDDEN":
        raise ValueError(f"illegal or protected Policy A target: {field}")
    if target_kind is not None and target_kind != (
        "SUGGESTION_ONLY" if policy.suggestion_only else "DETERMINISTIC_WRITABLE"
    ):
        raise ValueError(f"target_kind does not match Policy A for {field}")
    if policy.unresolved_only and not _unresolved(existing_value):
        raise ValueError(f"{field} proposals require an unresolved current value")
    return policy


def validate_protected_changes(changes: Mapping[str, Any]) -> None:
    """Reject any attempted change to identity, economics, or Actual structure."""
    if not isinstance(changes, Mapping):
        raise TypeError("changes must be an object")
    _reject_protected_payload(changes, "changes")
    _reject_protected_fields(changes, "changes")


__all__ = [
    "CANDIDATE_KINDS",
    "DECISIONS",
    "DETERMINISTIC_WRITABLE_FIELDS",
    "FORBIDDEN_SIDE_EFFECT_CAPABILITIES",
    "POLICY_A_EFFECT_MATRIX",
    "PROTECTED_ACTUAL_STRUCTURAL_FIELDS",
    "PROTECTED_ECONOMIC_FIELDS",
    "PROTECTED_FIELDS",
    "PROTECTED_IDENTITY_FIELDS",
    "SCHEMA_VERSION",
    "SUGGESTION_ONLY_FIELDS",
    "TARGET_KINDS",
    "CandidateEnvelope",
    "EffectEnvelope",
    "EffectPolicy",
    "FeedbackEnvelope",
    "LedgerObservation",
    "ProposalEnvelope",
    "canonical_json",
    "canonical_sha256",
    "sha256",
    "validate_policy_a_target",
    "validate_protected_changes",
]
