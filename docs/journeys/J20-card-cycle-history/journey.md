---
id: J20
title: Card cycle/history
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [cashback-control]
interfaces: [RO]
trace: []
---
# J20 -- Card cycle/history

## Goal
As a finance user, inspect a card's cycle and history and understand the displayed transaction chronology without changing data.

## Preconditions
- P1: A card/account with statement-cycle history is available.
- P2: Surfaces: finance tracker statement/history views and `finance_tracker/statements.py`.
- P3: Read-only path; no provider, ledger, cursor, or dashboard state may change.

## Steps
### S1 -- Select card cycle {#S1}
- **Do:** Choose a card and request its cycle/history.
- **Expect:** The applicable cycle and transactions are shown with stable ordering and identity.

### S2 -- Inspect cycle history {#S2}
- **Do:** Move between available cycles and inspect a transaction.
- **Expect:** The selected cycle remains explicit and history remains consistent with source records.

### S3 -- Reject invalid selection {#S3}
- **Do:** Request an unknown card, invalid cycle, or malformed identifier.
- **Expect (negative):** The request is rejected or returns an empty/explicit unavailable result without fallback mutation.

## Success criteria
- SC1: S1: The applicable cycle and transactions are shown with stable ordering and identity.
- SC2: S2: The selected cycle remains explicit and history remains consistent with source records.
- SC3: S3: The request is rejected or returns an empty/explicit unavailable result without fallback mutation.

## Known gaps
- G1: Authoritative journey corpus, exact cycle fixtures, and runtime readback are unavailable; draft remains unvalidated. Trace evidence: Beads `orc-n2q.379.21` (author contract; RO; canonical title). Beads artifact `local://journeys-J19-J27-beads.json`. Current statement/history surfaces: `finance_tracker/statements.py`, `finance_tracker/history.py`.

## Delta log
- No behavior delta; structural normalization only.
