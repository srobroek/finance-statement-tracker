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

## Goal
As an operator, run statement acquisition, archival, parsing, normalization, reconciliation, and review in a deterministic order with durable receipts.

## Preconditions
- P1: Source boundary, parser/rules configuration, archive destination, and disposable/resettable scope are fixed.
- P2: n8n orchestration, statement adapters, evidence archive, Actual read/verification, and reconciliation receipts are touched.
- P3: Evidence: `finance-statement-tracker/docs/wealth-ingestion.md`, `finance-statement-tracker/docs/full-ingestion-validation.md`, `finance-statement-tracker/docs/finance-execution-plan.md`.
- P4: The journey uses the exclusive-write-serial lane.

## Steps
### S1 -- Capture run inputs {#S1}
- **Do:** Capture source/config digests and pre-state.
- **Expect:** Run identity and immutable inputs are recorded.

### S2 -- Execute ordered stages {#S2}
- **Do:** Execute each stage once in order, stopping on first mismatch.
- **Expect:** Per-stage counts, hashes, and classifications are receipt-bound.

### S3 -- Stop on pipeline mismatch {#S3}
- **Do:** Parser gap, duplicate source, cursor anomaly, or reconciliation mismatch.
- **Expect (negative):** Run stops, remains review-required, and does not write a guessed transaction.

### S4 -- Replay after cleanup {#S4}
- **Do:** Repeat after clean rollback/cleanup.
- **Expect (negative):** Idempotent results and no residue or duplicate ledger effects.

## Success criteria
- SC1: S1-S4: Acceptance requires complete stage receipts, source/archive readback, reconciliation proof, rollback/cleanup proof, and redacted failures.
- SC2: S1-S4: Production execution is not implied by documentation.

## Known gaps
- G1: No journey-local execution artifact is available; original J01--J51 corpus and shared readiness artifacts are missing, so status is draft. Trace evidence: Beads contract `orc-n2q.379.36`.

## Delta log
- No behavior delta; structural normalization only.
