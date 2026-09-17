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

- **Stable ID:** J34
- **Profile:** RW-O
- **Beads contract:** `orc-n2q.379.35`
- **Lane:** exclusive-write-serial
- **Status:** draft (behavioral validation not performed)

## Goal
As an operator, deterministically match archived evidence to existing transactions, exposing ambiguity rather than guessing or writing an unrelated record.

## Preconditions and surfaces
- Evidence and transaction candidates have stable IDs, dates, amounts, currencies, and source digests.
- Matching policy, Actual readback, approval boundary, and rollback/compensation are the surfaces.
- Evidence: `finance-statement-tracker/docs/actual-note-contract.md`, `finance-statement-tracker/docs/full-ingestion-validation.md`.

## Steps
1. **Do:** Read candidates and apply the declared exact identity/tolerance policy. **Expect:** One match, explicit no-match, or ambiguity; never an implicit tie-break.
2. **Do:** Obtain point-of-risk approval before any consequential link/write. **Expect:** Approval is bound to affected IDs and policy.
3. **Do (negative):** Multiple equal candidates, sign/currency conflict, or missing identity. **Expect:** Fail closed and request review.
4. **Do (negative):** After an approved link, perform rollback and fresh readback. **Expect:** Original state restored with no duplicate/partial effect.

## Evidence and acceptance
Capture candidate set, selected identity, approval, pre/post state, write receipt (if authorized), rollback receipt, and fresh equality proof. No source artifact is overwritten.

## Known gaps
The concrete production endpoint and original journey trace are not available in this checkout; no write was attempted, so readiness remains draft.
