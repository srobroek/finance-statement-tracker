"""Pure deterministic matching for proposal-only rule analysis.

The matcher consumes immutable :class:`LedgerObservation` values and emits
immutable, canonically serializable evidence.  It never changes ledger rows,
links, or rule state.  A match is only a relation proposal; Actual mutation is
intentionally outside this module.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Final, cast

from .rule_analysis_reasons import (
    ReasonCode,
    ReasonRecord,
    reason_code,
    reason_record,
)
from .rule_analysis_schema import (
    LedgerObservation,
    SCHEMA_VERSION,
    canonical_json,
    sha256,
)

MATCHER_VERSION: Final[str] = "rule-transfer-matcher-v1"
MATCH_OUTCOMES: Final[tuple[str, ...]] = (
    "UNIQUE",
    "AMBIGUOUS",
    "MISSING",
    "CONFLICTING",
    "EXISTING_LINK",
)

_CHECK_ORDER: Final[tuple[str, ...]] = (
    "transfer_eligible",
    "distinct_row",
    "distinct_account",
    "direction",
    "amount",
    "currency",
    "foreign_amount",
    "settlement_date",
    "reference",
    "counterpart",
    "existing_link",
    "manual_lock",
)
_RELATION_LOCK_FIELDS: Final[frozenset[str]] = frozenset({"existing_link"})
_REWARD_TAGS: Final[frozenset[str]] = frozenset(
    {"cashback", "income", "refund", "reimbursement", "reward"}
)
_TRANSFER_TAGS: Final[frozenset[str]] = frozenset({"card-payment", "transfer"})
_RULE_STAGE_ORDER: Final[Mapping[str, int]] = {
    "TRANSACTION_NORMALIZATION": 10,
    "VENDOR_NORMALIZATION": 20,
    "CLASSIFICATION": 30,
    "TAGGING": 40,
    "EVIDENCE": 50,
    "CASHBACK": 60,
}
_DEFAULT_RULE_ORDER: Final[tuple[tuple[str, int, str], ...]] = (
    ("TRANSACTION_NORMALIZATION", 10, "normalize-card-payment"),
    ("TRANSACTION_NORMALIZATION", 12, "normalize-explicit-account-transfer"),
    ("TRANSACTION_NORMALIZATION", 14, "normalize-ambiguous-incoming-transfer"),
    ("TRANSACTION_NORMALIZATION", 15, "normalize-outgoing-card-payment"),
    ("TRANSACTION_NORMALIZATION", 16, "normalize-outgoing-bank-transfer"),
)


@dataclass(frozen=True, slots=True, order=True)
class RulePrecedence:
    """Static-rule candidacy context, never a counterpart tie-breaker."""

    stage: str
    priority: int
    rule_id: str

    def __post_init__(self) -> None:
        if not self.stage.strip() or not self.rule_id.strip():
            raise ValueError("rule precedence stage and rule_id must not be empty")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise TypeError("rule precedence priority must be an integer")
        if self.stage.strip().upper() not in _RULE_STAGE_ORDER:
            raise ValueError(f"unsupported rule precedence stage {self.stage!r}")
        object.__setattr__(self, "stage", self.stage.strip().upper())
        object.__setattr__(self, "rule_id", self.rule_id.strip())

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "priority": self.priority,
            "rule_id": self.rule_id,
        }


_DEFAULT_PRECEDENCE: Final[tuple[RulePrecedence, ...]] = tuple(
    RulePrecedence(*value) for value in _DEFAULT_RULE_ORDER
)


@dataclass(frozen=True, slots=True)
class MatcherPolicy:
    """Closed policy for deterministic reciprocal-transfer matching."""

    settlement_days: int = 1
    one_to_one: bool = True
    matcher_version: str = MATCHER_VERSION
    rule_precedence: tuple[RulePrecedence, ...] = _DEFAULT_PRECEDENCE

    def __post_init__(self) -> None:
        if (
            isinstance(self.settlement_days, bool)
            or not isinstance(self.settlement_days, int)
            or not 0 <= self.settlement_days <= 31
        ):
            raise ValueError("settlement_days must be an integer from 0 through 31")
        if self.one_to_one is not True:
            raise ValueError("one_to_one matching cannot be disabled")
        if (
            not isinstance(self.matcher_version, str)
            or not self.matcher_version.strip()
        ):
            raise ValueError("matcher_version must not be empty")
        if not isinstance(self.rule_precedence, tuple) or any(
            not isinstance(value, RulePrecedence) for value in self.rule_precedence
        ):
            raise TypeError("rule_precedence must be a tuple of RulePrecedence")
        ordered = tuple(
            sorted(
                self.rule_precedence,
                key=lambda value: (
                    _RULE_STAGE_ORDER[value.stage],
                    value.priority,
                    value.rule_id,
                ),
            )
        )
        if len({value.rule_id for value in ordered}) != len(ordered):
            raise ValueError("rule_precedence contains duplicate rule_id values")
        object.__setattr__(self, "matcher_version", self.matcher_version.strip())
        object.__setattr__(self, "rule_precedence", ordered)

    def to_dict(self) -> dict[str, object]:
        return {
            "settlement_days": self.settlement_days,
            "one_to_one": self.one_to_one,
            "matcher_version": self.matcher_version,
            "rule_precedence": tuple(value.to_dict() for value in self.rule_precedence),
        }


@dataclass(frozen=True, slots=True)
class ObservationSnapshot:
    """Canonical immutable view retaining protected ledger facts verbatim."""

    identity_key: str
    observation_id: str
    observation_hash: str
    source_system: str
    account_id: str
    transaction_id: str
    actual_transaction_id: str | None
    imported_id: str | None
    deduplication_key: str | None
    transaction_at: str
    settlement_date: str
    card: str
    account: str | None
    institution: str | None
    account_last4: str | None
    merchant_raw: str
    amount_aed: str
    amount_minor: int
    amount_original: str | None
    original_amount_minor: int | None
    currency: str
    source_direction: str | None
    signed_actual_minor: int | None
    transaction_type: str
    is_refund: bool
    reconciliation_status: str
    category: str | None
    subcategory: str | None
    property_code: str | None
    rental_unit: str | None
    existing_link: str | None
    manual_locked_fields: tuple[str, ...]
    tags: tuple[str, ...]
    reference: str | None
    counterparty_account_id: str | None
    candidacy_rule_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "identity_key": self.identity_key,
            "observation_id": self.observation_id,
            "observation_hash": self.observation_hash,
            "source_system": self.source_system,
            "account_id": self.account_id,
            "transaction_id": self.transaction_id,
            "actual_transaction_id": self.actual_transaction_id,
            "imported_id": self.imported_id,
            "deduplication_key": self.deduplication_key,
            "transaction_at": self.transaction_at,
            "settlement_date": self.settlement_date,
            "card": self.card,
            "account": self.account,
            "institution": self.institution,
            "account_last4": self.account_last4,
            "merchant_raw": self.merchant_raw,
            "amount_aed": self.amount_aed,
            "amount_minor": self.amount_minor,
            "amount_original": self.amount_original,
            "original_amount_minor": self.original_amount_minor,
            "currency": self.currency,
            "source_direction": self.source_direction,
            "signed_actual_minor": self.signed_actual_minor,
            "transaction_type": self.transaction_type,
            "is_refund": self.is_refund,
            "reconciliation_status": self.reconciliation_status,
            "category": self.category,
            "subcategory": self.subcategory,
            "property_code": self.property_code,
            "rental_unit": self.rental_unit,
            "existing_link": self.existing_link,
            "manual_locked_fields": self.manual_locked_fields,
            "tags": self.tags,
            "reference": self.reference,
            "counterparty_account_id": self.counterparty_account_id,
            "candidacy_rule_ids": self.candidacy_rule_ids,
            "evidence_refs": self.evidence_refs,
        }


@dataclass(frozen=True, slots=True)
class MatchCheck:
    """One replayable hard check for a directed candidate."""

    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {"passed": self.passed, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class Candidate:
    """Immutable directed counterpart candidate, including rejected evidence."""

    candidate_id: str
    candidate_hash: str
    edge_key: str
    source: ObservationSnapshot
    counterpart: ObservationSnapshot
    checks: tuple[MatchCheck, ...]
    eligible: bool
    rejection_reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "candidate_id": self.candidate_id,
            "edge_key": self.edge_key,
            "source": self.source.to_dict(),
            "counterpart": self.counterpart.to_dict(),
            "checks": {check.name: check.to_dict() for check in self.checks},
            "eligible": self.eligible,
            "rejection_reasons": self.rejection_reasons,
            "evidence_refs": self.evidence_refs,
        }
        if include_hash:
            value["candidate_hash"] = self.candidate_hash
        return value


@dataclass(frozen=True, slots=True)
class Trace:
    """Replayable decision trace for one source observation."""

    trace_hash: str
    schema_version: int
    matcher_version: str
    run_id: str
    canonical_input_sha256: str
    source: ObservationSnapshot
    candidates: tuple[Candidate, ...]
    outcome: str
    selected_edge: str | None
    reason_codes: tuple[str, ...]
    policy: MatcherPolicy

    def __post_init__(self) -> None:
        if self.outcome not in MATCH_OUTCOMES:
            raise ValueError(f"unsupported match outcome {self.outcome!r}")
        for value in self.reason_codes:
            reason_code(value)

    @property
    def reasons(self) -> tuple[ReasonRecord, ...]:
        return tuple(
            reason_record(value, evidence_refs=self.source.evidence_refs)
            for value in self.reason_codes
        )

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": self.schema_version,
            "matcher_version": self.matcher_version,
            "run_id": self.run_id,
            "canonical_input_sha256": self.canonical_input_sha256,
            "source": self.source.to_dict(),
            "candidates": tuple(candidate.to_dict() for candidate in self.candidates),
            "outcome": self.outcome,
            "selected_edge": self.selected_edge,
            "reason_codes": self.reason_codes,
            "policy": self.policy.to_dict(),
        }
        if include_hash:
            value["trace_hash"] = self.trace_hash
        return value


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Canonical batch result preserving global one-to-one ambiguity."""

    schema_version: int
    matcher_version: str
    run_id: str
    canonical_input_sha256: str
    candidates: tuple[Candidate, ...]
    traces: tuple[Trace, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "matcher_version": self.matcher_version,
            "run_id": self.run_id,
            "canonical_input_sha256": self.canonical_input_sha256,
            "candidates": tuple(candidate.to_dict() for candidate in self.candidates),
            "traces": tuple(trace.to_dict() for trace in self.traces),
        }


ObservationInput = LedgerObservation | Mapping[str, Any]
ObservationCollection = ObservationInput | Iterable[ObservationInput]


def _text(value: object) -> str | None:
    if value is None:
        return None
    parsed = str(value).strip()
    return parsed or None


def _metadata_text(observation: LedgerObservation, *names: str) -> str | None:
    for name in names:
        parsed = _text(observation.redacted_metadata.get(name))
        if parsed is not None:
            return parsed
    return None


def _minor_units(value: object, name: str) -> int:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal amount") from exc
    minor = parsed * 100
    if not minor.is_finite() or minor != minor.to_integral_value():
        raise ValueError(f"{name} must have at most two decimal places")
    return int(minor)


def _signed_actual_minor(observation: LedgerObservation) -> int | None:
    for name in ("actual_amount_minor", "signed_amount_minor"):
        value = observation.redacted_metadata.get(name)
        if value is not None:
            if isinstance(value, bool):
                raise TypeError(f"redacted_metadata.{name} must be an integer")
            try:
                parsed = int(str(value))
            except ValueError as exc:
                raise ValueError(
                    f"redacted_metadata.{name} must be an integer"
                ) from exc
            if parsed == 0:
                raise ValueError(f"redacted_metadata.{name} must be non-zero")
            return parsed
    for name in ("actual_amount", "signed_amount_aed"):
        value = observation.redacted_metadata.get(name)
        if value is not None:
            parsed = _minor_units(value, f"redacted_metadata.{name}")
            if parsed == 0:
                raise ValueError(f"redacted_metadata.{name} must be non-zero")
            return parsed
    return None


def _reference(observation: LedgerObservation) -> str | None:
    value = _metadata_text(
        observation,
        "trusted_reference",
        "transfer_reference",
        "statement_reference",
        "reference",
    )
    if value is None:
        return None
    normalized = "".join(
        character for character in value.casefold() if character.isalnum()
    )
    return normalized or None


def _rule_ids(observation: LedgerObservation, policy: MatcherPolicy) -> tuple[str, ...]:
    raw = observation.redacted_metadata.get("matched_rule_ids")
    if raw is None:
        raw = observation.redacted_metadata.get("rule_ids")
    if raw is None:
        raw = observation.redacted_metadata.get("rule_id")
    if raw is None:
        values: tuple[str, ...] = ()
    elif isinstance(raw, str):
        values = (raw.strip(),) if raw.strip() else ()
    elif isinstance(raw, (tuple, list)) and all(
        isinstance(value, str) for value in raw
    ):
        values = tuple(value.strip() for value in raw if value.strip())
    else:
        raise TypeError("rule candidacy metadata must contain strings")
    rank = {
        value.rule_id: (value.stage, value.priority, value.rule_id)
        for value in policy.rule_precedence
    }
    return tuple(
        sorted(set(values), key=lambda value: rank.get(value, ("~", 2**31, value)))
    )


def _snapshot(
    observation: LedgerObservation, policy: MatcherPolicy
) -> ObservationSnapshot:
    account_id = _metadata_text(observation, "account_id", "actual_account_id")
    account_id = account_id or observation.account or observation.card
    actual_id = _metadata_text(observation, "actual_transaction_id", "actual_id")
    imported_id = _metadata_text(observation, "imported_id", "import_id")
    row_kind: str
    row_id: str
    if actual_id is not None:
        row_kind, row_id = "actual_transaction_id", actual_id
    elif imported_id is not None:
        row_kind, row_id = "imported_id", imported_id
    else:
        row_kind, row_id = "transaction_id", observation.transaction_id
    identity_key = sha256(
        {
            "source_system": observation.source_type,
            "account_id": account_id,
            "row_id_kind": row_kind,
            "row_id": row_id,
        }
    )
    amount_original = observation.amount_original
    return ObservationSnapshot(
        identity_key=identity_key,
        observation_id=observation.observation_id,
        observation_hash=str(observation.observation_hash),
        source_system=observation.source_type,
        account_id=account_id,
        transaction_id=observation.transaction_id,
        actual_transaction_id=actual_id,
        imported_id=imported_id,
        deduplication_key=_metadata_text(observation, "deduplication_key"),
        transaction_at=observation.transaction_at,
        settlement_date=date.fromisoformat(observation.transaction_at[:10]).isoformat(),
        card=observation.card,
        account=observation.account,
        institution=observation.institution,
        account_last4=observation.account_last4,
        merchant_raw=observation.merchant_raw,
        amount_aed=str(observation.amount_aed),
        amount_minor=_minor_units(observation.amount_aed, "amount_aed"),
        amount_original=None if amount_original is None else str(amount_original),
        original_amount_minor=(
            None
            if amount_original is None
            else _minor_units(amount_original, "amount_original")
        ),
        currency=observation.currency,
        source_direction=observation.source_direction,
        signed_actual_minor=_signed_actual_minor(observation),
        transaction_type=observation.transaction_type,
        is_refund=observation.is_refund,
        reconciliation_status=observation.reconciliation_status,
        category=observation.category,
        subcategory=observation.subcategory,
        property_code=observation.property_code,
        rental_unit=observation.rental_unit,
        existing_link=observation.existing_link,
        manual_locked_fields=observation.manual_locked_fields,
        tags=observation.tags,
        reference=_reference(observation),
        counterparty_account_id=_metadata_text(
            observation, "counterparty_account_id", "transfer_account_id"
        ),
        candidacy_rule_ids=_rule_ids(observation, policy),
        evidence_refs=observation.evidence_refs,
    )


def _coerce_observation(value: object) -> LedgerObservation:
    if isinstance(value, LedgerObservation):
        return value
    if isinstance(value, Mapping):
        return LedgerObservation.from_mapping(cast(Mapping[str, Any], value))
    raise TypeError("matcher inputs must be LedgerObservation values or mappings")


def _coerce_collection(
    value: ObservationCollection,
    *,
    name: str,
    allow_empty: bool,
) -> tuple[LedgerObservation, ...]:
    if isinstance(value, (LedgerObservation, Mapping)):
        values = (_coerce_observation(value),)
    else:
        if isinstance(value, (str, bytes)):
            raise TypeError(f"{name} must contain LedgerObservation values")
        values = tuple(_coerce_observation(item) for item in value)
    if not values and not allow_empty:
        raise ValueError(f"{name} must not be empty")
    return values


def _unique_snapshots(
    values: Iterable[ObservationSnapshot],
) -> tuple[ObservationSnapshot, ...]:
    by_identity: dict[str, ObservationSnapshot] = {}
    for value in values:
        previous = by_identity.get(value.identity_key)
        if previous is not None and canonical_json(
            previous.to_dict()
        ) != canonical_json(value.to_dict()):
            raise ValueError(
                "conflicting observations share one canonical source identity"
            )
        by_identity[value.identity_key] = value
    return tuple(sorted(by_identity.values(), key=lambda value: value.identity_key))


def _transfer_eligible(value: ObservationSnapshot) -> bool:
    tags = {tag.casefold() for tag in value.tags}
    explicit_transfer = value.transaction_type == "TRANSFER"
    tagged_transfer = bool(tags & _TRANSFER_TAGS)
    excluded = (
        value.is_refund
        or value.transaction_type in {"INCOME", "REFUND", "REWARD"}
        or bool(tags & _REWARD_TAGS)
    )
    return explicit_transfer or (tagged_transfer and not excluded)


def _direction_check(
    left: ObservationSnapshot, right: ObservationSnapshot
) -> tuple[bool, str]:
    if left.source_direction is not None or right.source_direction is not None:
        passed = (
            left.source_direction is not None
            and right.source_direction is not None
            and left.source_direction != right.source_direction
        )
        return (
            passed,
            "opposite_source_directions"
            if passed
            else "source_directions_not_opposite",
        )
    passed = (
        left.signed_actual_minor is not None
        and right.signed_actual_minor is not None
        and left.signed_actual_minor * right.signed_actual_minor < 0
    )
    return (
        passed,
        "inverse_signed_actual_amounts"
        if passed
        else "signed_actual_amounts_unavailable_or_not_inverse",
    )


def _link_identifiers(value: ObservationSnapshot) -> frozenset[str]:
    return frozenset(
        item
        for item in (
            value.observation_id,
            value.transaction_id,
            value.actual_transaction_id,
            value.imported_id,
        )
        if item is not None
    )


def _reciprocal_link(left: ObservationSnapshot, right: ObservationSnapshot) -> bool:
    return (
        left.existing_link is not None
        and right.existing_link is not None
        and left.existing_link in _link_identifiers(right)
        and right.existing_link in _link_identifiers(left)
    )


def _checks(
    left: ObservationSnapshot,
    right: ObservationSnapshot,
    policy: MatcherPolicy,
) -> tuple[MatchCheck, ...]:
    direction_ok, direction_detail = _direction_check(left, right)
    foreign = left.currency != "AED" or right.currency != "AED"
    foreign_amount_ok = not foreign or (
        left.original_amount_minor is not None
        and right.original_amount_minor is not None
        and left.original_amount_minor == right.original_amount_minor
    )
    day_gap = abs(
        (
            date.fromisoformat(left.settlement_date)
            - date.fromisoformat(right.settlement_date)
        ).days
    )
    reference_ok = (
        left.reference is None
        or right.reference is None
        or left.reference == right.reference
    )
    counterpart_ok = (
        left.counterparty_account_id is None
        or left.counterparty_account_id == right.account_id
    ) and (
        right.counterparty_account_id is None
        or right.counterparty_account_id == left.account_id
    )
    reciprocal = _reciprocal_link(left, right)
    links_clear_or_reciprocal = (
        left.existing_link is None and right.existing_link is None
    ) or reciprocal
    relation_locked = bool(
        _RELATION_LOCK_FIELDS
        & (set(left.manual_locked_fields) | set(right.manual_locked_fields))
    )
    lock_ok = not relation_locked or reciprocal
    values = (
        MatchCheck(
            "transfer_eligible",
            _transfer_eligible(left) and _transfer_eligible(right),
            "both_transfer_candidates"
            if _transfer_eligible(left) and _transfer_eligible(right)
            else "one_or_both_rows_not_transfer_candidates",
        ),
        MatchCheck(
            "distinct_row",
            left.identity_key != right.identity_key,
            "distinct_source_identity"
            if left.identity_key != right.identity_key
            else "same_source_identity",
        ),
        MatchCheck(
            "distinct_account",
            left.account_id != right.account_id,
            "distinct_accounts"
            if left.account_id != right.account_id
            else "same_account",
        ),
        MatchCheck("direction", direction_ok, direction_detail),
        MatchCheck(
            "amount",
            left.amount_minor == right.amount_minor,
            "exact_aed_minor_amount"
            if left.amount_minor == right.amount_minor
            else "aed_minor_amount_mismatch",
        ),
        MatchCheck(
            "currency",
            left.currency == right.currency,
            "exact_currency"
            if left.currency == right.currency
            else "currency_mismatch",
        ),
        MatchCheck(
            "foreign_amount",
            foreign_amount_ok,
            "exact_original_minor_amount"
            if foreign and foreign_amount_ok
            else "not_foreign"
            if not foreign
            else "original_minor_amount_missing_or_mismatch",
        ),
        MatchCheck(
            "settlement_date",
            day_gap <= policy.settlement_days,
            f"day_gap={day_gap};tolerance={policy.settlement_days}",
        ),
        MatchCheck(
            "reference",
            reference_ok,
            "exact_normalized_reference"
            if left.reference is not None
            and right.reference is not None
            and reference_ok
            else "reference_not_constrained"
            if left.reference is None or right.reference is None
            else "normalized_reference_mismatch",
        ),
        MatchCheck(
            "counterpart",
            counterpart_ok,
            "counterpart_account_satisfied"
            if counterpart_ok
            else "counterpart_account_mismatch",
        ),
        MatchCheck(
            "existing_link",
            links_clear_or_reciprocal,
            "reciprocal_existing_link"
            if reciprocal
            else "links_clear"
            if left.existing_link is None and right.existing_link is None
            else "existing_link_conflict",
        ),
        MatchCheck(
            "manual_lock",
            lock_ok,
            "reciprocal_link_preserved"
            if relation_locked and reciprocal
            else "relation_unlocked"
            if not relation_locked
            else "manual_relation_lock_conflict",
        ),
    )
    if tuple(value.name for value in values) != _CHECK_ORDER:
        raise AssertionError("matcher check order drifted")
    return values


def _rejection_reasons(checks: tuple[MatchCheck, ...]) -> tuple[str, ...]:
    by_check = {
        "transfer_eligible": ReasonCode.MATCH_CONFLICTING,
        "distinct_row": ReasonCode.MATCH_CONFLICTING,
        "distinct_account": ReasonCode.MATCH_CONFLICTING,
        "direction": ReasonCode.TRANSFER_DIRECTION_MISMATCH,
        "amount": ReasonCode.TRANSFER_AMOUNT_MISMATCH,
        "currency": ReasonCode.TRANSFER_CURRENCY_MISMATCH,
        "foreign_amount": ReasonCode.TRANSFER_AMOUNT_MISMATCH,
        "settlement_date": ReasonCode.TRANSFER_DATE_MISMATCH,
        "reference": ReasonCode.MATCH_CONFLICTING,
        "counterpart": ReasonCode.MATCH_CONFLICTING,
        "existing_link": ReasonCode.MATCH_CONFLICTING,
        "manual_lock": ReasonCode.MANUAL_LOCK_CONFLICT,
    }
    return tuple(
        sorted(
            {
                reason_code(by_check[value.name]).value
                for value in checks
                if not value.passed
            }
        )
    )


def _candidate(
    left: ObservationSnapshot,
    right: ObservationSnapshot,
    policy: MatcherPolicy,
) -> Candidate:
    checks = _checks(left, right, policy)
    candidate_id = sha256(
        {
            "source_key": left.identity_key,
            "candidate_key": right.identity_key,
            "matcher_version": policy.matcher_version,
        }
    )
    edge_key = sha256(
        {
            "endpoints": tuple(sorted((left.identity_key, right.identity_key))),
            "matcher_version": policy.matcher_version,
        }
    )
    base = Candidate(
        candidate_id=candidate_id,
        candidate_hash="",
        edge_key=edge_key,
        source=left,
        counterpart=right,
        checks=checks,
        eligible=all(value.passed for value in checks),
        rejection_reasons=_rejection_reasons(checks),
        evidence_refs=tuple(sorted(set(left.evidence_refs) | set(right.evidence_refs))),
    )
    return replace(base, candidate_hash=sha256(base.to_dict(include_hash=False)))


def _core_checks_pass(candidate: Candidate) -> bool:
    return all(
        check.passed
        for check in candidate.checks
        if check.name not in {"existing_link", "manual_lock"}
    )


def _connected_component(
    start: str, neighbors: Mapping[str, frozenset[str]]
) -> frozenset[str]:
    pending = [start]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(sorted(neighbors.get(current, frozenset()) - visited))
    return frozenset(visited)


def _trace(
    *,
    source: ObservationSnapshot,
    candidates: tuple[Candidate, ...],
    outcome: str,
    selected_edge: str | None,
    reason_codes: tuple[str, ...],
    policy: MatcherPolicy,
    input_hash: str,
    run_id: str,
) -> Trace:
    normalized_reasons = tuple(
        sorted({reason_code(value).value for value in reason_codes})
    )
    base = Trace(
        trace_hash="",
        schema_version=SCHEMA_VERSION,
        matcher_version=policy.matcher_version,
        run_id=run_id,
        canonical_input_sha256=input_hash,
        source=source,
        candidates=candidates,
        outcome=outcome,
        selected_edge=selected_edge,
        reason_codes=normalized_reasons,
        policy=policy,
    )
    return replace(base, trace_hash=sha256(base.to_dict(include_hash=False)))


def _match(
    source: ObservationCollection,
    corpus: ObservationCollection,
    *,
    policy: MatcherPolicy | None,
) -> MatchResult:
    selected_policy = policy or MatcherPolicy()
    if not isinstance(selected_policy, MatcherPolicy):
        raise TypeError("policy must be MatcherPolicy or None")

    source_values = _coerce_collection(source, name="source", allow_empty=False)
    corpus_values = _coerce_collection(corpus, name="corpus", allow_empty=True)
    source_snapshots = _unique_snapshots(
        _snapshot(value, selected_policy) for value in source_values
    )
    pool = _unique_snapshots(
        _snapshot(value, selected_policy) for value in (*source_values, *corpus_values)
    )

    input_hash = sha256(
        {
            "sources": tuple(value.to_dict() for value in source_snapshots),
            "corpus": tuple(value.to_dict() for value in pool),
            "policy": selected_policy.to_dict(),
        }
    )
    run_id = sha256(
        {
            "canonical_input_sha256": input_hash,
            "matcher_version": selected_policy.matcher_version,
        }
    )

    directed: dict[tuple[str, str], Candidate] = {}
    for left in pool:
        for right in pool:
            if left.identity_key == right.identity_key:
                continue
            directed[(left.identity_key, right.identity_key)] = _candidate(
                left, right, selected_policy
            )

    neighbors_mutable: dict[str, set[str]] = {
        value.identity_key: set() for value in pool
    }
    for left_index, left in enumerate(pool):
        for right in pool[left_index + 1 :]:
            candidate = directed[(left.identity_key, right.identity_key)]
            if candidate.eligible:
                neighbors_mutable[left.identity_key].add(right.identity_key)
                neighbors_mutable[right.identity_key].add(left.identity_key)
    neighbors = {key: frozenset(values) for key, values in neighbors_mutable.items()}

    traces: list[Trace] = []
    result_candidates: list[Candidate] = []
    for current in source_snapshots:
        candidates = tuple(
            directed[(current.identity_key, other.identity_key)]
            for other in pool
            if other.identity_key != current.identity_key
        )
        candidates = tuple(sorted(candidates, key=lambda value: value.candidate_id))
        result_candidates.extend(candidates)

        linked = tuple(
            candidate
            for candidate in candidates
            if _reciprocal_link(candidate.source, candidate.counterpart)
            and _core_checks_pass(candidate)
            and candidate.eligible
        )
        if current.existing_link is not None:
            if len(linked) == 1:
                outcome = "EXISTING_LINK"
                selected_edge = linked[0].edge_key
                reasons = (ReasonCode.DETERMINISTIC_PRECEDENCE.value,)
            else:
                outcome = "CONFLICTING"
                selected_edge = None
                reasons = (ReasonCode.MATCH_CONFLICTING.value,)
                if _RELATION_LOCK_FIELDS & set(current.manual_locked_fields):
                    reasons += (ReasonCode.MANUAL_LOCK_CONFLICT.value,)
        elif _RELATION_LOCK_FIELDS & set(current.manual_locked_fields):
            outcome = "CONFLICTING"
            selected_edge = None
            reasons = (ReasonCode.MANUAL_LOCK_CONFLICT.value,)
        else:
            peers = neighbors.get(current.identity_key, frozenset())
            if not peers:
                precedence_conflict = any(
                    _core_checks_pass(candidate) and not candidate.eligible
                    for candidate in candidates
                )
                outcome = "CONFLICTING" if precedence_conflict else "MISSING"
                selected_edge = None
                reasons = (
                    (ReasonCode.MATCH_CONFLICTING.value,)
                    if precedence_conflict
                    else (ReasonCode.TRANSFER_COUNTERPART_NOT_FOUND.value,)
                )
            else:
                component = _connected_component(current.identity_key, neighbors)
                unique = (
                    len(component) == 2
                    and len(peers) == 1
                    and all(len(neighbors[value]) == 1 for value in component)
                )
                if unique:
                    peer_key = next(iter(peers))
                    outcome = "UNIQUE"
                    selected_edge = directed[(current.identity_key, peer_key)].edge_key
                    reasons = (ReasonCode.TRANSFER_MATCH_UNIQUE.value,)
                else:
                    outcome = "AMBIGUOUS"
                    selected_edge = None
                    reasons = (ReasonCode.TRANSFER_MATCH_AMBIGUOUS.value,)
        traces.append(
            _trace(
                source=current,
                candidates=candidates,
                outcome=outcome,
                selected_edge=selected_edge,
                reason_codes=reasons,
                policy=selected_policy,
                input_hash=input_hash,
                run_id=run_id,
            )
        )

    ordered_candidates = tuple(
        sorted(
            {value.candidate_id: value for value in result_candidates}.values(),
            key=lambda value: value.candidate_id,
        )
    )
    return MatchResult(
        schema_version=SCHEMA_VERSION,
        matcher_version=selected_policy.matcher_version,
        run_id=run_id,
        canonical_input_sha256=input_hash,
        candidates=ordered_candidates,
        traces=tuple(sorted(traces, key=lambda value: value.source.identity_key)),
    )


def match_observations(
    source: ObservationCollection,
    corpus: ObservationCollection,
    *,
    policy: MatcherPolicy | None = None,
) -> MatchResult:
    """Match one or many immutable observations with global uniqueness checks."""

    return _match(source, corpus, policy=policy)


def match_transfers(
    source: ObservationCollection,
    corpus: ObservationCollection,
    *,
    policy: MatcherPolicy | None = None,
) -> MatchResult:
    """Match reciprocal transfer candidates without mutating either collection."""

    return _match(source, corpus, policy=policy)


def serialize_candidate(value: Candidate) -> str:
    if not isinstance(value, Candidate):
        raise TypeError("serialize_candidate expects Candidate")
    return canonical_json(value.to_dict())


def serialize_trace(value: Trace) -> str:
    if not isinstance(value, Trace):
        raise TypeError("serialize_trace expects Trace")
    return canonical_json(value.to_dict())


def serialize_match_result(value: MatchResult) -> str:
    if not isinstance(value, MatchResult):
        raise TypeError("serialize_match_result expects MatchResult")
    return canonical_json(value.to_dict())


__all__ = (
    "MATCHER_VERSION",
    "MATCH_OUTCOMES",
    "Candidate",
    "MatchCheck",
    "MatchResult",
    "MatcherPolicy",
    "ObservationSnapshot",
    "RulePrecedence",
    "Trace",
    "match_observations",
    "match_transfers",
    "serialize_candidate",
    "serialize_match_result",
    "serialize_trace",
)
