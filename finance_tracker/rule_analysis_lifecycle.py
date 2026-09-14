"""Pure, immutable lifecycle state for proposal-only rule analysis.

This module records candidates, proposals, review feedback, locks, and exact-target
approvals.  It deliberately exposes no execution surface: accepting a proposal is
only a review-state transition and never writes to a ledger or another store.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from .rule_analysis_reasons import ReasonCode, reason_code, reason_definition
from .rule_analysis_schema import (
    POLICY_A_EFFECT_MATRIX,
    PROTECTED_ACTUAL_STRUCTURAL_FIELDS,
    PROTECTED_ECONOMIC_FIELDS,
    PROTECTED_IDENTITY_FIELDS,
    CandidateEnvelope,
    FeedbackEnvelope,
    ProposalEnvelope,
    canonical_sha256,
    validate_policy_a_target,
    validate_protected_changes,
)

CANDIDATE_STATES = ("CANDIDATE", "REVIEW_ONLY", "REJECTED")
PROPOSAL_STATES = ("PROPOSED", "REVIEW_ONLY", "ACCEPTED", "REJECTED", "STALE")
TERMINAL_PROPOSAL_STATES = frozenset({"REJECTED", "STALE"})

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_AMBIGUOUS_REASONS = frozenset(
    {
        ReasonCode.MATCH_AMBIGUOUS.value,
        ReasonCode.MATCH_CONFLICTING.value,
        ReasonCode.TRANSFER_MATCH_AMBIGUOUS.value,
    }
)
_STALE_REASONS = frozenset(
    {ReasonCode.STALE_PROPOSAL.value, ReasonCode.STALE_BASE.value}
)


class LifecycleRejection(ValueError):
    """Deterministic, reason-coded rejection of a lifecycle operation."""

    def __init__(self, code: str | ReasonCode, detail: str) -> None:
        self.reason_code = reason_code(code).value
        self.detail = detail
        super().__init__(f"{self.reason_code}: {detail}")


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _hash(value: Any, name: str) -> str:
    parsed = _identifier(value, name)
    if not _HASH_RE.fullmatch(parsed):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return parsed


def _names(values: Any, name: str) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list, set, frozenset)):
        raise TypeError(f"{name} must be a collection of strings")
    return tuple(sorted({_identifier(value, name) for value in values}))


def _reason(value: str | ReasonCode) -> str:
    return reason_code(value).value


def _protected_reason(field: str) -> ReasonCode | None:
    if field in PROTECTED_IDENTITY_FIELDS:
        return ReasonCode.PROTECTED_IDENTITY_FIELD
    if field in PROTECTED_ECONOMIC_FIELDS:
        return ReasonCode.PROTECTED_ECONOMIC_FIELD
    if field in PROTECTED_ACTUAL_STRUCTURAL_FIELDS:
        return ReasonCode.PROTECTED_ACTUAL_FIELD
    return None


def _feedback_outcome(decision: str) -> str:
    return {
        "ACCEPTED": "accepted",
        "REJECTED": "rejected",
        "REVIEW_ONLY": "review_only",
    }[decision]


@dataclass(frozen=True, slots=True)
class ManualLocks:
    """Immutable effective manual field and relation locks."""

    fields: tuple[str, ...] = ()
    relations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", _names(self.fields, "fields"))
        object.__setattr__(self, "relations", _names(self.relations, "relations"))

    def field_is_locked(self, field: str) -> bool:
        parsed = _identifier(field, "field")
        return parsed in self.fields or _protected_reason(parsed) is not None

    def relation_is_locked(self, relation: str) -> bool:
        return _identifier(relation, "relation") in self.relations

    def with_field_locked(self, field: str) -> ManualLocks:
        parsed = _identifier(field, "field")
        return replace(self, fields=(*self.fields, parsed))

    def with_field_released(self, field: str) -> ManualLocks:
        parsed = _identifier(field, "field")
        return replace(
            self, fields=tuple(value for value in self.fields if value != parsed)
        )

    def with_relation_locked(self, relation: str) -> ManualLocks:
        parsed = _identifier(relation, "relation")
        return replace(self, relations=(*self.relations, parsed))

    def with_relation_released(self, relation: str) -> ManualLocks:
        parsed = _identifier(relation, "relation")
        return replace(
            self,
            relations=tuple(value for value in self.relations if value != parsed),
        )

    def to_dict(self) -> dict[str, list[str]]:
        return {"fields": list(self.fields), "relations": list(self.relations)}


@dataclass(frozen=True, slots=True)
class ExactTargetApproval:
    """Human approval bound to one proposal and one immutable exact target."""

    proposal_id: str
    proposal_hash: str
    candidate_id: str
    candidate_hash: str
    observation_id: str
    observation_hash: str
    field: str
    target_kind: str
    value_hash: str
    replay_key: str
    exact_target_ref: str
    exact_target_hash: str
    actor_id: str
    approved_at: str
    relation: str | None = None
    approval_hash: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "proposal_id",
            "candidate_id",
            "observation_id",
            "field",
            "target_kind",
            "replay_key",
            "exact_target_ref",
            "actor_id",
            "approved_at",
        ):
            object.__setattr__(self, name, _identifier(getattr(self, name), name))
        for name in (
            "proposal_hash",
            "candidate_hash",
            "observation_hash",
            "value_hash",
            "exact_target_hash",
        ):
            object.__setattr__(self, name, _hash(getattr(self, name), name))
        if self.relation is not None:
            object.__setattr__(self, "relation", _identifier(self.relation, "relation"))
        expected = canonical_sha256(self._without_hash())
        if (
            self.approval_hash is not None
            and _hash(self.approval_hash, "approval_hash") != expected
        ):
            raise ValueError("approval_hash does not match exact-target approval facts")
        object.__setattr__(self, "approval_hash", expected)

    @classmethod
    def for_proposal(
        cls,
        proposal: ProposalEnvelope,
        *,
        replay_key: str,
        exact_target_ref: str,
        exact_target_hash: str,
        actor_id: str,
        approved_at: str,
        relation: str | None = None,
    ) -> ExactTargetApproval:
        return cls(
            proposal_id=proposal.proposal_id,
            proposal_hash=_hash(proposal.proposal_hash, "proposal_hash"),
            candidate_id=proposal.candidate_id,
            candidate_hash=_hash(proposal.candidate_hash, "candidate_hash"),
            observation_id=proposal.observation_id,
            observation_hash=proposal.observation_hash,
            field=proposal.field,
            target_kind=proposal.target_kind,
            value_hash=canonical_sha256(proposal.value),
            replay_key=replay_key,
            exact_target_ref=exact_target_ref,
            exact_target_hash=exact_target_hash,
            actor_id=actor_id,
            approved_at=approved_at,
            relation=relation,
        )

    def matches(
        self, proposal: ProposalEnvelope, replay_key: str, relation: str | None
    ) -> bool:
        return (
            self.proposal_id == proposal.proposal_id
            and self.proposal_hash == proposal.proposal_hash
            and self.candidate_id == proposal.candidate_id
            and self.candidate_hash == proposal.candidate_hash
            and self.observation_id == proposal.observation_id
            and self.observation_hash == proposal.observation_hash
            and self.field == proposal.field
            and self.target_kind == proposal.target_kind
            and self.value_hash == canonical_sha256(proposal.value)
            and self.replay_key == replay_key
            and self.relation == relation
        )

    def _without_hash(self) -> dict[str, Any]:
        value = self.to_dict()
        value.pop("approval_hash", None)
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "proposal_hash": self.proposal_hash,
            "candidate_id": self.candidate_id,
            "candidate_hash": self.candidate_hash,
            "observation_id": self.observation_id,
            "observation_hash": self.observation_hash,
            "field": self.field,
            "target_kind": self.target_kind,
            "value_hash": self.value_hash,
            "replay_key": self.replay_key,
            "exact_target_ref": self.exact_target_ref,
            "exact_target_hash": self.exact_target_hash,
            "actor_id": self.actor_id,
            "approved_at": self.approved_at,
            "relation": self.relation,
            "approval_hash": self.approval_hash,
        }


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    """Immutable lifecycle classification of one candidate envelope."""

    envelope: CandidateEnvelope
    state: str
    ambiguous: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.envelope, CandidateEnvelope):
            raise TypeError("envelope must be a CandidateEnvelope")
        if self.state not in CANDIDATE_STATES:
            raise ValueError("illegal candidate state")
        registered_reason = _reason(self.envelope.reason_code)
        if self.ambiguous != (registered_reason in _AMBIGUOUS_REASONS):
            raise ValueError(
                "candidate ambiguity must follow its registered reason code"
            )
        if self.ambiguous and self.state != "REVIEW_ONLY":
            raise ValueError("ambiguous candidates must remain review-only")

    def to_dict(self) -> dict[str, Any]:
        return {
            "envelope": self.envelope.to_dict(),
            "state": self.state,
            "ambiguous": self.ambiguous,
        }


@dataclass(frozen=True, slots=True)
class ProposalRecord:
    """Immutable proposal state plus its append-only feedback chain."""

    envelope: ProposalEnvelope
    replay_key: str
    initial_state: str = "PROPOSED"
    state: str = "PROPOSED"
    relation: str | None = None
    ambiguous: bool = False
    feedback: tuple[FeedbackEnvelope, ...] = ()
    approval: ExactTargetApproval | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.envelope, ProposalEnvelope):
            raise TypeError("envelope must be a ProposalEnvelope")
        object.__setattr__(
            self, "replay_key", _identifier(self.replay_key, "replay_key")
        )
        if self.initial_state not in {"PROPOSED", "REVIEW_ONLY"}:
            raise ValueError("illegal initial proposal state")
        if self.state not in PROPOSAL_STATES:
            raise ValueError("illegal proposal state")
        if self.relation is not None:
            object.__setattr__(self, "relation", _identifier(self.relation, "relation"))
        if not isinstance(self.feedback, tuple):
            raise TypeError("feedback must be an append-only tuple")
        if self.ambiguous and self.initial_state != "REVIEW_ONLY":
            raise ValueError("ambiguous proposals must begin review-only")
        expected_state = self.initial_state
        previous_hash: str | None = None
        feedback_ids: set[str] = set()
        feedback_hashes: set[str] = set()
        accepted = False
        for sequence, event in enumerate(self.feedback, start=1):
            if not isinstance(event, FeedbackEnvelope):
                raise TypeError("feedback must contain FeedbackEnvelope values")
            self._validate_feedback_binding(event)
            if (
                event.sequence != sequence
                or event.previous_feedback_hash != previous_hash
            ):
                raise LifecycleRejection(
                    ReasonCode.REPLAY_DRIFT,
                    "feedback sequence or previous hash does not replay exactly",
                )
            feedback_hash = _hash(event.feedback_hash, "feedback_hash")
            if event.feedback_id in feedback_ids or feedback_hash in feedback_hashes:
                raise LifecycleRejection(
                    ReasonCode.REPLAY_DRIFT, "duplicate feedback replay identity"
                )
            feedback_ids.add(event.feedback_id)
            feedback_hashes.add(feedback_hash)
            registered = reason_definition(event.reason_code)
            if registered.outcome != _feedback_outcome(event.decision):
                raise ValueError("feedback decision does not match its reason outcome")
            if expected_state in TERMINAL_PROPOSAL_STATES:
                raise ValueError("terminal proposal feedback cannot be extended")
            if event.decision == "ACCEPTED":
                if self.ambiguous:
                    raise LifecycleRejection(
                        ReasonCode.REVIEW_ONLY,
                        "ambiguous candidates cannot become accepted",
                    )
                expected_state = "ACCEPTED"
                accepted = True
            elif event.decision == "REVIEW_ONLY":
                if expected_state == "ACCEPTED":
                    raise ValueError("accepted proposals cannot return to review-only")
                expected_state = "REVIEW_ONLY"
            else:
                expected_state = (
                    "STALE"
                    if _reason(event.reason_code) in _STALE_REASONS
                    else "REJECTED"
                )
            previous_hash = event.feedback_hash
        if self.state != expected_state:
            raise ValueError(
                "proposal state does not match append-only feedback replay"
            )
        if accepted:
            if self.approval is None or not self.approval.matches(
                self.envelope, self.replay_key, self.relation
            ):
                raise LifecycleRejection(
                    ReasonCode.EXACT_TARGET_CONFIRMATION_REQUIRED,
                    "accepted feedback requires matching exact-target approval",
                )
        elif self.approval is not None:
            raise ValueError("approval metadata is valid only after accepted feedback")

    @property
    def suggestion_only(self) -> bool:
        return self.envelope.target_kind == "SUGGESTION_ONLY"

    @property
    def executable(self) -> bool:
        return False

    def _validate_feedback_binding(self, event: FeedbackEnvelope) -> None:
        if (
            event.proposal_id != self.envelope.proposal_id
            or event.proposal_hash != self.envelope.proposal_hash
            or event.observation_id != self.envelope.observation_id
            or event.observation_hash != self.envelope.observation_hash
        ):
            raise LifecycleRejection(
                ReasonCode.HASH_MISMATCH,
                "feedback is not bound to the exact proposal and observation",
            )

    def with_feedback(
        self,
        event: FeedbackEnvelope,
        approval: ExactTargetApproval | None = None,
    ) -> ProposalRecord:
        self._validate_feedback_binding(event)
        if event.feedback_id in {value.feedback_id for value in self.feedback} or (
            event.feedback_hash in {value.feedback_hash for value in self.feedback}
        ):
            raise LifecycleRejection(
                ReasonCode.REPLAY_DRIFT, "feedback replay identity was already recorded"
            )
        expected_sequence = len(self.feedback) + 1
        expected_previous = self.feedback[-1].feedback_hash if self.feedback else None
        if (
            event.sequence != expected_sequence
            or event.previous_feedback_hash != expected_previous
        ):
            raise LifecycleRejection(
                ReasonCode.REPLAY_DRIFT,
                "feedback must append to the current sequence and hash",
            )
        if self.state in TERMINAL_PROPOSAL_STATES:
            raise LifecycleRejection(
                ReasonCode.STALE_PROPOSAL,
                f"proposal is terminal in state {self.state}",
            )
        if event.decision == "ACCEPTED":
            if self.ambiguous:
                raise LifecycleRejection(
                    ReasonCode.REVIEW_ONLY,
                    "ambiguous candidates cannot become accepted",
                )
            if approval is None or not approval.matches(
                self.envelope, self.replay_key, self.relation
            ):
                raise LifecycleRejection(
                    ReasonCode.EXACT_TARGET_CONFIRMATION_REQUIRED,
                    "acceptance requires approval bound to the exact target",
                )
        elif approval is not None:
            raise ValueError("approval metadata requires ACCEPTED feedback")
        registered = reason_definition(event.reason_code)
        if registered.outcome != _feedback_outcome(event.decision):
            raise ValueError("feedback decision does not match its reason outcome")
        next_state = {
            "ACCEPTED": "ACCEPTED",
            "REVIEW_ONLY": "REVIEW_ONLY",
            "REJECTED": (
                "STALE" if _reason(event.reason_code) in _STALE_REASONS else "REJECTED"
            ),
        }[event.decision]
        return replace(
            self,
            state=next_state,
            feedback=(*self.feedback, event),
            approval=approval if event.decision == "ACCEPTED" else self.approval,
        )

    def replayed(self) -> ProposalRecord:
        rebuilt = ProposalRecord(
            envelope=self.envelope,
            replay_key=self.replay_key,
            initial_state=self.initial_state,
            state=self.initial_state,
            relation=self.relation,
            ambiguous=self.ambiguous,
        )
        for event in self.feedback:
            approval = self.approval if event.decision == "ACCEPTED" else None
            rebuilt = rebuilt.with_feedback(event, approval)
        return rebuilt

    def to_dict(self) -> dict[str, Any]:
        return {
            "envelope": self.envelope.to_dict(),
            "replay_key": self.replay_key,
            "initial_state": self.initial_state,
            "state": self.state,
            "relation": self.relation,
            "ambiguous": self.ambiguous,
            "suggestion_only": self.suggestion_only,
            "executable": self.executable,
            "feedback": [value.to_dict() for value in self.feedback],
            "approval": self.approval.to_dict() if self.approval is not None else None,
        }


@dataclass(frozen=True, slots=True)
class RuleAnalysisLifecycle:
    """Immutable aggregate for candidates, proposals, locks, and feedback."""

    candidates: tuple[CandidateRecord, ...] = ()
    proposals: tuple[ProposalRecord, ...] = ()
    locks: ManualLocks = ManualLocks()

    def __post_init__(self) -> None:
        if not isinstance(self.candidates, tuple) or not isinstance(
            self.proposals, tuple
        ):
            raise TypeError("candidates and proposals must be immutable tuples")
        if not isinstance(self.locks, ManualLocks):
            raise TypeError("locks must be ManualLocks")
        self._require_unique(
            (record.envelope.candidate_id for record in self.candidates),
            "candidate id",
        )
        self._require_unique(
            (record.envelope.candidate_hash for record in self.candidates),
            "candidate hash",
        )
        self._require_unique(
            (record.envelope.proposal_id for record in self.proposals),
            "proposal id",
        )
        self._require_unique(
            (record.envelope.proposal_hash for record in self.proposals),
            "proposal hash",
        )
        self._require_unique(
            (record.replay_key for record in self.proposals), "proposal replay key"
        )

    @staticmethod
    def _require_unique(values: Any, name: str) -> None:
        materialized = tuple(values)
        if len(materialized) != len(set(materialized)):
            raise LifecycleRejection(ReasonCode.DUPLICATE_PROPOSAL, f"duplicate {name}")

    def guard_target(self, field: str, relation: str | None = None) -> None:
        parsed_field = _identifier(field, "field")
        protected_reason = _protected_reason(parsed_field)
        if protected_reason is not None:
            raise LifecycleRejection(
                protected_reason, f"{parsed_field} is protected from proposal changes"
            )
        if self.locks.field_is_locked(parsed_field):
            raise LifecycleRejection(
                ReasonCode.MANUAL_LOCKED, f"manual field lock protects {parsed_field}"
            )
        if relation is not None and self.locks.relation_is_locked(relation):
            raise LifecycleRejection(
                ReasonCode.MANUAL_LOCK_CONFLICT,
                f"manual relation lock protects {_identifier(relation, 'relation')}",
            )
        policy = POLICY_A_EFFECT_MATRIX.get(parsed_field)
        if policy is None or policy.effect_kind == "FORBIDDEN":
            raise LifecycleRejection(
                ReasonCode.PROTECTED_FIELD_PROPOSAL,
                f"{parsed_field} is not a legal Policy A proposal target",
            )

    def with_field_locked(self, field: str) -> RuleAnalysisLifecycle:
        return replace(self, locks=self.locks.with_field_locked(field))

    def with_field_released(self, field: str) -> RuleAnalysisLifecycle:
        return replace(self, locks=self.locks.with_field_released(field))

    def with_relation_locked(self, relation: str) -> RuleAnalysisLifecycle:
        return replace(self, locks=self.locks.with_relation_locked(relation))

    def with_relation_released(self, relation: str) -> RuleAnalysisLifecycle:
        return replace(self, locks=self.locks.with_relation_released(relation))

    def register_candidate(self, envelope: CandidateEnvelope) -> RuleAnalysisLifecycle:
        registered = reason_definition(envelope.reason_code)
        ambiguous = registered.code.value in _AMBIGUOUS_REASONS
        state = {
            "accepted": "CANDIDATE",
            "review_only": "REVIEW_ONLY",
            "rejected": "REJECTED",
        }[registered.outcome]
        record = CandidateRecord(envelope=envelope, state=state, ambiguous=ambiguous)
        if any(
            existing.envelope.candidate_id == envelope.candidate_id
            or existing.envelope.candidate_hash == envelope.candidate_hash
            for existing in self.candidates
        ):
            raise LifecycleRejection(
                ReasonCode.DUPLICATE_PROPOSAL,
                "candidate identity was already registered",
            )
        return replace(self, candidates=(*self.candidates, record))

    def register_proposal(
        self,
        envelope: ProposalEnvelope,
        *,
        replay_key: str,
        relation: str | None = None,
    ) -> RuleAnalysisLifecycle:
        parsed_key = _identifier(replay_key, "replay_key")
        parsed_relation = (
            _identifier(relation, "relation") if relation is not None else None
        )
        if any(
            existing.envelope.proposal_id == envelope.proposal_id
            or existing.envelope.proposal_hash == envelope.proposal_hash
            or existing.replay_key == parsed_key
            for existing in self.proposals
        ):
            raise LifecycleRejection(
                ReasonCode.DUPLICATE_PROPOSAL,
                "proposal id, hash, or replay key was already registered",
            )
        candidate = self._candidate(envelope.candidate_id)
        self._validate_candidate_binding(candidate.envelope, envelope)
        if candidate.state == "REJECTED":
            raise LifecycleRejection(
                candidate.envelope.reason_code,
                "a rejected candidate cannot produce a proposal",
            )
        self.guard_target(envelope.field, parsed_relation)
        validate_protected_changes({envelope.field: envelope.value})
        validate_policy_a_target(
            envelope.field, envelope.existing_value, envelope.target_kind
        )
        if envelope.field not in candidate.envelope.target_fields:
            raise LifecycleRejection(
                ReasonCode.SCOPE_MISMATCH,
                "proposal field is outside the candidate target fields",
            )
        if envelope.field not in candidate.envelope.payload or canonical_sha256(
            candidate.envelope.payload[envelope.field]
        ) != canonical_sha256(envelope.value):
            raise LifecycleRejection(
                ReasonCode.EFFECT_DRIFT,
                "proposal value differs from the immutable candidate payload",
            )
        initial_state = (
            "REVIEW_ONLY" if candidate.state == "REVIEW_ONLY" else "PROPOSED"
        )
        record = ProposalRecord(
            envelope=envelope,
            replay_key=parsed_key,
            initial_state=initial_state,
            state=initial_state,
            relation=parsed_relation,
            ambiguous=candidate.ambiguous,
        )
        return replace(self, proposals=(*self.proposals, record))

    def record_feedback(
        self,
        proposal_id: str,
        event: FeedbackEnvelope,
        *,
        approval: ExactTargetApproval | None = None,
    ) -> RuleAnalysisLifecycle:
        index, record = self._proposal_with_index(proposal_id)
        if event.decision == "ACCEPTED":
            self.guard_target(record.envelope.field, record.relation)
            validate_policy_a_target(
                record.envelope.field,
                record.envelope.existing_value,
                record.envelope.target_kind,
            )
        updated = record.with_feedback(event, approval)
        proposals = self.proposals[:index] + (updated,) + self.proposals[index + 1 :]
        return replace(self, proposals=proposals)

    def accept_proposal(
        self,
        proposal_id: str,
        event: FeedbackEnvelope,
        approval: ExactTargetApproval,
    ) -> RuleAnalysisLifecycle:
        if event.decision != "ACCEPTED":
            raise ValueError("accept_proposal requires ACCEPTED feedback")
        return self.record_feedback(proposal_id, event, approval=approval)

    def reject_proposal(
        self, proposal_id: str, event: FeedbackEnvelope
    ) -> RuleAnalysisLifecycle:
        if event.decision != "REJECTED" or _reason(event.reason_code) in _STALE_REASONS:
            raise ValueError("reject_proposal requires non-stale REJECTED feedback")
        return self.record_feedback(proposal_id, event)

    def mark_proposal_stale(
        self, proposal_id: str, event: FeedbackEnvelope
    ) -> RuleAnalysisLifecycle:
        if (
            event.decision != "REJECTED"
            or _reason(event.reason_code) not in _STALE_REASONS
        ):
            raise ValueError("mark_proposal_stale requires a stale REJECTED reason")
        return self.record_feedback(proposal_id, event)

    def replay_feedback(self) -> RuleAnalysisLifecycle:
        replayed = tuple(record.replayed() for record in self.proposals)
        result = replace(self, proposals=replayed)
        if result.lifecycle_hash != self.lifecycle_hash:
            raise LifecycleRejection(
                ReasonCode.REPLAY_NONDETERMINISTIC,
                "append-only lifecycle replay changed canonical state",
            )
        return result

    @property
    def lifecycle_hash(self) -> str:
        return canonical_sha256(self.to_dict())

    def _candidate(self, candidate_id: str) -> CandidateRecord:
        parsed = _identifier(candidate_id, "candidate_id")
        for record in self.candidates:
            if record.envelope.candidate_id == parsed:
                return record
        raise LifecycleRejection(
            ReasonCode.MATCH_MISSING, f"candidate {parsed} is not registered"
        )

    def _proposal_with_index(self, proposal_id: str) -> tuple[int, ProposalRecord]:
        parsed = _identifier(proposal_id, "proposal_id")
        for index, record in enumerate(self.proposals):
            if record.envelope.proposal_id == parsed:
                return index, record
        raise LifecycleRejection(
            ReasonCode.MATCH_MISSING, f"proposal {parsed} is not registered"
        )

    @staticmethod
    def _validate_candidate_binding(
        candidate: CandidateEnvelope, proposal: ProposalEnvelope
    ) -> None:
        if (
            proposal.candidate_hash != candidate.candidate_hash
            or proposal.candidate_kind != candidate.candidate_kind
            or proposal.observation_id != candidate.observation_id
            or proposal.observation_hash != candidate.observation_hash
            or proposal.scope_hash != candidate.scope_hash
            or proposal.input_hash != candidate.input_hash
        ):
            raise LifecycleRejection(
                ReasonCode.HASH_MISMATCH,
                "proposal is not bound to the immutable candidate inputs",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [record.to_dict() for record in self.candidates],
            "proposals": [record.to_dict() for record in self.proposals],
            "locks": self.locks.to_dict(),
            "proposal_only": True,
            "external_effects": (),
        }


__all__ = (
    "CANDIDATE_STATES",
    "PROPOSAL_STATES",
    "CandidateRecord",
    "ExactTargetApproval",
    "LifecycleRejection",
    "ManualLocks",
    "ProposalRecord",
    "RuleAnalysisLifecycle",
)
