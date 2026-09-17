---
id: J36
title: Acquire schema-bound AI proposal
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [evidence-ingestion]
interfaces: [RW-O]
trace: []
---
# J36 -- Acquire schema-bound AI proposal

## Goal
As an operator, obtain a constrained AI proposal for unresolved transaction classification/evidence fields, while preserving source facts and requiring deterministic validation before any application.

## Preconditions
- P1: Source transaction identity, existing categories, configured policy, and allowed value sources are present.
- P2: AI prompt/runner, proposal schema validator, policy gate, review queue, and Actual write boundary are touched.
- P3: Evidence: `finance-statement-tracker/docs/ai-enrichment.md`, `finance-statement-tracker/docs/actual-note-contract.md`.
- P4: The journey uses the exclusive-write-serial lane.

## Steps
### S1 -- Request proposal {#S1}
- **Do:** Submit only source facts and unresolved fields to the schema-bound proposal path.
- **Expect:** A typed proposal references the transaction and allowed evidence sources.

### S2 -- Validate proposal {#S2}
- **Do:** Validate schema, confidence, policy, and deterministic identity before review.
- **Expect (negative):** Invalid or out-of-policy proposals are rejected without mutation.

### S3 -- Reject invented facts {#S3}
- **Do:** Ask AI to invent amount/date/merchant, alter IDs, or write a transaction.
- **Expect (negative):** Request is refused and source remains unchanged.

### S4 -- Replay proposal {#S4}
- **Do:** Re-run with identical inputs/configuration.
- **Expect:** The replay returns the same proposal identity/output or an explicit deterministic rejection.

## Success criteria
- SC1: S1-S4: Capture redacted prompt/proposal digests, validator result, policy decision, reviewer/approval boundary, and proof of zero ledger mutation.
- SC2: S1-S4: Applying a proposal is outside this read-only journey.

## Known gaps
- G1: No AI execution receipt or authoritative prior journey body is available; this remains a draft pending independent readiness review. Trace evidence: Beads contract `orc-n2q.379.37`.

## Delta log
- No behavior delta; structural normalization only.
