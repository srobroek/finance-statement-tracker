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
# Goal
As a finance user, inspect a card's cycle and history and understand the displayed transaction chronology without changing data.

## Preconditions and surfaces
- A card/account with statement-cycle history is available.
- Surfaces: finance tracker statement/history views and `finance_tracker/statements.py`.
- Read-only path; no provider, ledger, cursor, or dashboard state may change.

## Journey
1. **Do:** Choose a card and request its cycle/history.
   **Expect:** The applicable cycle and transactions are shown with stable ordering and identity.
2. **Do:** Move between available cycles and inspect a transaction.
   **Expect:** The selected cycle remains explicit and history remains consistent with source records.
3. **Expect-negative:** Request an unknown card, invalid cycle, or malformed identifier.
   **Expect:** The request is rejected or returns an empty/explicit unavailable result without fallback mutation.

## Evidence
- Beads `orc-n2q.379.21` (author contract; RO; canonical title).
- Beads artifact `local://journeys-J19-J27-beads.json`.
- Current statement/history surfaces: `finance_tracker/statements.py`, `finance_tracker/history.py`.

## Known gaps
- Authoritative journey corpus, exact cycle fixtures, and runtime readback are unavailable; draft remains unvalidated.
