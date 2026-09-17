---
id: J26
title: Recalculate dashboard/stale warnings
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [cashback-control]
interfaces: [RW-O]
trace: []
---
# Goal
As a finance user, recalculate dashboard data and observe honest stale-data warnings without changing source transactions.

## Preconditions and surfaces
- Dashboard inputs and last-refresh metadata are available.
- Surfaces: Cashback Control dashboard and `finance_tracker/reports.py` / aggregation code.
- Recalculation may write derived state only with disposable scope, approval, rollback, and readback.

## Journey
1. **Do:** Request dashboard recalculation.
   **Expect:** Derived totals are recomputed from current accepted records and refresh metadata updates deterministically.
2. **Do:** Inspect dashboard and freshness indicators.
   **Expect:** Stale warnings appear when source/refresh evidence is old or incomplete, and clear only when evidence supports freshness.
3. **Expect-negative:** Missing source, failed calculation, or contradictory refresh metadata.
   **Expect:** Fail closed or show an explicit stale warning; never present fabricated fresh totals.
4. **Do:** Re-read and, where applicable, roll back derived state.
   **Expect:** No source ledger/event mutation and no duplicate derived rows.

## Evidence
- Beads `orc-n2q.379.27`; canonical finding linkage `orc-n2q.379.157`.
- Beads artifact `local://journeys-J19-J27-beads.json`.
- `finance_tracker/reports.py` and current cashback aggregation surfaces.

## Known gaps
- Dashboard runtime and exact stale-warning fixtures are unavailable; this is an evidence-backed draft only.
