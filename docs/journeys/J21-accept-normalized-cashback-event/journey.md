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
# Goal
As the finance pipeline, accept one normalized cashback event, preserve its identity and semantics, and expose the resulting accepted event safely.

## Preconditions and surfaces
- A normalized event with source identity, amount, currency, and account context is available.
- Surfaces: Cashback Control and `finance_tracker/cashback_events.py` normalization/acceptance.
- Write-capable operation requires disposable/resettable state and explicit point-of-risk approval immediately before the effect.

## Journey
1. **Do:** Capture pre-state, then submit the normalized event for acceptance.
   **Expect:** The event is validated and accepted once with its canonical identity preserved.
2. **Do:** Read back the accepted record and receipt.
   **Expect:** Amount/sign, source identity, and acceptance status match the request; duplicate replay is harmless or rejected deterministically.
3. **Expect-negative:** Submit malformed, conflicting, duplicate, or unauthorized input.
   **Expect:** Fail closed with no partial ledger/event effect.
4. **Do:** Roll back/compensate the declared effect and read back fresh state.
   **Expect:** Restoration is complete and no duplicate/partial effect remains.

## Evidence
- Beads `orc-n2q.379.22`, including research contract `orc-n2q.379.316`.
- Beads artifact `local://journeys-J19-J27-beads.json` (RW-O, pre-state/approval/rollback contract).
- `finance_tracker/cashback_events.py` and `finance_tracker/cashback.py`.

## Known gaps
- Runtime provider/ledger receipt and exact source contract are unavailable; do not treat this draft as execution proof.
