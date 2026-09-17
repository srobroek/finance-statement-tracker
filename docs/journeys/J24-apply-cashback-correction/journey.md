---
id: J24
title: Apply cashback correction
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [cashback-control]
interfaces: [RW-O]
trace: []
---
# J24 -- Apply cashback correction

## Goal
As a finance operator, apply one reviewed cashback correction and verify the resulting record without creating a duplicate or losing provenance.

## Preconditions
- P1: A canonical finding identifies the affected cashback record and reviewed correction.
- P2: Surfaces: Cashback Control and `finance_tracker/cashback_events.py`.
- P3: Disposable/resettable state, explicit point-of-risk approval, and exact rollback/readback are required.

## Steps
### S1 -- Apply correction {#S1}
- **Do:** Capture target pre-state and submit the reviewed correction.
- **Expect:** Only the identified record changes; source identity/provenance is retained.

### S2 -- Read back correction {#S2}
- **Do:** Read back the record, receipt, and relevant aggregate.
- **Expect:** Corrected amount/status is visible once and aggregates reconcile.

### S3 -- Reject unsafe correction {#S3}
- **Do:** Target unknown, stale, conflicting, duplicate, or unauthorized correction.
- **Expect (negative):** Fail closed with no partial change.

### S4 -- Restore corrected record {#S4}
- **Do:** Roll back or compensate and freshly read state.
- **Expect:** The original state is restored.
- **Expect (negative):** Duplicate and partial effects are absent.

## Success criteria
- SC1: S1: Only the identified record changes; source identity/provenance is retained.
- SC2: S2: Corrected amount/status is visible once and aggregates reconcile.
- SC3: S3: Fail closed with no partial change.
- SC4: S4: Original state is restored and duplicate/partial effects are absent.

## Known gaps
- G1: Exact reviewed correction fixture and runtime readback are unavailable; draft does not claim product validation. Trace evidence: Beads `orc-n2q.379.25`; canonical finding linkage `orc-n2q.379.157`. Beads artifact `local://journeys-J19-J27-beads.json`. `finance_tracker/cashback_events.py`, `finance_tracker/reports.py`.

## Delta log
- No behavior delta; structural normalization only.
