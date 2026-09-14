"""Fail-closed, side-effect-free validation for RuleAnalysisWorkbench artifacts."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Never

from .rule_analysis_reasons import (
    ReasonCode,
    UnknownReasonCode,
    reason_code,
    reason_definition,
)
from .rule_analysis_schema import (
    CANDIDATE_KINDS,
    DETERMINISTIC_WRITABLE_FIELDS,
    FORBIDDEN_SIDE_EFFECT_CAPABILITIES,
    POLICY_A_EFFECT_MATRIX,
    PROTECTED_ACTUAL_STRUCTURAL_FIELDS,
    PROTECTED_ECONOMIC_FIELDS,
    PROTECTED_IDENTITY_FIELDS,
    SCHEMA_VERSION,
    SUGGESTION_ONLY_FIELDS,
    TARGET_KINDS,
    CandidateEnvelope,
    EffectEnvelope,
    LedgerObservation,
    ProposalEnvelope,
    canonical_json,
    canonical_sha256,
)

_HASH_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_PROJECTION_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "contract_status",
        "authoring_source",
        "candidate_kinds",
        "target_kinds",
        "target_fields",
        "deterministic_writable_fields",
        "suggestion_only_fields",
        "protected_proposal_fields",
        "protected_identity_fields",
        "protected_economic_fields",
        "protected_actual_structural_fields",
        "direct_commit_forbidden",
        "forbidden_side_effect_capabilities",
        "effect_matrix",
    }
)
_EFFECT_POLICY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "effect_kind",
        "writable",
        "suggestion_only",
        "unresolved_only",
        "direct_commit_allowed",
        "validator_required",
        "protection_class",
    }
)
_ARTIFACT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "artifact_id",
        "artifact_hash",
        "scope_hash",
        "input_hash",
        "observation_id",
        "observation_hash",
        "candidate",
        "provenance",
        "proposal_only",
    }
)
_PROVENANCE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "agent_id",
        "agent_version",
        "policy_id",
        "source_artifact_id",
        "source_artifact_sha256",
    }
)


class RuleAnalysisValidationError(ValueError):
    """A fail-closed validation result carrying a registered reason code."""

    def __init__(self, code: str | ReasonCode, detail: str) -> None:
        self.reason_code = reason_code(code)
        self.detail = detail
        super().__init__(f"{self.reason_code.value}: {detail}")


@dataclass(frozen=True, slots=True)
class ValidationReceipt:
    """Immutable proof emitted only after a validation gate succeeds."""

    gate: str
    output_hash: str
    item_count: int
    scope_hash: str | None = None
    input_hash: str | None = None
    schema_version: int = SCHEMA_VERSION
    deterministic: bool = True
    proposal_only: bool = True

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported ValidationReceipt schema_version")
        _identifier(self.gate, "gate")
        _validated_hash(self.output_hash, "output_hash")
        if self.scope_hash is not None:
            _validated_hash(self.scope_hash, "scope_hash")
        if self.input_hash is not None:
            _validated_hash(self.input_hash, "input_hash")
        if isinstance(self.item_count, bool) or self.item_count < 0:
            raise ValueError("item_count must be a non-negative integer")
        if self.deterministic is not True or self.proposal_only is not True:
            raise ValueError(
                "validation receipts must be deterministic and proposal-only"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "gate": self.gate,
            "scope_hash": self.scope_hash,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "item_count": self.item_count,
            "deterministic": self.deterministic,
            "proposal_only": self.proposal_only,
        }


@dataclass(frozen=True, slots=True)
class AgentArtifactProvenance:
    """Closed provenance binding for one already-captured agent artifact."""

    agent_id: str
    agent_version: str
    policy_id: str
    source_artifact_id: str
    source_artifact_sha256: str

    def __post_init__(self) -> None:
        for name in ("agent_id", "agent_version", "policy_id", "source_artifact_id"):
            _identifier(getattr(self, name), name)
        _validated_hash(self.source_artifact_sha256, "source_artifact_sha256")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> AgentArtifactProvenance:
        data = _strict_object(value, _PROVENANCE_FIELDS, "provenance")
        missing = _PROVENANCE_FIELDS - set(data)
        if missing:
            _fail(
                ReasonCode.MISSING_EVIDENCE,
                f"provenance is missing fields: {', '.join(sorted(missing))}",
            )
        return cls(**dict(data))

    def to_dict(self) -> dict[str, str]:
        return {
            "agent_id": self.agent_id,
            "agent_version": self.agent_version,
            "policy_id": self.policy_id,
            "source_artifact_id": self.source_artifact_id,
            "source_artifact_sha256": self.source_artifact_sha256,
        }


@dataclass(frozen=True, slots=True)
class FixedAgentArtifact:
    """Immutable replay input; it contains captured output, never an agent capability."""

    artifact_id: str
    scope_hash: str
    input_hash: str
    observation_id: str
    observation_hash: str
    candidate: CandidateEnvelope
    provenance: AgentArtifactProvenance
    artifact_hash: str | None = None
    schema_version: int = SCHEMA_VERSION
    artifact_kind: str = "FIXED_AGENT_CANDIDATE"
    proposal_only: bool = True

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            _fail(ReasonCode.SCHEMA_DRIFT, "unsupported fixed artifact schema_version")
        if self.artifact_kind != "FIXED_AGENT_CANDIDATE":
            _fail(ReasonCode.SCHEMA_DRIFT, "illegal fixed artifact kind")
        _identifier(self.artifact_id, "artifact_id")
        _validated_hash(self.scope_hash, "scope_hash")
        _validated_hash(self.input_hash, "input_hash")
        _identifier(self.observation_id, "observation_id")
        _validated_hash(self.observation_hash, "observation_hash")
        if not isinstance(self.candidate, CandidateEnvelope):
            _fail(ReasonCode.SCHEMA_INVALID, "candidate must be a CandidateEnvelope")
        if self.candidate.candidate_kind != "AGENT_POLICY":
            _fail(
                ReasonCode.SCHEMA_INVALID,
                "fixed artifacts require AGENT_POLICY candidates",
            )
        if not isinstance(self.provenance, AgentArtifactProvenance):
            _fail(ReasonCode.MISSING_EVIDENCE, "fixed artifact provenance is required")
        if self.proposal_only is not True:
            _fail(
                ReasonCode.PROTECTED_FIELD_PROPOSAL,
                "fixed artifact is not proposal-only",
            )
        expected = canonical_sha256(self._without_hash())
        if self.artifact_hash is not None:
            _expect_hash(self.artifact_hash, expected, "artifact_hash")
        object.__setattr__(self, "artifact_hash", expected)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> FixedAgentArtifact:
        data = _strict_object(value, _ARTIFACT_FIELDS, "FixedAgentArtifact")
        if "provenance" not in data:
            _fail(ReasonCode.MISSING_EVIDENCE, "fixed artifact provenance is missing")
        _require_fields(data, _ARTIFACT_FIELDS, "FixedAgentArtifact")
        try:
            candidate = _candidate(data["candidate"])
            provenance = AgentArtifactProvenance.from_mapping(data["provenance"])
            return cls(
                artifact_id=data["artifact_id"],
                scope_hash=data["scope_hash"],
                input_hash=data["input_hash"],
                observation_id=data["observation_id"],
                observation_hash=data["observation_hash"],
                candidate=candidate,
                provenance=provenance,
                artifact_hash=data["artifact_hash"],
                schema_version=data["schema_version"],
                artifact_kind=data["artifact_kind"],
                proposal_only=data["proposal_only"],
            )
        except RuleAnalysisValidationError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            _raise_schema_error(exc)

    def _without_hash(self) -> dict[str, Any]:
        data = self.to_dict()
        data.pop("artifact_hash")
        return data

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "artifact_kind": self.artifact_kind,
            "artifact_id": self.artifact_id,
            "artifact_hash": self.artifact_hash,
            "scope_hash": self.scope_hash,
            "input_hash": self.input_hash,
            "observation_id": self.observation_id,
            "observation_hash": self.observation_hash,
            "candidate": self.candidate.to_dict(),
            "provenance": self.provenance.to_dict(),
            "proposal_only": self.proposal_only,
        }


def _fail(code: str | ReasonCode, detail: str) -> Never:
    raise RuleAnalysisValidationError(code, detail)


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(ReasonCode.SCHEMA_INVALID, f"{name} must be a non-empty string")
    return value.strip()


def _validated_hash(value: Any, name: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        _fail(ReasonCode.HASH_INVALID, f"{name} must be a lowercase SHA-256 digest")
    return value


def _expect_hash(actual: Any, expected: Any, name: str, *, scope: bool = False) -> None:
    left = _validated_hash(actual, name)
    right = _validated_hash(expected, f"expected_{name}")
    if left != right:
        _fail(
            ReasonCode.SCOPE_MISMATCH if scope else ReasonCode.HASH_MISMATCH,
            f"{name} does not match its bound value",
        )


def _strict_object(
    value: Any, allowed: frozenset[str], context: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(ReasonCode.SCHEMA_INVALID, f"{context} must be an object")
    unknown = set(value) - allowed
    if unknown:
        _fail(
            ReasonCode.SCHEMA_DRIFT,
            f"{context} contains unknown fields: {', '.join(sorted(unknown))}",
        )
    return value


def _require_fields(
    value: Mapping[str, Any], required: frozenset[str], context: str
) -> None:
    missing = required - set(value)
    if missing:
        _fail(
            ReasonCode.SCHEMA_DRIFT,
            f"{context} is missing fields: {', '.join(sorted(missing))}",
        )


def _strict_sequence(value: Any, context: str) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        _fail(ReasonCode.SCHEMA_DRIFT, f"{context} must be an array")
    return tuple(value)


def _raise_schema_error(exc: BaseException) -> Never:
    text = str(exc)
    lowered = text.lower()
    if isinstance(exc, UnknownReasonCode):
        _fail(ReasonCode.UNKNOWN_REASON, text or "unknown reason code")
    if "hash does not match" in lowered:
        _fail(ReasonCode.HASH_MISMATCH, text)
    if "hash" in lowered and ("sha-256" in lowered or "64" in lowered):
        _fail(ReasonCode.HASH_INVALID, text)
    if "protected" in lowered:
        for field in PROTECTED_ECONOMIC_FIELDS:
            if field in text:
                _fail(ReasonCode.PROTECTED_ECONOMIC_FIELD, text)
        for field in PROTECTED_ACTUAL_STRUCTURAL_FIELDS:
            if field in text:
                _fail(ReasonCode.PROTECTED_ACTUAL_FIELD, text)
        _fail(ReasonCode.PROTECTED_IDENTITY_FIELD, text)
    if "unknown field" in lowered or "unsupported" in lowered:
        _fail(ReasonCode.SCHEMA_DRIFT, text)
    _fail(ReasonCode.SCHEMA_INVALID, text or type(exc).__name__)


def _known_reason(value: Any) -> None:
    try:
        reason_code(value)
    except (TypeError, UnknownReasonCode) as exc:
        _fail(ReasonCode.UNKNOWN_REASON, str(exc) or repr(value))


def _validate_target_field(value: Any, context: str) -> str:
    field = _identifier(value, context)
    policy = POLICY_A_EFFECT_MATRIX.get(field)
    if policy is None:
        _fail(ReasonCode.EFFECT_POLICY_DRIFT, f"{context} is not present in Policy A")
    if policy.effect_kind == "FORBIDDEN":
        protection_reasons = {
            "IDENTITY": ReasonCode.PROTECTED_IDENTITY_FIELD,
            "ECONOMIC": ReasonCode.PROTECTED_ECONOMIC_FIELD,
            "ACTUAL_STRUCTURAL": ReasonCode.PROTECTED_ACTUAL_FIELD,
        }
        _fail(
            protection_reasons.get(
                policy.protection_class, ReasonCode.PROTECTED_FIELD_PROPOSAL
            ),
            f"{context} targets protected field {field}",
        )
    return field


def _observation(value: LedgerObservation | Mapping[str, Any]) -> LedgerObservation:
    if isinstance(value, LedgerObservation):
        return value
    try:
        return LedgerObservation.from_mapping(value)
    except RuleAnalysisValidationError:
        raise
    except (TypeError, ValueError) as exc:
        _raise_schema_error(exc)


def _candidate(value: CandidateEnvelope | Mapping[str, Any]) -> CandidateEnvelope:
    if isinstance(value, CandidateEnvelope):
        _known_reason(value.reason_code)
        return value
    if isinstance(value, Mapping) and "reason_code" in value:
        _known_reason(value["reason_code"])
    if isinstance(value, Mapping) and "target_fields" in value:
        for field in _strict_sequence(value["target_fields"], "target_fields"):
            _validate_target_field(field, "candidate field")
    try:
        parsed = CandidateEnvelope.from_mapping(value)
        _known_reason(parsed.reason_code)
        return parsed
    except RuleAnalysisValidationError:
        raise
    except (TypeError, ValueError) as exc:
        _raise_schema_error(exc)


def _proposal(value: ProposalEnvelope | Mapping[str, Any]) -> ProposalEnvelope:
    if isinstance(value, ProposalEnvelope):
        _known_reason(value.reason_code)
        return value
    if isinstance(value, Mapping) and "reason_code" in value:
        _known_reason(value["reason_code"])
    if isinstance(value, Mapping) and "field" in value:
        _validate_target_field(value["field"], "proposal field")
    try:
        parsed = ProposalEnvelope.from_mapping(value)
        _known_reason(parsed.reason_code)
        return parsed
    except RuleAnalysisValidationError:
        raise
    except (TypeError, ValueError) as exc:
        _raise_schema_error(exc)


def _effect(value: EffectEnvelope | Mapping[str, Any]) -> EffectEnvelope:
    if isinstance(value, EffectEnvelope):
        return value
    if isinstance(value, Mapping) and "field" in value:
        _validate_target_field(value["field"], "effect field")
    try:
        return EffectEnvelope.from_mapping(value)
    except RuleAnalysisValidationError:
        raise
    except (TypeError, ValueError) as exc:
        _raise_schema_error(exc)


def _same(left: Any, right: Any) -> bool:
    try:
        return canonical_json(left) == canonical_json(right)
    except (TypeError, ValueError) as exc:
        _raise_schema_error(exc)


def _receipt(
    gate: str,
    value: Any,
    count: int,
    *,
    scope_hash: str | None = None,
    input_hash: str | None = None,
) -> ValidationReceipt:
    output_hash = canonical_sha256(value)
    return ValidationReceipt(
        gate=gate,
        scope_hash=scope_hash,
        input_hash=input_hash,
        output_hash=output_hash,
        item_count=count,
    )


def lint_effect_projection(projection: Mapping[str, Any]) -> ValidationReceipt:
    """Prove the SPEC_ONLY cross-runtime projection exactly matches Policy A."""

    data = _strict_object(projection, _PROJECTION_FIELDS, "effect projection")
    _require_fields(data, _PROJECTION_FIELDS, "effect projection")
    scalar_contract = {
        "schema_version": SCHEMA_VERSION,
        "contract_status": "SPEC_ONLY",
        "authoring_source": "config/ai-policies.json",
        "direct_commit_forbidden": True,
    }
    for name, expected in scalar_contract.items():
        if data[name] != expected:
            _fail(ReasonCode.SCHEMA_DRIFT, f"effect projection {name} drifted")

    ordered_contracts = {
        "candidate_kinds": CANDIDATE_KINDS,
        "target_kinds": TARGET_KINDS,
        "deterministic_writable_fields": DETERMINISTIC_WRITABLE_FIELDS,
        "suggestion_only_fields": SUGGESTION_ONLY_FIELDS,
        "protected_proposal_fields": SUGGESTION_ONLY_FIELDS,
        "protected_identity_fields": PROTECTED_IDENTITY_FIELDS,
        "protected_economic_fields": PROTECTED_ECONOMIC_FIELDS,
        "protected_actual_structural_fields": PROTECTED_ACTUAL_STRUCTURAL_FIELDS,
        "forbidden_side_effect_capabilities": FORBIDDEN_SIDE_EFFECT_CAPABILITIES,
    }
    for name, expected in ordered_contracts.items():
        if _strict_sequence(data[name], name) != tuple(expected):
            _fail(ReasonCode.SCHEMA_DRIFT, f"effect projection {name} drifted")

    target_fields = _strict_sequence(data["target_fields"], "target_fields")
    expected_targets = set(DETERMINISTIC_WRITABLE_FIELDS) | set(SUGGESTION_ONLY_FIELDS)
    if (
        len(target_fields) != len(set(target_fields))
        or set(target_fields) != expected_targets
    ):
        _fail(ReasonCode.SCHEMA_DRIFT, "effect projection target_fields drifted")

    matrix = _strict_object(
        data["effect_matrix"], frozenset(POLICY_A_EFFECT_MATRIX), "effect_matrix"
    )
    _require_fields(matrix, frozenset(POLICY_A_EFFECT_MATRIX), "effect_matrix")
    for field, policy in POLICY_A_EFFECT_MATRIX.items():
        record = _strict_object(
            matrix[field], _EFFECT_POLICY_FIELDS, f"effect_matrix.{field}"
        )
        _require_fields(record, _EFFECT_POLICY_FIELDS, f"effect_matrix.{field}")
        expected_record = {
            name: getattr(policy, name) for name in _EFFECT_POLICY_FIELDS
        }
        if not _same(record, expected_record):
            _fail(ReasonCode.EFFECT_POLICY_DRIFT, f"effect policy drifted for {field}")
    return _receipt("effect_projection", data, len(matrix))


def validate_candidate(
    candidate: CandidateEnvelope | Mapping[str, Any],
    observation: LedgerObservation | Mapping[str, Any],
    *,
    expected_scope_hash: str,
    expected_input_hash: str,
    require_provenance: bool = True,
) -> ValidationReceipt:
    """Validate a candidate's closed schema, bindings, locks, and provenance."""

    observed = _observation(observation)
    parsed = _candidate(candidate)
    if parsed.observation_id != observed.observation_id:
        _fail(ReasonCode.HASH_MISMATCH, "candidate observation_id is not bound")
    _expect_hash(parsed.observation_hash, observed.observation_hash, "observation_hash")
    _expect_hash(parsed.scope_hash, expected_scope_hash, "scope_hash", scope=True)
    _expect_hash(parsed.input_hash, expected_input_hash, "input_hash")
    locked = set(parsed.target_fields) & set(observed.manual_locked_fields)
    if locked:
        _fail(
            ReasonCode.MANUAL_LOCK_CONFLICT,
            f"candidate targets manually locked fields: {', '.join(sorted(locked))}",
        )
    if require_provenance and not parsed.evidence_refs:
        _fail(ReasonCode.MISSING_EVIDENCE, "candidate provenance is missing")
    return _receipt(
        "candidate",
        parsed.to_dict(),
        1,
        scope_hash=parsed.scope_hash,
        input_hash=parsed.input_hash,
    )


def validate_proposal(
    proposal: ProposalEnvelope | Mapping[str, Any],
    candidate: CandidateEnvelope | Mapping[str, Any],
    observation: LedgerObservation | Mapping[str, Any],
    *,
    expected_scope_hash: str,
    expected_input_hash: str,
    require_provenance: bool = True,
) -> ValidationReceipt:
    """Validate one proposal as an exact, immutable derivation of its candidate."""

    observed = _observation(observation)
    parsed_candidate = _candidate(candidate)
    validate_candidate(
        parsed_candidate,
        observed,
        expected_scope_hash=expected_scope_hash,
        expected_input_hash=expected_input_hash,
        require_provenance=require_provenance,
    )
    parsed = _proposal(proposal)
    if (
        parsed.candidate_id != parsed_candidate.candidate_id
        or parsed.candidate_hash != parsed_candidate.candidate_hash
    ):
        _fail(ReasonCode.HASH_MISMATCH, "proposal is not bound to its candidate")
    if parsed.observation_id != observed.observation_id:
        _fail(ReasonCode.HASH_MISMATCH, "proposal observation_id is not bound")
    _expect_hash(parsed.observation_hash, observed.observation_hash, "observation_hash")
    _expect_hash(parsed.scope_hash, expected_scope_hash, "scope_hash", scope=True)
    _expect_hash(parsed.input_hash, expected_input_hash, "input_hash")
    if parsed.candidate_kind != parsed_candidate.candidate_kind:
        _fail(ReasonCode.SCHEMA_DRIFT, "proposal candidate_kind drifted")
    if parsed.field not in parsed_candidate.target_fields:
        _fail(
            ReasonCode.EFFECT_DRIFT, "proposal field was not targeted by its candidate"
        )
    if parsed.field not in parsed_candidate.payload or not _same(
        parsed.value, parsed_candidate.payload[parsed.field]
    ):
        _fail(ReasonCode.EFFECT_DRIFT, "proposal value differs from candidate payload")
    if parsed.field in observed.manual_locked_fields:
        _fail(ReasonCode.MANUAL_LOCK_CONFLICT, f"{parsed.field} is manually locked")
    observation_values = observed.to_dict()
    if parsed.field in observation_values and not _same(
        parsed.existing_value, observation_values[parsed.field]
    ):
        _fail(ReasonCode.STALE_BASE, f"existing {parsed.field} value drifted")
    if require_provenance:
        if not parsed.evidence_refs:
            _fail(ReasonCode.MISSING_EVIDENCE, "proposal provenance is missing")
        if not set(parsed.evidence_refs).issubset(parsed_candidate.evidence_refs):
            _fail(
                ReasonCode.EVIDENCE_VALIDATION_FAILED, "proposal provenance is unbound"
            )
    return _receipt(
        "proposal",
        parsed.to_dict(),
        1,
        scope_hash=parsed.scope_hash,
        input_hash=parsed.input_hash,
    )


def validate_proposal_batch(
    proposals: Sequence[ProposalEnvelope | Mapping[str, Any]],
    candidate: CandidateEnvelope | Mapping[str, Any],
    observation: LedgerObservation | Mapping[str, Any],
    *,
    expected_scope_hash: str,
    expected_input_hash: str,
    require_provenance: bool = True,
) -> ValidationReceipt:
    """Validate a canonical proposal batch and reject ambiguous field outcomes."""

    if isinstance(proposals, (str, bytes, bytearray)) or not isinstance(
        proposals, Sequence
    ):
        _fail(ReasonCode.SCHEMA_INVALID, "proposals must be a sequence")
    parsed = tuple(_proposal(item) for item in proposals)
    identities: set[tuple[str, str]] = set()
    for item in parsed:
        validate_proposal(
            item,
            candidate,
            observation,
            expected_scope_hash=expected_scope_hash,
            expected_input_hash=expected_input_hash,
            require_provenance=require_provenance,
        )
        identity = (item.observation_id, item.field)
        if identity in identities:
            _fail(ReasonCode.MATCH_AMBIGUOUS, f"multiple proposals target {item.field}")
        identities.add(identity)
    ordered = tuple(sorted(parsed, key=lambda item: (item.field, item.proposal_id)))
    return _receipt(
        "proposal_batch",
        [item.to_dict() for item in ordered],
        len(ordered),
        scope_hash=expected_scope_hash,
        input_hash=expected_input_hash,
    )


def validate_effect(
    effect: EffectEnvelope | Mapping[str, Any],
    proposal: ProposalEnvelope | Mapping[str, Any],
    candidate: CandidateEnvelope | Mapping[str, Any],
    observation: LedgerObservation | Mapping[str, Any],
    *,
    expected_scope_hash: str,
    expected_input_hash: str,
    require_provenance: bool = True,
) -> ValidationReceipt:
    """Gate a descriptor against its proposal without exposing a commit capability."""

    observed = _observation(observation)
    parsed_proposal = _proposal(proposal)
    validate_proposal(
        parsed_proposal,
        candidate,
        observed,
        expected_scope_hash=expected_scope_hash,
        expected_input_hash=expected_input_hash,
        require_provenance=require_provenance,
    )
    definition = reason_definition(parsed_proposal.reason_code)
    if definition.outcome != "accepted":
        _fail(
            definition.code, "review-only or rejected proposals cannot produce effects"
        )
    parsed = _effect(effect)
    if (
        parsed.proposal_id != parsed_proposal.proposal_id
        or parsed.proposal_hash != parsed_proposal.proposal_hash
    ):
        _fail(ReasonCode.HASH_MISMATCH, "effect is not bound to its proposal")
    if (
        parsed.observation_id != observed.observation_id
        or parsed.observation_hash != observed.observation_hash
    ):
        _fail(ReasonCode.HASH_MISMATCH, "effect is not bound to its observation")
    if (
        parsed.field != parsed_proposal.field
        or parsed.target_kind != parsed_proposal.target_kind
        or not _same(parsed.value, parsed_proposal.value)
    ):
        _fail(ReasonCode.EFFECT_DRIFT, "effect differs from its proposal")
    if parsed.field in observed.manual_locked_fields:
        _fail(ReasonCode.MANUAL_LOCK_CONFLICT, f"{parsed.field} is manually locked")
    return _receipt(
        "effect",
        parsed.to_dict(),
        1,
        scope_hash=expected_scope_hash,
        input_hash=expected_input_hash,
    )


def build_fixed_agent_artifact(
    *,
    artifact_id: str,
    candidate: CandidateEnvelope | Mapping[str, Any],
    provenance: AgentArtifactProvenance | Mapping[str, Any],
) -> FixedAgentArtifact:
    """Build a hash-bound immutable artifact from already-captured agent output."""

    parsed_candidate = _candidate(candidate)
    parsed_provenance = (
        provenance
        if isinstance(provenance, AgentArtifactProvenance)
        else AgentArtifactProvenance.from_mapping(provenance)
    )
    return FixedAgentArtifact(
        artifact_id=artifact_id,
        scope_hash=parsed_candidate.scope_hash,
        input_hash=parsed_candidate.input_hash,
        observation_id=parsed_candidate.observation_id,
        observation_hash=parsed_candidate.observation_hash,
        candidate=parsed_candidate,
        provenance=parsed_provenance,
    )


def validate_fixed_agent_artifact_replay(
    artifact: FixedAgentArtifact | Mapping[str, Any],
    replay: FixedAgentArtifact | Mapping[str, Any],
    observation: LedgerObservation | Mapping[str, Any],
    *,
    expected_scope_hash: str,
    expected_input_hash: str,
    expected_agent_id: str | None = None,
    expected_policy_id: str | None = None,
) -> ValidationReceipt:
    """Validate two fixed artifacts and require byte-equivalent canonical replay."""

    parsed = (
        artifact
        if isinstance(artifact, FixedAgentArtifact)
        else FixedAgentArtifact.from_mapping(artifact)
    )
    replayed = (
        replay
        if isinstance(replay, FixedAgentArtifact)
        else FixedAgentArtifact.from_mapping(replay)
    )
    for item in (parsed, replayed):
        validate_candidate(
            item.candidate,
            observation,
            expected_scope_hash=expected_scope_hash,
            expected_input_hash=expected_input_hash,
            require_provenance=True,
        )
        _expect_hash(item.scope_hash, expected_scope_hash, "scope_hash", scope=True)
        _expect_hash(item.input_hash, expected_input_hash, "input_hash")
        _expect_hash(
            item.observation_hash, item.candidate.observation_hash, "observation_hash"
        )
        if item.observation_id != item.candidate.observation_id:
            _fail(ReasonCode.HASH_MISMATCH, "artifact observation_id is not bound")
        if (
            expected_agent_id is not None
            and item.provenance.agent_id != expected_agent_id
        ):
            _fail(
                ReasonCode.SCOPE_MISMATCH,
                "artifact agent_id is outside the fixed scope",
            )
        if (
            expected_policy_id is not None
            and item.provenance.policy_id != expected_policy_id
        ):
            _fail(
                ReasonCode.SCOPE_MISMATCH,
                "artifact policy_id is outside the fixed scope",
            )
    if canonical_json(parsed.to_dict()) != canonical_json(replayed.to_dict()):
        _fail(ReasonCode.REPLAY_DRIFT, "fixed agent artifact replay differs")
    return _receipt(
        "fixed_agent_artifact_replay",
        parsed.to_dict(),
        1,
        scope_hash=parsed.scope_hash,
        input_hash=parsed.input_hash,
    )


def validate_deterministic_double_run(
    corpus: Any,
    runner: Callable[[Any], Any],
    *,
    expected_scope_hash: str,
    expected_input_hash: str | None = None,
    expected_output_hash: str | None = None,
    result_validator: Callable[[Any], None] | None = None,
) -> ValidationReceipt:
    """Run a pure evaluator twice on independent corpus copies and compare output."""

    scope_hash = _validated_hash(expected_scope_hash, "expected_scope_hash")
    try:
        corpus_json = canonical_json(corpus)
        input_hash = canonical_sha256(corpus)
    except (TypeError, ValueError) as exc:
        _raise_schema_error(exc)
    if expected_input_hash is not None:
        _expect_hash(input_hash, expected_input_hash, "input_hash")
    copies = (json.loads(corpus_json), json.loads(corpus_json))
    outputs: list[Any] = []
    serialized_outputs: list[str] = []
    for replay_input in copies:
        try:
            output = runner(replay_input)
        except RuleAnalysisValidationError:
            raise
        except Exception as exc:  # noqa: BLE001 - evaluator is a trust boundary.
            _fail(
                ReasonCode.DOMAIN_VALIDATION_FAILED,
                f"replay evaluator failed: {type(exc).__name__}: {exc}",
            )
        try:
            if canonical_json(replay_input) != corpus_json:
                _fail(ReasonCode.REPLAY_DRIFT, "runner mutated the fixed corpus")
            output_json = canonical_json(output)
        except RuleAnalysisValidationError:
            raise
        except (TypeError, ValueError) as exc:
            _raise_schema_error(exc)
        if result_validator is not None:
            try:
                result_validator(output)
            except RuleAnalysisValidationError:
                raise
            except Exception as exc:  # noqa: BLE001 - validator is a trust boundary.
                _fail(
                    ReasonCode.DOMAIN_VALIDATION_FAILED,
                    f"replay result validation failed: {type(exc).__name__}: {exc}",
                )
        outputs.append(output)
        serialized_outputs.append(output_json)
    if serialized_outputs[0] != serialized_outputs[1]:
        _fail(ReasonCode.REPLAY_NONDETERMINISTIC, "double-run outputs differ")
    output_hash = canonical_sha256(outputs[0])
    if expected_output_hash is not None:
        _expect_hash(output_hash, expected_output_hash, "output_hash")
    count = len(outputs[0]) if isinstance(outputs[0], (list, tuple)) else 1
    return ValidationReceipt(
        gate="deterministic_double_run",
        scope_hash=scope_hash,
        input_hash=input_hash,
        output_hash=output_hash,
        item_count=count,
    )


__all__ = (
    "AgentArtifactProvenance",
    "FixedAgentArtifact",
    "RuleAnalysisValidationError",
    "ValidationReceipt",
    "build_fixed_agent_artifact",
    "lint_effect_projection",
    "validate_candidate",
    "validate_deterministic_double_run",
    "validate_effect",
    "validate_fixed_agent_artifact_replay",
    "validate_proposal",
    "validate_proposal_batch",
)
