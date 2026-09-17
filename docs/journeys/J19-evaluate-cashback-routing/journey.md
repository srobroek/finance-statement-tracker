---
id: J19
title: Evaluate cashback routing
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [cashback-control]
interfaces: [RO]
trace: []
---
# Goal
As a finance user, evaluate a cashback candidate and observe the selected routing without changing ledger or provider state.

## Preconditions and surfaces
- Cashback candidate and account context are available.
- Surfaces: Cashback Control and finance tracker routing/classification (`finance_tracker/cashback.py`, `finance_tracker/rules.py`).
- Read-only evaluation; no provider, ledger, cursor, dashboard, or deployment mutation is permitted.

## Journey
1. **Do:** Submit the candidate and account context to the routing evaluator.
   **Expect:** Canonical candidate identity, purchase type, and routing decision are returned deterministically.
2. **Do:** Inspect the returned route and supporting match/ranking details.
   **Expect:** Decision is explainable and stable for equivalent normalized input.
3. **Expect-negative:** Submit unknown, ambiguous, or malformed candidate data.
   **Expect:** Evaluation rejects or reports no route; it does not invent a route or mutate state.

## Evidence
- Beads `orc-n2q.379.20` (author contract; RO; canonical title).
- Beads artifact `local://journeys-J19-J27-beads.json` (record and readiness/review evidence).
- `finance_tracker/cashback.py` and `finance_tracker/rules.py` are current implementation surfaces.

## Known gaps
- Authoritative J01--J51 corpus and FORMAT/INDEX history are unavailable; this is a draft reconstruction. Runtime journey evidence and exact route fixtures remain unverified.
