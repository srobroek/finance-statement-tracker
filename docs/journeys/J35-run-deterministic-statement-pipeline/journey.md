---
id: J35
title: Run deterministic statement pipeline
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [evidence-ingestion]
interfaces: [RW-O]
trace: []
---
# J35 -- Run deterministic statement pipeline

- **Stable ID:** J35
- **Profile:** RW-O
- **Beads contract:** `orc-n2q.379.36`
- **Lane:** exclusive-write-serial
- **Status:** draft (behavioral validation not performed)

## Goal
As an operator, run statement acquisition, archival, parsing, normalization, reconciliation, and review in a deterministic order with durable receipts.

## Preconditions and surfaces
- Source boundary, parser/rules configuration, archive destination, and disposable/resettable scope are fixed.
- n8n orchestration, statement adapters, evidence archive, Actual read/verification, and reconciliation receipts are touched.
- Evidence: `finance-statement-tracker/docs/wealth-ingestion.md`, `finance-statement-tracker/docs/full-ingestion-validation.md`, `finance-statement-tracker/docs/finance-execution-plan.md`.

## Steps
1. **Do:** Capture source/config digests and pre-state. **Expect:** Run identity and immutable inputs are recorded.
2. **Do:** Execute each stage once in order, stopping on first mismatch. **Expect:** Per-stage counts, hashes, and classifications are receipt-bound.
3. **Do (negative):** Parser gap, duplicate source, cursor anomaly, or reconciliation mismatch. **Expect:** Run stops, remains review-required, and does not write a guessed transaction.
4. **Do (negative):** Repeat after clean rollback/cleanup. **Expect:** Idempotent results and no residue or duplicate ledger effects.

## Evidence and acceptance
Acceptance requires complete stage receipts, source/archive readback, reconciliation proof, rollback/cleanup proof, and redacted failures. Production execution is not implied by documentation.

## Known gaps
No journey-local execution artifact is available; original J01--J51 corpus and shared readiness artifacts are missing, so status is draft.
