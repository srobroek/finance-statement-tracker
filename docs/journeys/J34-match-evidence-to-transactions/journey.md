---
id: J34
title: Match evidence to transactions
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [evidence-ingestion]
interfaces: [RW-O]
trace: []
---
# J34 -- Match evidence to transactions

## Goal
As an operator, deterministically match archived evidence to existing transactions, exposing ambiguity rather than guessing or writing an unrelated record.

## Preconditions
- P1: Evidence and transaction candidates have stable IDs, dates, amounts, currencies, and source digests.
- P2: Matching policy, Actual readback, approval boundary, and rollback/compensation are the surfaces.
- P3: Evidence: `finance-statement-tracker/docs/actual-note-contract.md`, `finance-statement-tracker/docs/full-ingestion-validation.md`.
- P4: The journey uses the exclusive-write-serial lane.

## Steps
### S1 -- Match candidates {#S1}
- **Do:** Read candidates and apply the declared exact identity/tolerance policy.
- **Expect:** The result is one match, an explicit no-match, or an ambiguity.
- **Expect (negative):** No implicit tie-break occurs.

### S2 -- Approve consequential link {#S2}
- **Do:** Obtain point-of-risk approval before any consequential link/write.
- **Expect:** Approval is bound to affected IDs and policy.

### S3 -- Reject ambiguous match {#S3}
- **Do:** Multiple equal candidates, sign/currency conflict, or missing identity.
- **Expect:** The operator requests review.
- **Expect (negative):** Matching fails closed.

### S4 -- Restore linked state {#S4}
- **Do:** After an approved link, perform rollback and fresh readback.
- **Expect:** The operation restores the original state.
- **Expect (negative):** No duplicate or partial effect remains.

## Success criteria
- SC1: S1-S4: Capture candidate set, selected identity, approval, pre/post state, write receipt (if authorized), rollback receipt, and fresh equality proof.
- SC2: S1-S4: No source artifact is overwritten.

## Known gaps
- G1: The concrete production endpoint and original journey trace are not available in this checkout; no write was attempted, so readiness remains draft. Trace evidence: Beads contract `orc-n2q.379.35`.

## Delta log
- No behavior delta; structural normalization only.
