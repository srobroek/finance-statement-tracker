from __future__ import annotations

import copy
import json
import unittest
from collections.abc import MutableMapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast

from finance_tracker.rule_analysis import (
    EvaluationResult,
    ProposalResult,
    QueryResult,
    RuleAnalysisWorkbench,
    VerificationResult,
)
from finance_tracker.rule_analysis_lifecycle import (
    LifecycleRejection,
    RuleAnalysisLifecycle,
)
from finance_tracker.rule_analysis_reasons import (
    ReasonCode,
    UnknownReasonCode,
    reason_code,
)
from finance_tracker.rule_analysis_schema import (
    FORBIDDEN_SIDE_EFFECT_CAPABILITIES,
    POLICY_A_EFFECT_MATRIX,
    PROTECTED_ACTUAL_STRUCTURAL_FIELDS,
    PROTECTED_ECONOMIC_FIELDS,
    PROTECTED_IDENTITY_FIELDS,
    CandidateEnvelope,
    EffectEnvelope,
    FeedbackEnvelope,
    LedgerObservation,
    ProposalEnvelope,
    canonical_sha256,
)
from finance_tracker.rule_analysis_validation import (
    AgentArtifactProvenance,
    RuleAnalysisValidationError,
    build_fixed_agent_artifact,
    lint_effect_projection,
)

ROOT = Path(__file__).resolve().parent.parent
_ZERO_HASH = "0" * 64


class RuleAnalysisWorkbenchTests(unittest.TestCase):
    def _workbench(
        self, *, lifecycle: RuleAnalysisLifecycle | None = None
    ) -> RuleAnalysisWorkbench:
        return RuleAnalysisWorkbench(
            scope={"ledger": "household", "period": "2026-08"},
            lifecycle=lifecycle or RuleAnalysisLifecycle(),
        )

    def _observation(self, observation_id: str, **overrides: Any) -> LedgerObservation:
        values: dict[str, Any] = {
            "observation_id": observation_id,
            "transaction_id": f"txn-{observation_id}",
            "transaction_at": "2026-08-15T12:00:00+00:00",
            "card": "CARD-1234",
            "account": f"account-{observation_id}",
            "merchant_raw": "Internal transfer",
            "amount_aed": "125.50",
            "currency": "AED",
            "source_direction": "DEBIT",
            "source_type": "actual-export",
            "transaction_type": "TRANSFER",
            "evidence_refs": (f"evidence:{observation_id}",),
        }
        values.update(overrides)
        return LedgerObservation(**values)

    def _candidate(
        self,
        workbench: RuleAnalysisWorkbench,
        observation: LedgerObservation,
        *,
        candidate_id: str = "candidate-1",
        candidate_kind: str = "DETERMINISTIC_RULE",
        input_hash: str = _ZERO_HASH,
        target_fields: tuple[str, ...] = ("category",),
        reason_code_value: str = "MATCH_UNIQUE",
        payload: dict[str, Any] | None = None,
        evidence_refs: tuple[str, ...] = ("evidence:reviewed",),
    ) -> CandidateEnvelope:
        return CandidateEnvelope(
            candidate_id=candidate_id,
            candidate_kind=candidate_kind,
            observation_id=observation.observation_id,
            observation_hash=observation.observation_hash or "",
            scope_hash=workbench.scope_hash,
            input_hash=input_hash,
            target_fields=target_fields,
            reason_code=reason_code_value,
            confidence=0.98,
            evidence_refs=evidence_refs,
            payload=payload or {target_fields[0]: "Groceries"},
        )

    def _counterpart(self, observation_id: str, *, account: str) -> LedgerObservation:
        return self._observation(
            observation_id,
            account=account,
            card=f"CARD-{account}",
            source_direction="CREDIT",
        )

    def _feedback(
        self,
        proposal: ProposalEnvelope,
        *,
        feedback_id: str,
        decision: str,
        reason: str,
        sequence: int,
        previous_feedback_hash: str | None = None,
    ) -> FeedbackEnvelope:
        return FeedbackEnvelope(
            feedback_id=feedback_id,
            proposal_id=proposal.proposal_id,
            proposal_hash=proposal.proposal_hash or "",
            observation_id=proposal.observation_id,
            observation_hash=proposal.observation_hash,
            decision=decision,
            reason_code=reason,
            actor_id="reviewer-1",
            sequence=sequence,
            previous_feedback_hash=previous_feedback_hash,
        )

    def test_closed_shapes_are_deeply_immutable_and_reject_unknown_fields(self) -> None:
        workbench = self._workbench()
        observation = self._observation("immutable")
        candidate = self._candidate(workbench, observation)
        proposal = workbench.propose(candidate, observation).proposals[0]
        feedback = self._feedback(
            proposal,
            feedback_id="feedback-1",
            decision="REVIEW_ONLY",
            reason="REVIEW_ONLY",
            sequence=1,
        )

        for value, field_name, replacement in (
            (observation, "category", "Dining"),
            (candidate, "confidence", 0.1),
            (proposal, "value", "Dining"),
            (feedback, "actor_id", "other-reviewer"),
        ):
            with (
                self.subTest(type=type(value).__name__),
                self.assertRaises(FrozenInstanceError),
            ):
                setattr(value, field_name, replacement)
        with self.assertRaises(TypeError):
            cast(MutableMapping[str, Any], candidate.payload)["category"] = "Dining"
        with self.assertRaises(TypeError):
            cast(MutableMapping[str, Any], workbench.scope)["ledger"] = "other"

        closed_values = (
            (LedgerObservation, observation.to_dict()),
            (CandidateEnvelope, candidate.to_dict()),
            (ProposalEnvelope, proposal.to_dict()),
            (FeedbackEnvelope, feedback.to_dict()),
        )
        for factory, serialized in closed_values:
            with self.subTest(factory=factory.__name__):
                serialized["unexpected"] = True
                with self.assertRaisesRegex(ValueError, "unknown fields"):
                    factory.from_mapping(serialized)
        with self.assertRaisesRegex(ValueError, "missing target fields"):
            self._candidate(
                workbench,
                observation,
                target_fields=("category", "tags"),
                payload={"category": "Groceries"},
            )

    def test_unknown_reasons_fail_closed_before_lifecycle_registration(self) -> None:
        with self.assertRaises(UnknownReasonCode):
            reason_code("NOT_A_REGISTERED_REASON")

        workbench = self._workbench()
        observation = self._observation("unknown-reason")
        candidate = self._candidate(
            workbench,
            observation,
            reason_code_value="NOT_A_REGISTERED_REASON",
        )
        with self.assertRaises(RuleAnalysisValidationError) as caught:
            workbench.propose(candidate, observation)
        self.assertIs(caught.exception.reason_code, ReasonCode.UNKNOWN_REASON)
        self.assertEqual(workbench.lifecycle.candidates, ())

    def test_policy_a_agent_targets_are_unresolved_suggestions_and_never_committable(
        self,
    ) -> None:
        workbench = self._workbench()
        observation = self._observation(
            "agent-policy", channel="UNKNOWN", reward_bucket=None
        )
        candidate = self._candidate(
            workbench,
            observation,
            candidate_kind="AGENT_POLICY",
            target_fields=("channel", "reward_bucket"),
            reason_code_value="UNRESOLVED",
            payload={"channel": "ONLINE", "reward_bucket": "SC_ONLINE"},
        )

        result = workbench.propose(candidate, observation)

        self.assertEqual(
            [(item.field, item.target_kind) for item in result.proposals],
            [("channel", "SUGGESTION_ONLY"), ("reward_bucket", "SUGGESTION_ONLY")],
        )
        for field in ("channel", "reward_bucket"):
            policy = POLICY_A_EFFECT_MATRIX[field]
            self.assertTrue(policy.suggestion_only)
            self.assertTrue(policy.unresolved_only)
            self.assertFalse(policy.writable)
            self.assertFalse(policy.direct_commit_allowed)

        proposal = result.proposals[0]
        effect_args = {
            "effect_id": "effect-1",
            "proposal_id": proposal.proposal_id,
            "proposal_hash": proposal.proposal_hash or "",
            "observation_id": proposal.observation_id,
            "observation_hash": proposal.observation_hash,
            "field": proposal.field,
            "target_kind": proposal.target_kind,
            "value": proposal.value,
        }
        with self.assertRaisesRegex(ValueError, "cannot commit directly"):
            EffectEnvelope(**effect_args, direct_commit_allowed=True)
        with self.assertRaisesRegex(ValueError, "executable capabilities"):
            EffectEnvelope(**effect_args, capabilities=("SQLITE_WRITE",))

        resolved = self._observation("resolved-channel", channel="ONLINE")
        resolved_candidate = self._candidate(
            workbench,
            resolved,
            candidate_kind="AGENT_POLICY",
            target_fields=("channel",),
            reason_code_value="UNRESOLVED",
            payload={"channel": "PHYSICAL_POS"},
        )
        with self.assertRaisesRegex(ValueError, "only while.*unresolved"):
            workbench.propose(resolved_candidate, resolved)

        with self.assertRaisesRegex(ValueError, "only suggestion-only fields"):
            self._candidate(
                workbench,
                observation,
                candidate_id="candidate-illegal-agent-target",
                candidate_kind="AGENT_POLICY",
                target_fields=("category",),
                reason_code_value="UNRESOLVED",
            )

    def test_manual_field_relation_and_all_protected_classes_are_guarded(self) -> None:
        lifecycle = RuleAnalysisLifecycle().with_field_locked("category")
        with self.assertRaises(LifecycleRejection) as caught:
            lifecycle.guard_target("category")
        self.assertEqual(caught.exception.reason_code, ReasonCode.MANUAL_LOCKED.value)

        locked_relation = RuleAnalysisLifecycle().with_relation_locked("transfer-link")
        workbench = self._workbench(lifecycle=locked_relation)
        observation = self._observation("relation-lock")
        candidate = self._candidate(workbench, observation)
        with self.assertRaises(LifecycleRejection) as caught:
            workbench.propose(candidate, observation, relation="transfer-link")
        self.assertEqual(
            caught.exception.reason_code, ReasonCode.MANUAL_LOCK_CONFLICT.value
        )

        field_locked_observation = self._observation(
            "field-lock", manual_locked_fields=("category",)
        )
        field_locked_candidate = self._candidate(workbench, field_locked_observation)
        with self.assertRaises(RuleAnalysisValidationError) as caught:
            workbench.propose(field_locked_candidate, field_locked_observation)
        self.assertIs(caught.exception.reason_code, ReasonCode.MANUAL_LOCK_CONFLICT)

        protected_groups = (
            (PROTECTED_IDENTITY_FIELDS, ReasonCode.PROTECTED_IDENTITY_FIELD),
            (PROTECTED_ECONOMIC_FIELDS, ReasonCode.PROTECTED_ECONOMIC_FIELD),
            (PROTECTED_ACTUAL_STRUCTURAL_FIELDS, ReasonCode.PROTECTED_ACTUAL_FIELD),
        )
        for fields, expected_reason in protected_groups:
            for field in fields:
                with self.subTest(field=field):
                    with self.assertRaises(LifecycleRejection) as caught:
                        RuleAnalysisLifecycle().guard_target(field)
                    self.assertEqual(
                        caught.exception.reason_code, expected_reason.value
                    )

    def test_matching_unique_missing_conflicting_and_ambiguous_never_guesses(
        self,
    ) -> None:
        workbench = self._workbench()
        source = self._observation("source", account="account-source")
        counterpart = self._counterpart("counterpart", account="account-counterpart")

        unique = workbench.evaluate(source, (counterpart,))
        self.assertEqual(unique.outcome, "UNIQUE")
        self.assertIsNotNone(unique.relation)
        self.assertFalse(unique.review_only)

        with self.assertRaises(UnknownReasonCode):
            EvaluationResult(
                query=unique.query,
                outcome=unique.outcome,
                relation=unique.relation,
                reason_codes=("NOT_A_REGISTERED_REASON",),
                review_only=False,
            )
        with self.assertRaisesRegex(ValueError, "do not match the query trace"):
            EvaluationResult(
                query=unique.query,
                outcome="MISSING",
                relation=None,
                reason_codes=unique.reason_codes,
                review_only=True,
            )

        missing = workbench.evaluate(source, (source,))
        self.assertEqual(missing.outcome, "MISSING")
        self.assertIsNone(missing.relation)
        self.assertTrue(missing.review_only)

        locked_source = self._observation(
            "locked-source",
            account="account-locked-source",
            manual_locked_fields=("existing_link",),
        )
        conflicting = workbench.evaluate(
            locked_source,
            (self._counterpart("locked-peer", account="account-locked-peer"),),
        )
        self.assertEqual(conflicting.outcome, "CONFLICTING")
        self.assertIsNone(conflicting.relation)
        self.assertIn(ReasonCode.MANUAL_LOCK_CONFLICT.value, conflicting.reason_codes)
        self.assertTrue(conflicting.review_only)

        ambiguous = workbench.evaluate(
            source,
            (
                counterpart,
                self._counterpart("second-counterpart", account="account-second"),
            ),
        )
        self.assertEqual(ambiguous.outcome, "AMBIGUOUS")
        self.assertIsNone(ambiguous.relation)
        self.assertTrue(ambiguous.review_only)
        self.assertEqual(
            ambiguous.reason_codes,
            (ReasonCode.TRANSFER_MATCH_AMBIGUOUS.value,),
        )

    def test_query_and_evaluation_are_deterministic_across_corpus_order(self) -> None:
        workbench = self._workbench()
        source = self._observation("deterministic-source", account="account-source")
        counterpart = self._counterpart("deterministic-peer", account="account-peer")
        corpus = (source, counterpart)

        first_query = workbench.query(source, corpus)
        second_query = workbench.query(source, tuple(reversed(corpus)))
        first_evaluation = workbench.evaluate(source, corpus)
        second_evaluation = workbench.evaluate(source, tuple(reversed(corpus)))

        self.assertIsInstance(first_query, QueryResult)
        self.assertEqual(first_query.to_dict(), second_query.to_dict())
        self.assertEqual(first_query.query_hash, second_query.query_hash)
        self.assertIsInstance(first_evaluation, EvaluationResult)
        self.assertEqual(first_evaluation.to_dict(), second_evaluation.to_dict())
        self.assertEqual(
            first_evaluation.evaluation_hash, second_evaluation.evaluation_hash
        )

    def test_scope_observation_input_and_provenance_bindings_fail_closed(self) -> None:
        workbench = self._workbench()
        observation = self._observation("bindings")

        wrong_scope = self._candidate(workbench, observation)
        wrong_scope_values = wrong_scope.to_dict()
        wrong_scope_values.pop("candidate_hash")
        wrong_scope_values["scope_hash"] = _ZERO_HASH
        with self.assertRaises(RuleAnalysisValidationError) as caught:
            workbench.propose(
                CandidateEnvelope.from_mapping(wrong_scope_values), observation
            )
        self.assertIs(caught.exception.reason_code, ReasonCode.SCOPE_MISMATCH)

        wrong_observation_values = wrong_scope.to_dict()
        wrong_observation_values.pop("candidate_hash")
        wrong_observation_values["observation_hash"] = _ZERO_HASH
        with self.assertRaises(RuleAnalysisValidationError) as caught:
            workbench.propose(
                CandidateEnvelope.from_mapping(wrong_observation_values), observation
            )
        self.assertIs(caught.exception.reason_code, ReasonCode.HASH_MISMATCH)

        missing_provenance = self._candidate(
            workbench,
            observation,
            candidate_id="candidate-missing-provenance",
            evidence_refs=(),
        )
        with self.assertRaises(RuleAnalysisValidationError) as caught:
            workbench.propose(missing_provenance, observation)
        self.assertIs(caught.exception.reason_code, ReasonCode.MISSING_EVIDENCE)

        peer = self._counterpart("bindings-peer", account="account-bindings-peer")
        wrong_input = self._candidate(
            workbench,
            observation,
            candidate_id="candidate-wrong-input",
            input_hash=_ZERO_HASH,
            reason_code_value="TRANSFER_MATCH_UNIQUE",
        )
        with self.assertRaises(RuleAnalysisValidationError) as caught:
            workbench.verify(wrong_input, (observation, peer))
        self.assertIs(caught.exception.reason_code, ReasonCode.HASH_MISMATCH)

    def test_candidate_replay_is_idempotent_but_same_id_drift_is_rejected(self) -> None:
        workbench = self._workbench()
        observation = self._observation("candidate-replay")
        candidate = self._candidate(workbench, observation)
        first = workbench.propose(candidate, observation)
        replayed = first.workbench.propose(candidate, observation)

        self.assertEqual(first.to_dict(), replayed.to_dict())
        self.assertEqual(first.lifecycle_hash, replayed.lifecycle_hash)

        drifted = self._candidate(
            workbench,
            observation,
            payload={"category": "Dining"},
        )
        with self.assertRaises(RuleAnalysisValidationError) as caught:
            first.workbench.propose(drifted, observation)
        self.assertIs(caught.exception.reason_code, ReasonCode.REPLAY_DRIFT)

    def test_feedback_is_append_only_and_duplicate_or_post_stale_updates_fail(
        self,
    ) -> None:
        workbench = self._workbench()
        observation = self._observation("feedback")
        candidate = self._candidate(workbench, observation)
        proposed = workbench.propose(candidate, observation)
        proposal = proposed.proposals[0]
        review = self._feedback(
            proposal,
            feedback_id="feedback-review",
            decision="REVIEW_ONLY",
            reason="REVIEW_ONLY",
            sequence=1,
        )

        reviewed = proposed.workbench.propose(
            candidate, observation, feedback=(review,)
        )
        self.assertEqual(proposed.lifecycle.proposals[0].feedback, ())
        self.assertEqual(reviewed.lifecycle.proposals[0].state, "REVIEW_ONLY")
        self.assertEqual(reviewed.lifecycle.proposals[0].feedback, (review,))

        with self.assertRaises(LifecycleRejection) as caught:
            reviewed.workbench.propose(candidate, observation, feedback=(review,))
        self.assertEqual(caught.exception.reason_code, ReasonCode.REPLAY_DRIFT.value)

        stale = self._feedback(
            proposal,
            feedback_id="feedback-stale",
            decision="REJECTED",
            reason="STALE_PROPOSAL",
            sequence=2,
            previous_feedback_hash=review.feedback_hash,
        )
        stale_result = reviewed.workbench.propose(
            candidate, observation, feedback=(stale,)
        )
        record = stale_result.lifecycle.proposals[0]
        self.assertEqual(record.state, "STALE")
        self.assertEqual(record.feedback, (review, stale))

        post_stale = self._feedback(
            proposal,
            feedback_id="feedback-after-stale",
            decision="REJECTED",
            reason="DOMAIN_VALIDATION_FAILED",
            sequence=3,
            previous_feedback_hash=stale.feedback_hash,
        )
        with self.assertRaises(LifecycleRejection) as caught:
            stale_result.workbench.propose(
                candidate, observation, feedback=(post_stale,)
            )
        self.assertEqual(caught.exception.reason_code, ReasonCode.STALE_PROPOSAL.value)

    def test_cross_runtime_spec_only_effect_projection_has_no_policy_drift(
        self,
    ) -> None:
        projection = json.loads(
            (ROOT / "integrations" / "n8n" / "ai-policy-targets.json").read_text(
                encoding="utf-8"
            )
        )

        receipt = lint_effect_projection(projection)

        self.assertEqual(receipt.gate, "effect_projection")
        self.assertTrue(receipt.deterministic)
        self.assertTrue(receipt.proposal_only)
        self.assertEqual(projection["contract_status"], "SPEC_ONLY")
        self.assertTrue(projection["direct_commit_forbidden"])
        self.assertEqual(
            tuple(projection["forbidden_side_effect_capabilities"]),
            FORBIDDEN_SIDE_EFFECT_CAPABILITIES,
        )

        drifted = copy.deepcopy(projection)
        drifted["effect_matrix"]["channel"]["direct_commit_allowed"] = True
        with self.assertRaises(RuleAnalysisValidationError) as caught:
            lint_effect_projection(drifted)
        self.assertIs(caught.exception.reason_code, ReasonCode.EFFECT_POLICY_DRIFT)

    def test_facade_query_evaluate_propose_and_verify_bind_one_observable_flow(
        self,
    ) -> None:
        workbench = self._workbench()
        observation = self._observation("facade-source", account="account-source")
        counterpart = self._counterpart("facade-peer", account="account-peer")
        corpus = (counterpart, observation)
        query = workbench.query(observation, corpus)
        evaluation = workbench.evaluate(observation, corpus)
        candidate = self._candidate(
            workbench,
            observation,
            input_hash=evaluation.input_hash,
            reason_code_value=ReasonCode.TRANSFER_MATCH_UNIQUE.value,
        )

        proposed = workbench.propose(candidate, observation)
        verified = workbench.verify(candidate, corpus)

        self.assertIsInstance(query, QueryResult)
        self.assertIsInstance(evaluation, EvaluationResult)
        self.assertIsInstance(proposed, ProposalResult)
        self.assertIsInstance(verified, VerificationResult)
        self.assertEqual(query.input_hash, evaluation.input_hash)
        self.assertEqual(proposed.candidate.candidate_hash, candidate.candidate_hash)
        self.assertEqual(verified.input_hash, evaluation.input_hash)
        self.assertEqual(
            verified.corpus_hash, canonical_sha256([item.to_dict() for item in corpus])
        )
        self.assertEqual(verified.candidate_receipt.gate, "candidate")
        self.assertEqual(verified.replay_receipt.gate, "deterministic_double_run")
        self.assertTrue(verified.to_dict()["verified"])
        self.assertTrue(proposed.to_dict()["proposal_only"])
        self.assertTrue(verified.to_dict()["proposal_only"])

    def test_fixed_agent_artifact_replay_is_required_and_drift_is_rejected(
        self,
    ) -> None:
        workbench = self._workbench()
        observation = self._observation("artifact-source", account="account-source")
        counterpart = self._counterpart("artifact-peer", account="account-peer")
        corpus = (observation, counterpart)
        evaluation = workbench.evaluate(observation, corpus)
        candidate = self._candidate(
            workbench,
            observation,
            candidate_kind="AGENT_POLICY",
            input_hash=evaluation.input_hash,
            target_fields=("channel",),
            reason_code_value="UNRESOLVED",
            payload={"channel": "ONLINE"},
        )
        provenance = AgentArtifactProvenance(
            agent_id="fixed-agent",
            agent_version="sol-high-2026-09-14",
            policy_id="policy-a",
            source_artifact_id="captured-output-1",
            source_artifact_sha256=canonical_sha256({"captured": "output"}),
        )
        artifact = build_fixed_agent_artifact(
            artifact_id="artifact-1",
            candidate=candidate,
            provenance=provenance,
        )

        verified = workbench.verify(artifact, corpus)

        artifact_receipt = verified.artifact_receipt
        self.assertIsNotNone(artifact_receipt)
        if artifact_receipt is None:
            self.fail("agent verification omitted its artifact replay receipt")
        self.assertEqual(artifact_receipt.gate, "fixed_agent_artifact_replay")

        drifted_candidate = self._candidate(
            workbench,
            observation,
            candidate_id="candidate-drifted-artifact",
            candidate_kind="AGENT_POLICY",
            input_hash=evaluation.input_hash,
            target_fields=("reward_bucket",),
            reason_code_value="UNRESOLVED",
            payload={"reward_bucket": "SC_ONLINE"},
        )
        drifted_artifact = build_fixed_agent_artifact(
            artifact_id="artifact-1",
            candidate=drifted_candidate,
            provenance=provenance,
        )
        with self.assertRaises(RuleAnalysisValidationError) as caught:
            workbench.verify((artifact, drifted_artifact), corpus)
        self.assertIs(caught.exception.reason_code, ReasonCode.REPLAY_DRIFT)

        with self.assertRaises(RuleAnalysisValidationError) as caught:
            workbench.verify(candidate, corpus)
        self.assertIs(caught.exception.reason_code, ReasonCode.MISSING_EVIDENCE)

    def test_public_facade_and_results_expose_no_executable_mutation_capability(
        self,
    ) -> None:
        workbench = self._workbench()
        observation = self._observation("capabilities-source", account="account-source")
        counterpart = self._counterpart("capabilities-peer", account="account-peer")
        corpus = (observation, counterpart)
        query = workbench.query(observation, corpus)
        evaluation = workbench.evaluate(observation, corpus)
        candidate = self._candidate(
            workbench,
            observation,
            input_hash=evaluation.input_hash,
            reason_code_value=ReasonCode.TRANSFER_MATCH_UNIQUE.value,
        )
        proposal = workbench.propose(candidate, observation)
        verification = workbench.verify(candidate, corpus)
        public_objects = (workbench, query, evaluation, proposal, verification)
        forbidden_methods = {
            "apply",
            "apply_actual",
            "commit",
            "execute",
            "mutate_ledger",
            "network",
            "persist",
            "promote",
            "promote_proposal",
            "provider_call",
            "send_notification",
            "sqlite_write",
            "write",
        }

        forbidden_attribute = "apply"
        for value in public_objects:
            with self.subTest(type=type(value).__name__):
                exposed = {
                    name
                    for name in forbidden_methods
                    if callable(getattr(value, name, None))
                }
                self.assertEqual(exposed, set())
                with self.assertRaises(
                    (FrozenInstanceError, TypeError, AttributeError)
                ):
                    setattr(value, forbidden_attribute, lambda: None)
        self.assertEqual(proposal.lifecycle.to_dict()["external_effects"], ())
        self.assertFalse(proposal.lifecycle.proposals[0].executable)


if __name__ == "__main__":
    unittest.main()
