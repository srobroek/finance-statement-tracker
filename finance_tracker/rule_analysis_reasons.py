"""Closed, deterministic reason codes for proposal-only rule analysis.

This module is deliberately data-only: it has no provider, ledger, database,
network, apply, or promote integration.  Callers must treat a reason as an
explanation, never as authorization to perform an effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from types import MappingProxyType
from typing import Final, Mapping


class ReasonCode(str, Enum):
    """The complete set of stable rule-analysis outcomes."""

    MATCH_UNIQUE = "MATCH_UNIQUE"
    MATCH_AMBIGUOUS = "MATCH_AMBIGUOUS"
    MATCH_MISSING = "MATCH_MISSING"
    MATCH_CONFLICTING = "MATCH_CONFLICTING"
    UNRESOLVED = "UNRESOLVED"
    DOMAIN_VALIDATION_FAILED = "DOMAIN_VALIDATION_FAILED"
    EVIDENCE_VALIDATION_FAILED = "EVIDENCE_VALIDATION_FAILED"
    MANUAL_LOCKED = "MANUAL_LOCKED"
    PROTECTED_IDENTITY_FIELD = "PROTECTED_IDENTITY_FIELD"
    PROTECTED_ECONOMIC_FIELD = "PROTECTED_ECONOMIC_FIELD"
    PROTECTED_ACTUAL_FIELD = "PROTECTED_ACTUAL_FIELD"
    DUPLICATE_PROPOSAL = "DUPLICATE_PROPOSAL"
    STALE_PROPOSAL = "STALE_PROPOSAL"
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    EFFECT_DRIFT = "EFFECT_DRIFT"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    HASH_MISMATCH = "HASH_MISMATCH"
    REPLAY_DRIFT = "REPLAY_DRIFT"
    AGENT_REFUSAL = "AGENT_REFUSAL"
    REVIEW_ONLY = "REVIEW_ONLY"
    TRANSFER_MATCH_UNIQUE = "TRANSFER_MATCH_UNIQUE"
    TRANSFER_MATCH_AMBIGUOUS = "TRANSFER_MATCH_AMBIGUOUS"
    TRANSFER_COUNTERPART_NOT_FOUND = "TRANSFER_COUNTERPART_NOT_FOUND"
    TRANSFER_AMOUNT_MISMATCH = "TRANSFER_AMOUNT_MISMATCH"
    TRANSFER_CURRENCY_MISMATCH = "TRANSFER_CURRENCY_MISMATCH"
    TRANSFER_DIRECTION_MISMATCH = "TRANSFER_DIRECTION_MISMATCH"
    TRANSFER_DATE_MISMATCH = "TRANSFER_DATE_MISMATCH"
    MANUAL_LOCK_CONFLICT = "MANUAL_LOCK_CONFLICT"
    DETERMINISTIC_PRECEDENCE = "DETERMINISTIC_PRECEDENCE"
    PROTECTED_FIELD_PROPOSAL = "PROTECTED_FIELD_PROPOSAL"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    STALE_BASE = "STALE_BASE"
    REPLAY_NONDETERMINISTIC = "REPLAY_NONDETERMINISTIC"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    HASH_INVALID = "HASH_INVALID"
    GROUP_TRACE_MISSING = "GROUP_TRACE_MISSING"
    ACTUAL_ECONOMIC_INVARIANT = "ACTUAL_ECONOMIC_INVARIANT"
    ACTUAL_ID_INVARIANT = "ACTUAL_ID_INVARIANT"
    ACTUAL_LINK_INVARIANT = "ACTUAL_LINK_INVARIANT"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    EXACT_TARGET_CONFIRMATION_REQUIRED = "EXACT_TARGET_CONFIRMATION_REQUIRED"
    EFFECT_POLICY_DRIFT = "EFFECT_POLICY_DRIFT"
    UNKNOWN_REASON = "UNKNOWN_REASON"
    LEGACY_ACCEPTED = "accepted"
    LEGACY_PROPOSAL_NOT_OBJECT = "proposal_not_object"
    LEGACY_FIELD_NOT_REQUESTED_OR_ALREADY_RESOLVED = (
        "field_not_requested_or_already_resolved"
    )
    LEGACY_PROTECTED_OR_UNSUPPORTED_FIELD = "protected_or_unsupported_field"
    LEGACY_BELOW_CONFIDENCE_THRESHOLD = "below_confidence_threshold"
    LEGACY_VALUE_NOT_ALLOWED = "value_not_allowed"
    LEGACY_BOOLEAN_VALUE_REQUIRED = "boolean_value_required"
    LEGACY_EMPTY_VALUE = "empty_value"
    LEGACY_TAG_NOT_ALLOWED = "tag_not_allowed"
    LEGACY_RULE_RECOMMENDATION_MUST_BE_OBJECT = "rule_recommendation_must_be_object"
    LEGACY_CATEGORY_RECOMMENDATION_MUST_NAME_CATEGORY = (
        "category_recommendation_must_name_category"
    )


@dataclass(frozen=True, slots=True)
class ReasonDefinition:
    """Immutable metadata for one registered code."""

    code: ReasonCode
    outcome: str
    description: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code.value,
            "outcome": self.outcome,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class ReasonRecord:
    """An immutable occurrence of a registered reason."""

    code: ReasonCode
    detail: str = ""
    field: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, ReasonCode):
            raise UnknownReasonCode(self.code)
        if not isinstance(self.detail, str) or not isinstance(
            self.field, (str, type(None))
        ):
            raise TypeError("detail and field must be strings or None")
        if not isinstance(self.evidence_refs, tuple) or any(
            not isinstance(ref, str) for ref in self.evidence_refs
        ):
            raise TypeError("evidence_refs must be a tuple of strings")

    def as_dict(self) -> dict[str, object]:
        """Return the closed, JSON-shaped representation."""
        return {
            "code": self.code.value,
            "detail": self.detail,
            "field": self.field,
            "evidence_refs": self.evidence_refs,
        }


class UnknownReasonCode(ValueError):
    """Raised when an unregistered code is supplied (fail closed)."""


_DEFINITIONS: tuple[ReasonDefinition, ...] = (
    ReasonDefinition(
        ReasonCode.MATCH_UNIQUE,
        "accepted",
        "Exactly one deterministic match was found.",
    ),
    ReasonDefinition(
        ReasonCode.MATCH_AMBIGUOUS, "review_only", "More than one candidate matched."
    ),
    ReasonDefinition(ReasonCode.MATCH_MISSING, "review_only", "No candidate matched."),
    ReasonDefinition(
        ReasonCode.MATCH_CONFLICTING, "review_only", "Evidence or candidates conflict."
    ),
    ReasonDefinition(
        ReasonCode.UNRESOLVED, "review_only", "A requested field remains unresolved."
    ),
    ReasonDefinition(
        ReasonCode.DOMAIN_VALIDATION_FAILED,
        "rejected",
        "The proposal failed domain validation.",
    ),
    ReasonDefinition(
        ReasonCode.EVIDENCE_VALIDATION_FAILED,
        "rejected",
        "Evidence was missing or invalid.",
    ),
    ReasonDefinition(
        ReasonCode.MANUAL_LOCKED,
        "rejected",
        "A manual lock prevents changing the field.",
    ),
    ReasonDefinition(
        ReasonCode.PROTECTED_IDENTITY_FIELD,
        "rejected",
        "An identity field is protected.",
    ),
    ReasonDefinition(
        ReasonCode.PROTECTED_ECONOMIC_FIELD,
        "rejected",
        "An economic field is protected.",
    ),
    ReasonDefinition(
        ReasonCode.PROTECTED_ACTUAL_FIELD,
        "rejected",
        "An Actual-owned field is protected.",
    ),
    ReasonDefinition(
        ReasonCode.DUPLICATE_PROPOSAL,
        "rejected",
        "The proposal duplicates an existing proposal.",
    ),
    ReasonDefinition(
        ReasonCode.STALE_PROPOSAL,
        "rejected",
        "The proposal was generated from stale state.",
    ),
    ReasonDefinition(
        ReasonCode.SCHEMA_DRIFT,
        "rejected",
        "The candidate schema differs from the contract.",
    ),
    ReasonDefinition(
        ReasonCode.EFFECT_DRIFT,
        "rejected",
        "The candidate effect differs from the allowed policy.",
    ),
    ReasonDefinition(
        ReasonCode.SCOPE_MISMATCH,
        "rejected",
        "The candidate scope differs from the query scope.",
    ),
    ReasonDefinition(
        ReasonCode.HASH_MISMATCH, "rejected", "A bound input or candidate hash differs."
    ),
    ReasonDefinition(
        ReasonCode.REPLAY_DRIFT,
        "rejected",
        "Deterministic replay produced different output.",
    ),
    ReasonDefinition(
        ReasonCode.AGENT_REFUSAL,
        "review_only",
        "The agent refused or could not provide a suggestion.",
    ),
    ReasonDefinition(
        ReasonCode.REVIEW_ONLY,
        "review_only",
        "The outcome requires human review and cannot be applied.",
    ),
    ReasonDefinition(
        ReasonCode.TRANSFER_MATCH_UNIQUE,
        "accepted",
        "Exactly one transfer counterpart matched.",
    ),
    ReasonDefinition(
        ReasonCode.TRANSFER_MATCH_AMBIGUOUS,
        "review_only",
        "Transfer counterpart matching is ambiguous.",
    ),
    ReasonDefinition(
        ReasonCode.TRANSFER_COUNTERPART_NOT_FOUND,
        "review_only",
        "No transfer counterpart was found.",
    ),
    ReasonDefinition(
        ReasonCode.TRANSFER_AMOUNT_MISMATCH,
        "rejected",
        "Transfer amounts do not match.",
    ),
    ReasonDefinition(
        ReasonCode.TRANSFER_CURRENCY_MISMATCH,
        "rejected",
        "Transfer currencies do not match.",
    ),
    ReasonDefinition(
        ReasonCode.TRANSFER_DIRECTION_MISMATCH,
        "rejected",
        "Transfer directions do not oppose as required.",
    ),
    ReasonDefinition(
        ReasonCode.TRANSFER_DATE_MISMATCH,
        "rejected",
        "Transfer dates fall outside the allowed window.",
    ),
    ReasonDefinition(
        ReasonCode.MANUAL_LOCK_CONFLICT,
        "rejected",
        "A manual lock conflicts with the proposal.",
    ),
    ReasonDefinition(
        ReasonCode.DETERMINISTIC_PRECEDENCE,
        "accepted",
        "A deterministic precedence rule selected the outcome.",
    ),
    ReasonDefinition(
        ReasonCode.PROTECTED_FIELD_PROPOSAL,
        "rejected",
        "The proposal targets a protected field.",
    ),
    ReasonDefinition(
        ReasonCode.LOW_CONFIDENCE,
        "review_only",
        "The match confidence is below the acceptance threshold.",
    ),
    ReasonDefinition(
        ReasonCode.MISSING_EVIDENCE, "rejected", "Required evidence is missing."
    ),
    ReasonDefinition(
        ReasonCode.STALE_BASE, "rejected", "The proposal base snapshot is stale."
    ),
    ReasonDefinition(
        ReasonCode.REPLAY_NONDETERMINISTIC,
        "rejected",
        "Replay produced non-deterministic output.",
    ),
    ReasonDefinition(
        ReasonCode.SCHEMA_INVALID,
        "rejected",
        "The candidate does not satisfy the schema.",
    ),
    ReasonDefinition(
        ReasonCode.HASH_INVALID, "rejected", "A supplied hash is malformed or invalid."
    ),
    ReasonDefinition(
        ReasonCode.GROUP_TRACE_MISSING,
        "rejected",
        "Required group trace evidence is missing.",
    ),
    ReasonDefinition(
        ReasonCode.ACTUAL_ECONOMIC_INVARIANT,
        "rejected",
        "An Actual economic invariant would be violated.",
    ),
    ReasonDefinition(
        ReasonCode.ACTUAL_ID_INVARIANT,
        "rejected",
        "An Actual identity invariant would be violated.",
    ),
    ReasonDefinition(
        ReasonCode.ACTUAL_LINK_INVARIANT,
        "rejected",
        "An Actual link invariant would be violated.",
    ),
    ReasonDefinition(
        ReasonCode.APPROVAL_REQUIRED,
        "review_only",
        "Explicit approval is required before any effect.",
    ),
    ReasonDefinition(
        ReasonCode.EXACT_TARGET_CONFIRMATION_REQUIRED,
        "review_only",
        "The exact target requires confirmation.",
    ),
    ReasonDefinition(
        ReasonCode.EFFECT_POLICY_DRIFT,
        "rejected",
        "The proposed effect differs from policy.",
    ),
    ReasonDefinition(
        ReasonCode.UNKNOWN_REASON, "rejected", "The reason code is not recognized."
    ),
    ReasonDefinition(
        ReasonCode.LEGACY_ACCEPTED, "accepted", "Legacy analysis accepted the proposal."
    ),
    ReasonDefinition(
        ReasonCode.LEGACY_PROPOSAL_NOT_OBJECT,
        "rejected",
        "Legacy proposal output was not an object.",
    ),
    ReasonDefinition(
        ReasonCode.LEGACY_FIELD_NOT_REQUESTED_OR_ALREADY_RESOLVED,
        "rejected",
        "Legacy field was not requested or was already resolved.",
    ),
    ReasonDefinition(
        ReasonCode.LEGACY_PROTECTED_OR_UNSUPPORTED_FIELD,
        "rejected",
        "Legacy field was protected or unsupported.",
    ),
    ReasonDefinition(
        ReasonCode.LEGACY_BELOW_CONFIDENCE_THRESHOLD,
        "review_only",
        "Legacy confidence was below threshold.",
    ),
    ReasonDefinition(
        ReasonCode.LEGACY_VALUE_NOT_ALLOWED, "rejected", "Legacy value was not allowed."
    ),
    ReasonDefinition(
        ReasonCode.LEGACY_BOOLEAN_VALUE_REQUIRED,
        "rejected",
        "Legacy value was required to be boolean.",
    ),
    ReasonDefinition(
        ReasonCode.LEGACY_EMPTY_VALUE, "rejected", "Legacy value was empty."
    ),
    ReasonDefinition(
        ReasonCode.LEGACY_TAG_NOT_ALLOWED, "rejected", "Legacy tag was not allowed."
    ),
    ReasonDefinition(
        ReasonCode.LEGACY_RULE_RECOMMENDATION_MUST_BE_OBJECT,
        "rejected",
        "Legacy rule recommendation was not an object.",
    ),
    ReasonDefinition(
        ReasonCode.LEGACY_CATEGORY_RECOMMENDATION_MUST_NAME_CATEGORY,
        "rejected",
        "Legacy category recommendation lacked a category name.",
    ),
)

_DEFINITIONS_BY_CODE: Final[Mapping[ReasonCode, ReasonDefinition]] = MappingProxyType(
    {definition.code: definition for definition in _DEFINITIONS}
)
REASON_DEFINITIONS: Final[tuple[ReasonDefinition, ...]] = _DEFINITIONS
REASON_CODES: Final[tuple[str, ...]] = tuple(
    definition.code.value for definition in _DEFINITIONS
)
REASON_REGISTRY: Final[Mapping[str, ReasonDefinition]] = MappingProxyType(
    {definition.code.value: definition for definition in _DEFINITIONS}
)


def reason_code(value: str | ReasonCode) -> ReasonCode:
    """Resolve a code exactly; unknown or non-string values fail closed."""
    if isinstance(value, ReasonCode):
        return value
    if not isinstance(value, str):
        raise UnknownReasonCode(value)
    try:
        return ReasonCode(value)
    except ValueError as exc:
        raise UnknownReasonCode(value) from exc


def reason_definition(value: str | ReasonCode) -> ReasonDefinition:
    """Look up immutable metadata for a known code."""
    return _DEFINITIONS_BY_CODE[reason_code(value)]


def reason_record(
    value: str | ReasonCode,
    *,
    detail: str = "",
    field: str | None = None,
    evidence_refs: tuple[str, ...] = (),
) -> ReasonRecord:
    """Construct a typed record only after exact code lookup."""
    return ReasonRecord(
        code=reason_code(value),
        detail=detail,
        field=field,
        evidence_refs=evidence_refs,
    )


def is_known_reason_code(value: object) -> bool:
    """Return false for every unknown or malformed code."""
    try:
        reason_code(value)  # type: ignore[arg-type]
    except (TypeError, UnknownReasonCode):
        return False
    return True


def sort_reasons(values: tuple[ReasonRecord, ...]) -> tuple[ReasonRecord, ...]:
    """Return records in deterministic code/detail/field/reference order."""
    if not isinstance(values, tuple) or any(
        not isinstance(value, ReasonRecord) for value in values
    ):
        raise TypeError("sort_reasons expects a tuple of ReasonRecord")
    return tuple(
        sorted(
            values,
            key=lambda value: (
                value.code.value,
                value.detail,
                value.field or "",
                value.evidence_refs,
            ),
        )
    )


def serialize_reason(value: ReasonRecord) -> str:
    """Serialize one record with canonical JSON settings."""
    if not isinstance(value, ReasonRecord):
        raise TypeError("serialize_reason expects ReasonRecord")
    return json.dumps(
        value.as_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )


def serialize_reasons(values: tuple[ReasonRecord, ...]) -> str:
    """Serialize records in deterministic order with canonical JSON settings."""
    ordered = sort_reasons(values)
    return json.dumps(
        [value.as_dict() for value in ordered],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def serialize_registry() -> str:
    """Serialize the complete closed registry in stable code order."""
    definitions = sorted(REASON_DEFINITIONS, key=lambda item: item.code.value)
    return json.dumps(
        [definition.as_dict() for definition in definitions],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def reasons_hash(values: tuple[ReasonRecord, ...]) -> str:
    """Return the SHA-256 digest of canonical reason serialization."""
    return hashlib.sha256(serialize_reasons(values).encode("utf-8")).hexdigest()


__all__ = (
    "REASON_CODES",
    "REASON_DEFINITIONS",
    "REASON_REGISTRY",
    "ReasonCode",
    "ReasonDefinition",
    "ReasonRecord",
    "UnknownReasonCode",
    "is_known_reason_code",
    "reason_code",
    "reason_definition",
    "reason_record",
    "reasons_hash",
    "serialize_reason",
    "serialize_reasons",
    "serialize_registry",
    "sort_reasons",
)
