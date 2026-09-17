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
# J26 -- Recalculate dashboard/stale warnings

## Goal
As a finance user, recalculate dashboard data and observe honest stale-data warnings without changing source transactions.

## Preconditions
- P1: Dashboard inputs and last-refresh metadata are available.
- P2: Surfaces: Cashback Control dashboard and `finance_tracker/reports.py` / aggregation code.
- P3: Recalculation may write derived state only with disposable scope, approval, rollback, and readback.

## Steps
### S1 -- Recalculate dashboard {#S1}
- **Do:** Request dashboard recalculation.
- **Expect:** Derived totals are recomputed from current accepted records and refresh metadata updates deterministically.

### S2 -- Inspect freshness {#S2}
- **Do:** Inspect dashboard and freshness indicators.
- **Expect:** Stale warnings appear when source/refresh evidence is old or incomplete, and clear only when evidence supports freshness.

### S3 -- Report stale or failed calculation {#S3}
- **Do:** Missing source, failed calculation, or contradictory refresh metadata.
- **Expect (negative):** Fail closed or show an explicit stale warning; never present fabricated fresh totals.

### S4 -- Verify source preservation {#S4}
- **Do:** Re-read and, where applicable, roll back derived state.
- **Expect (negative):** No source ledger/event mutation and no duplicate derived rows.

## Success criteria
- SC1: S1: Derived totals are recomputed from current accepted records and refresh metadata updates deterministically.
- SC2: S2: Stale warnings appear when source/refresh evidence is old or incomplete, and clear only when evidence supports freshness.
- SC3: S3: Fail closed or show an explicit stale warning; never present fabricated fresh totals.
- SC4: S4: No source ledger/event mutation and no duplicate derived rows.

## Known gaps
- G1: Dashboard runtime and exact stale-warning fixtures are unavailable; this is an evidence-backed draft only. Trace evidence: Beads `orc-n2q.379.27`; canonical finding linkage `orc-n2q.379.157`. Beads artifact `local://journeys-J19-J27-beads.json`. `finance_tracker/reports.py` and current cashback aggregation surfaces.

## Delta log
- No behavior delta; structural normalization only.
