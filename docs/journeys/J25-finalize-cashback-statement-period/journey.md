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
# Goal
As a finance operator, finalize a reviewed cashback statement period, preserving period boundaries and producing a reconciled final status.

## Preconditions and surfaces
- Statement-period data and any correction findings are reviewed and canonical.
- Surfaces: Cashback Control, statement/report generation (`finance_tracker/statements.py`, `finance_tracker/reports.py`).
- Finalization is consequential: pre-state, explicit approval, rollback/compensation, and fresh readback are mandatory.

## Journey
1. **Do:** Capture period pre-state and finalize the specified period.
   **Expect:** Only that period transitions to finalized; totals and provenance are retained.
2. **Do:** Read back period status, totals, and receipt.
   **Expect:** Final state is deterministic and reconciled; repeat finalization is idempotent/rejected.
3. **Expect-negative:** Incomplete, overlapping, stale, or unauthorized period finalization.
   **Expect:** Fail closed with no partial period mutation.
4. **Do:** Roll back/compensate and verify.
   **Expect:** Prior period state and totals are restored exactly.

## Evidence
- Beads `orc-n2q.379.26`; canonical finding linkage `orc-n2q.379.157`.
- Beads artifact `local://journeys-J19-J27-beads.json`.
- `finance_tracker/statements.py`, `finance_tracker/reports.py`.

## Known gaps
- Exact period fixture and runtime finalization receipt are unavailable; draft remains unvalidated.
