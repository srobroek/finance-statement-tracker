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

- **Stable ID:** J36
- **Profile:** RW-O
- **Beads contract:** `orc-n2q.379.37`
- **Lane:** exclusive-write-serial
- **Status:** draft (behavioral validation not performed)

## Goal
As an operator, obtain a constrained AI proposal for unresolved transaction classification/evidence fields, while preserving source facts and requiring deterministic validation before any application.

## Preconditions and surfaces
- Source transaction identity, existing categories, configured policy, and allowed value sources are present.
- AI prompt/runner, proposal schema validator, policy gate, review queue, and Actual write boundary are touched.
- Evidence: `finance-statement-tracker/docs/ai-enrichment.md`, `finance-statement-tracker/docs/actual-note-contract.md`.

## Steps
1. **Do:** Submit only source facts and unresolved fields to the schema-bound proposal path. **Expect:** A typed proposal references the transaction and allowed evidence sources.
2. **Do:** Validate schema, confidence, policy, and deterministic identity before review. **Expect:** Invalid or out-of-policy proposals are rejected without mutation.
3. **Do (negative):** Ask AI to invent amount/date/merchant, alter IDs, or write a transaction. **Expect:** Request is refused and source remains unchanged.
4. **Do (negative):** Re-run with identical inputs/configuration. **Expect:** Same proposal identity/output or an explicit deterministic rejection.

## Evidence and acceptance
Capture redacted prompt/proposal digests, validator result, policy decision, reviewer/approval boundary, and proof of zero ledger mutation. Applying a proposal is outside this read-only journey.

## Known gaps
No AI execution receipt or authoritative prior journey body is available; this remains a draft pending independent readiness review.
