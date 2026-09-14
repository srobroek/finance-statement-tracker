"""Immutable proposal-only facade for deterministic rule analysis.

The workbench composes the closed schema, matcher, lifecycle, and replay gates.  It
accepts only already-redacted values and returns immutable results; it has no
ledger, provider, persistence, or executable-effect capability.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Final

from .rule_analysis_lifecycle import RuleAnalysisLifecycle
from .rule_analysis_matcher import MatcherPolicy, MatchResult, match_observations
from .rule_analysis_reasons import ReasonCode, reason_definition
from .rule_analysis_schema import (
    POLICY_A_EFFECT_MATRIX,
    SUGGESTION_ONLY_FIELDS,
    CandidateEnvelope,
    FeedbackEnvelope,
    LedgerObservation,
    ProposalEnvelope,
    canonical_json,
    canonical_sha256,
)
from .rule_analysis_validation import (
    FixedAgentArtifact,
    RuleAnalysisValidationError,
    ValidationReceipt,
    validate_candidate,
    validate_deterministic_double_run,
    validate_fixed_agent_artifact_replay,
    validate_proposal_batch,
)

_AGENT_TARGET_FIELDS: Final[frozenset[str]] = frozenset(SUGGESTION_ONLY_FIELDS)
_AMBIGUOUS_OUTCOMES: Final[frozenset[str]] = frozenset({"AMBIGUOUS"})

ObservationInput = LedgerObservation | Mapping[str, Any]
CandidateInput = CandidateEnvelope | Mapping[str, Any]
FeedbackInput = FeedbackEnvelope | Mapping[str, Any]
FixedArtifactInput = FixedAgentArtifact | Mapping[str, Any]
VerificationCandidateInput = (
    CandidateInput | FixedArtifactInput | Sequence[FixedArtifactInput]
)


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _observation(value: object) -> LedgerObservation:
    if isinstance(value, LedgerObservation):
        return value
    if isinstance(value, Mapping):
        return LedgerObservation.from_mapping(value)
    raise TypeError("observation must be a LedgerObservation or mapping")


def _candidate(value: object) -> CandidateEnvelope:
    if isinstance(value, CandidateEnvelope):
        return value
    if isinstance(value, Mapping):
        return CandidateEnvelope.from_mapping(value)
    raise TypeError("candidate must be a CandidateEnvelope or mapping")


def _feedback(value: object) -> FeedbackEnvelope:
    if isinstance(value, FeedbackEnvelope):
        return value
    if isinstance(value, Mapping):
        return FeedbackEnvelope.from_mapping(value)
    raise TypeError("feedback must contain FeedbackEnvelope values or mappings")


def _fixed_artifact(value: object) -> FixedAgentArtifact:
    if isinstance(value, FixedAgentArtifact):
        return value
    if isinstance(value, Mapping):
        return FixedAgentArtifact.from_mapping(value)
    raise TypeError("fixed artifact must be a FixedAgentArtifact or mapping")


def _corpus(
    value: ObservationInput | Iterable[ObservationInput],
) -> tuple[LedgerObservation, ...]:
    if isinstance(value, (LedgerObservation, Mapping)):
        materialized = (_observation(value),)
    else:
        if isinstance(value, (str, bytes, bytearray)):
            raise TypeError("corpus must contain observations")
        materialized = tuple(_observation(item) for item in value)
    if not materialized:
        raise ValueError("corpus must not be empty")
    ordered = tuple(
        sorted(
            materialized,
            key=lambda item: (item.observation_id, item.observation_hash or ""),
        )
    )
    identities = tuple(item.observation_id for item in ordered)
    if len(identities) != len(set(identities)):
        raise ValueError("corpus contains duplicate observation_id values")
    return ordered


def _agent_targets(candidate: CandidateEnvelope) -> None:
    if candidate.candidate_kind != "AGENT_POLICY":
        return
    illegal = set(candidate.target_fields) - _AGENT_TARGET_FIELDS
    if illegal:
        raise RuleAnalysisValidationError(
            ReasonCode.PROTECTED_FIELD_PROPOSAL,
            "agent candidates may only suggest unresolved channel/reward_bucket "
            f"targets, not: {', '.join(sorted(illegal))}",
        )


def _artifact_pair(
    value: object,
) -> tuple[CandidateEnvelope, FixedAgentArtifact | None, FixedAgentArtifact | None]:
    if isinstance(value, FixedAgentArtifact) or (
        isinstance(value, Mapping) and value.get("artifact_kind") is not None
    ):
        artifact = _fixed_artifact(value)
        replay = FixedAgentArtifact.from_mapping(artifact.to_dict())
        return artifact.candidate, artifact, replay
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray, Mapping)
    ):
        if len(value) != 2:
            raise ValueError("agent verification requires exactly two fixed artifacts")
        artifact, replay = (_fixed_artifact(item) for item in value)
        return artifact.candidate, artifact, replay
    return _candidate(value), None, None


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Exact immutable matcher result bound to workbench scope and input."""

    scope_hash: str
    observation_id: str
    observation_hash: str
    input_hash: str
    match: MatchResult
    query_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.input_hash != self.match.canonical_input_sha256:
            raise ValueError("query input_hash is not bound to the matcher result")
        object.__setattr__(
            self,
            "query_hash",
            canonical_sha256(self.to_dict(include_hash=False)),
        )

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "scope_hash": self.scope_hash,
            "observation_id": self.observation_id,
            "observation_hash": self.observation_hash,
            "input_hash": self.input_hash,
            "match": self.match.to_dict(),
        }
        if include_hash:
            value["query_hash"] = self.query_hash
        return value


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """Deterministic decision projection; ambiguous matches remain review-only."""

    query: QueryResult
    outcome: str
    relation: str | None
    reason_codes: tuple[str, ...]
    review_only: bool
    evaluation_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.review_only != (self.outcome in _AMBIGUOUS_OUTCOMES):
            raise ValueError("evaluation review_only state does not match its outcome")
        if self.review_only and self.relation is not None:
            raise ValueError("review-only evaluations cannot select a relation")
        object.__setattr__(
            self,
            "evaluation_hash",
            canonical_sha256(self.to_dict(include_hash=False)),
        )

    @property
    def scope_hash(self) -> str:
        return self.query.scope_hash

    @property
    def input_hash(self) -> str:
        return self.query.input_hash

    @property
    def observation_hash(self) -> str:
        return self.query.observation_hash

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "query": self.query.to_dict(),
            "outcome": self.outcome,
            "relation": self.relation,
            "reason_codes": self.reason_codes,
            "review_only": self.review_only,
        }
        if include_hash:
            value["evaluation_hash"] = self.evaluation_hash
        return value


@dataclass(frozen=True, slots=True)
class RuleAnalysisWorkbench:
    """Proposal-only orchestration over immutable, redacted ledger observations."""

    scope: Mapping[str, Any]
    matcher_policy: MatcherPolicy = field(default_factory=MatcherPolicy)
    lifecycle: RuleAnalysisLifecycle = field(default_factory=RuleAnalysisLifecycle)
    _scope_hash: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.scope, Mapping):
            raise TypeError("scope must be a mapping")
        if not isinstance(self.matcher_policy, MatcherPolicy):
            raise TypeError("matcher_policy must be a MatcherPolicy")
        if not isinstance(self.lifecycle, RuleAnalysisLifecycle):
            raise TypeError("lifecycle must be a RuleAnalysisLifecycle")
        normalized = json.loads(canonical_json(self.scope))
        if not isinstance(normalized, dict) or not normalized:
            raise ValueError("scope must be a non-empty JSON object")
        object.__setattr__(self, "scope", _freeze_json(normalized))
        object.__setattr__(self, "_scope_hash", canonical_sha256(normalized))

    @property
    def scope_hash(self) -> str:
        return self._scope_hash

    def query(
        self,
        observation: ObservationInput,
        corpus: ObservationInput | Iterable[ObservationInput],
    ) -> QueryResult:
        observed = _observation(observation)
        matched = match_observations(
            observed,
            _corpus(corpus),
            policy=self.matcher_policy,
        )
        return QueryResult(
            scope_hash=self.scope_hash,
            observation_id=observed.observation_id,
            observation_hash=observed.observation_hash or "",
            input_hash=matched.canonical_input_sha256,
            match=matched,
        )

    def evaluate(
        self,
        observation: ObservationInput,
        corpus: ObservationInput | Iterable[ObservationInput],
    ) -> EvaluationResult:
        queried = self.query(observation, corpus)
        if len(queried.match.traces) != 1:
            raise RuleAnalysisValidationError(
                ReasonCode.GROUP_TRACE_MISSING,
                "a single observation must produce exactly one matcher trace",
            )
        trace = queried.match.traces[0]
        return EvaluationResult(
            query=queried,
            outcome=trace.outcome,
            relation=trace.selected_edge,
            reason_codes=trace.reason_codes,
            review_only=trace.outcome in _AMBIGUOUS_OUTCOMES,
        )

    def propose(
        self,
        candidate: CandidateInput,
        observation: ObservationInput,
        *,
        feedback: Sequence[FeedbackInput] = (),
        relation: str | None = None,
    ) -> ProposalResult:
        parsed_candidate = _candidate(candidate)
        observed = _observation(observation)
        _agent_targets(parsed_candidate)
        candidate_receipt = validate_candidate(
            parsed_candidate,
            observed,
            expected_scope_hash=self.scope_hash,
            expected_input_hash=parsed_candidate.input_hash,
            require_provenance=True,
        )

        observation_values = observed.to_dict()
        proposals = tuple(
            ProposalEnvelope(
                proposal_id="proposal-"
                + canonical_sha256(
                    {
                        "candidate_hash": parsed_candidate.candidate_hash,
                        "field": target,
                    }
                ),
                candidate_id=parsed_candidate.candidate_id,
                candidate_hash=parsed_candidate.candidate_hash or "",
                candidate_kind=parsed_candidate.candidate_kind,
                observation_id=parsed_candidate.observation_id,
                observation_hash=parsed_candidate.observation_hash,
                scope_hash=parsed_candidate.scope_hash,
                input_hash=parsed_candidate.input_hash,
                field=target,
                target_kind=(
                    "SUGGESTION_ONLY"
                    if POLICY_A_EFFECT_MATRIX[target].suggestion_only
                    else "DETERMINISTIC_WRITABLE"
                ),
                value=parsed_candidate.payload[target],
                existing_value=observation_values.get(target),
                reason_code=parsed_candidate.reason_code,
                confidence=parsed_candidate.confidence,
                evidence_refs=parsed_candidate.evidence_refs,
            )
            for target in parsed_candidate.target_fields
        )
        proposal_receipt = validate_proposal_batch(
            proposals,
            parsed_candidate,
            observed,
            expected_scope_hash=self.scope_hash,
            expected_input_hash=parsed_candidate.input_hash,
            require_provenance=True,
        )

        lifecycle = self.lifecycle
        existing_candidate = next(
            (
                record
                for record in lifecycle.candidates
                if record.envelope.candidate_id == parsed_candidate.candidate_id
            ),
            None,
        )
        if existing_candidate is None:
            lifecycle = lifecycle.register_candidate(parsed_candidate)
        elif canonical_json(existing_candidate.envelope) != canonical_json(
            parsed_candidate
        ):
            raise RuleAnalysisValidationError(
                ReasonCode.REPLAY_DRIFT,
                "candidate id was previously registered with different immutable facts",
            )

        replay_keys: dict[str, str] = {}
        for proposal in proposals:
            replay_key = canonical_sha256(
                {
                    "proposal_hash": proposal.proposal_hash,
                    "relation": relation,
                }
            )
            replay_keys[proposal.proposal_id] = replay_key
            existing_proposal = next(
                (
                    record
                    for record in lifecycle.proposals
                    if record.envelope.proposal_id == proposal.proposal_id
                ),
                None,
            )
            if existing_proposal is None:
                lifecycle = lifecycle.register_proposal(
                    proposal,
                    replay_key=replay_key,
                    relation=relation,
                )
            elif (
                canonical_json(existing_proposal.envelope) != canonical_json(proposal)
                or existing_proposal.replay_key != replay_key
                or existing_proposal.relation != relation
            ):
                raise RuleAnalysisValidationError(
                    ReasonCode.REPLAY_DRIFT,
                    "proposal id was previously registered with different immutable facts",
                )

        proposal_ids = frozenset(replay_keys)
        for item in feedback:
            event = _feedback(item)
            if event.proposal_id not in proposal_ids:
                raise RuleAnalysisValidationError(
                    ReasonCode.SCOPE_MISMATCH,
                    "feedback targets a proposal outside this candidate",
                )
            lifecycle = lifecycle.record_feedback(event.proposal_id, event)
        lifecycle = lifecycle.replay_feedback()
        updated = replace(self, lifecycle=lifecycle)
        return ProposalResult(
            workbench=updated,
            candidate=parsed_candidate,
            observation=observed,
            proposals=proposals,
            candidate_receipt=candidate_receipt,
            proposal_receipt=proposal_receipt,
        )

    def verify(
        self,
        candidate: VerificationCandidateInput,
        corpus: ObservationInput | Iterable[ObservationInput],
    ) -> VerificationResult:
        parsed_candidate, artifact, replay = _artifact_pair(candidate)
        _agent_targets(parsed_candidate)
        corpus_values = _corpus(corpus)
        matches = tuple(
            item
            for item in corpus_values
            if item.observation_id == parsed_candidate.observation_id
        )
        if len(matches) != 1:
            raise RuleAnalysisValidationError(
                ReasonCode.HASH_MISMATCH,
                "candidate observation is not present exactly once in the corpus",
            )
        observed = matches[0]
        evaluation = self.evaluate(observed, corpus_values)
        candidate_receipt = validate_candidate(
            parsed_candidate,
            observed,
            expected_scope_hash=self.scope_hash,
            expected_input_hash=evaluation.input_hash,
            require_provenance=True,
        )
        if (
            evaluation.review_only
            and reason_definition(parsed_candidate.reason_code).outcome != "review_only"
        ):
            raise RuleAnalysisValidationError(
                ReasonCode.REVIEW_ONLY,
                "an ambiguous matcher outcome requires a review-only candidate",
            )
        if (
            parsed_candidate.candidate_kind == "DETERMINISTIC_RULE"
            and parsed_candidate.reason_code not in evaluation.reason_codes
        ):
            raise RuleAnalysisValidationError(
                ReasonCode.EFFECT_DRIFT,
                "deterministic candidate reason is not bound to the matcher trace",
            )

        corpus_payload = tuple(item.to_dict() for item in corpus_values)
        expected_output_hash = canonical_sha256(evaluation.to_dict())

        def replay_runner(replay_corpus: Any) -> dict[str, Any]:
            return self.evaluate(observed, replay_corpus).to_dict()

        replay_receipt = validate_deterministic_double_run(
            corpus_payload,
            replay_runner,
            expected_scope_hash=self.scope_hash,
            expected_input_hash=canonical_sha256(corpus_payload),
            expected_output_hash=expected_output_hash,
        )
        artifact_receipt: ValidationReceipt | None = None
        if parsed_candidate.candidate_kind == "AGENT_POLICY":
            if artifact is None or replay is None:
                raise RuleAnalysisValidationError(
                    ReasonCode.MISSING_EVIDENCE,
                    "agent verification requires fixed artifact provenance",
                )
            artifact_receipt = validate_fixed_agent_artifact_replay(
                artifact,
                replay,
                observed,
                expected_scope_hash=self.scope_hash,
                expected_input_hash=evaluation.input_hash,
                expected_agent_id=artifact.provenance.agent_id,
                expected_policy_id=artifact.provenance.policy_id,
            )
        elif artifact is not None:
            raise RuleAnalysisValidationError(
                ReasonCode.SCHEMA_INVALID,
                "fixed artifacts may only contain AGENT_POLICY candidates",
            )
        return VerificationResult(
            candidate=parsed_candidate,
            observation=observed,
            evaluation=evaluation,
            corpus_hash=canonical_sha256(corpus_payload),
            candidate_receipt=candidate_receipt,
            replay_receipt=replay_receipt,
            artifact_receipt=artifact_receipt,
        )


@dataclass(frozen=True, slots=True)
class ProposalResult:
    """Validated proposal batch and its immutable next lifecycle state."""

    workbench: RuleAnalysisWorkbench
    candidate: CandidateEnvelope
    observation: LedgerObservation
    proposals: tuple[ProposalEnvelope, ...]
    candidate_receipt: ValidationReceipt
    proposal_receipt: ValidationReceipt

    @property
    def lifecycle(self) -> RuleAnalysisLifecycle:
        return self.workbench.lifecycle

    @property
    def lifecycle_hash(self) -> str:
        return self.lifecycle.lifecycle_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_hash": self.workbench.scope_hash,
            "candidate_hash": self.candidate.candidate_hash,
            "observation_hash": self.observation.observation_hash,
            "proposals": tuple(proposal.to_dict() for proposal in self.proposals),
            "candidate_receipt": self.candidate_receipt.to_dict(),
            "proposal_receipt": self.proposal_receipt.to_dict(),
            "lifecycle": self.lifecycle.to_dict(),
            "lifecycle_hash": self.lifecycle_hash,
            "proposal_only": True,
        }


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Fail-closed candidate, corpus, matcher, and replay proof."""

    candidate: CandidateEnvelope
    observation: LedgerObservation
    evaluation: EvaluationResult
    corpus_hash: str
    candidate_receipt: ValidationReceipt
    replay_receipt: ValidationReceipt
    artifact_receipt: ValidationReceipt | None = None

    @property
    def scope_hash(self) -> str:
        return self.evaluation.scope_hash

    @property
    def input_hash(self) -> str:
        return self.evaluation.input_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_hash": self.scope_hash,
            "input_hash": self.input_hash,
            "corpus_hash": self.corpus_hash,
            "observation_hash": self.observation.observation_hash,
            "candidate_hash": self.candidate.candidate_hash,
            "evaluation": self.evaluation.to_dict(),
            "candidate_receipt": self.candidate_receipt.to_dict(),
            "replay_receipt": self.replay_receipt.to_dict(),
            "artifact_receipt": (
                self.artifact_receipt.to_dict()
                if self.artifact_receipt is not None
                else None
            ),
            "verified": True,
            "proposal_only": True,
        }


__all__ = (
    "EvaluationResult",
    "ProposalResult",
    "QueryResult",
    "RuleAnalysisWorkbench",
    "VerificationResult",
)
