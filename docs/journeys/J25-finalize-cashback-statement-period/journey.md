---
id: J25
title: Finalize cashback statement period
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [cashback-control]
interfaces: [RW-S]
trace: []
---
# J25 -- Finalize cashback statement period

## Goal
As a finance operator, finalize a reviewed cashback statement period and preserve its boundaries. Produce a reconciled final status.

## Preconditions
- P1: Statement-period data and any correction findings are reviewed and canonical.
- P2: Surfaces: Cashback Control, statement/report generation (`finance_tracker/statements.py`, `finance_tracker/reports.py`).
- P3: Finalization is consequential. Capture pre-state and explicit approval. A rollback or compensation and fresh readback are mandatory.

## Steps
### S1 -- Finalize period {#S1}
- **Do:** Capture period pre-state and finalize the specified period.
- **Expect:** Only that period transitions to finalized; totals and provenance are retained.

### S2 -- Read back finalization {#S2}
- **Do:** Read back period status, totals, and receipt.
- **Expect:** Final state is deterministic and reconciled
- **Expect:** Repeat finalization is idempotent or rejected.

### S3 -- Reject unsafe finalization {#S3}
- **Do:** Incomplete or overlapping period finalization.
- **Expect (negative):** Stale or unauthorized finalization also fails closed with no partial period mutation.

### S4 -- Restore period state {#S4}
- **Do:** Roll back/compensate and verify.
- **Expect:** Prior period state and totals are restored exactly.

## Success criteria
- SC1: S1: Only that period transitions to finalized; totals and provenance are retained.
- SC2: S2: Final state is deterministic and reconciled
- SC3: S3: Stale or unauthorized finalization also fails closed with no partial period mutation.
- SC4: S4: Prior period state and totals are restored exactly.

## Known gaps
- G1: Exact period fixture and runtime finalization receipt are unavailable; draft remains unvalidated. Trace evidence: Beads `orc-n2q.379.26`; canonical finding linkage `orc-n2q.379.157`. Beads artifact `local://journeys-J19-J27-beads.json`. `finance_tracker/statements.py`, `finance_tracker/reports.py`.

## Delta log
- No behavior delta; structural normalization only.
