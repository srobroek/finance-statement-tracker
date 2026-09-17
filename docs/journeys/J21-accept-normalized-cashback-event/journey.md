---
id: J21
title: Accept normalized cashback event
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [cashback-control]
interfaces: [RW-O]
trace: []
---
# J21 -- Accept normalized cashback event

## Goal
As the finance pipeline, accept one normalized cashback event, preserve its identity and semantics, and expose the resulting accepted event safely.

## Preconditions
- P1: A normalized event with source identity, amount, currency, and account context is available.
- P2: Surfaces: Cashback Control and `finance_tracker/cashback_events.py` normalization/acceptance.
- P3: Write-capable operation requires disposable/resettable state and explicit point-of-risk approval immediately before the effect.

## Steps
### S1 -- Accept normalized event {#S1}
- **Do:** Capture pre-state, then submit the normalized event for acceptance.
- **Expect:** The event is validated and accepted once with its canonical identity preserved.

### S2 -- Read back accepted event {#S2}
- **Do:** Read back the accepted record and receipt.
- **Expect:** Amount/sign, source identity, and acceptance status match the request
- **Expect:** Duplicate replay is harmless or rejected deterministically.

### S3 -- Reject unsafe input {#S3}
- **Do:** Submit malformed, conflicting, duplicate, or unauthorized input.
- **Expect (negative):** Fail closed with no partial ledger/event effect.

### S4 -- Restore pre-state {#S4}
- **Do:** Roll back/compensate the declared effect and read back fresh state.
- **Expect:** Restoration is complete.
- **Expect (negative):** No duplicate or partial effect remains.

## Success criteria
- SC1: S1: The event is validated and accepted once with its canonical identity preserved.
- SC2: S2: Amount/sign, source identity, and acceptance status match the request.
- SC3: S3: Fail closed with no partial ledger/event effect.
- SC4: S4: Restoration is complete and no duplicate/partial effect remains.

## Known gaps
- G1: Runtime provider/ledger receipt and exact source contract are unavailable; do not treat this draft as execution proof. Trace evidence: Beads `orc-n2q.379.22`, including research contract `orc-n2q.379.316`. Beads artifact `local://journeys-J19-J27-beads.json` (RW-O, pre-state/approval/rollback contract). `finance_tracker/cashback_events.py` and `finance_tracker/cashback.py`.

## Delta log
- No behavior delta; structural normalization only.
