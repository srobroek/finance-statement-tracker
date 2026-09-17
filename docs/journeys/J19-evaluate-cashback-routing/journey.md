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
# J19 -- Evaluate cashback routing

## Goal
As a finance user, evaluate a cashback candidate and observe the selected routing without changing ledger or provider state.

## Preconditions
- P1: Cashback candidate and account context are available.
- P2: Surfaces: Cashback Control and finance tracker routing/classification (`finance_tracker/cashback.py`, `finance_tracker/rules.py`).
- P3: Read-only evaluation; no provider, ledger, cursor, dashboard, or deployment mutation is permitted.

## Steps
### S1 -- Evaluate candidate {#S1}
- **Do:** Submit the candidate and account context to the routing evaluator.
- **Expect:** Canonical candidate identity, purchase type, and routing decision are returned deterministically.

### S2 -- Inspect routing decision {#S2}
- **Do:** Inspect the returned route and supporting match/ranking details.
- **Expect:** Decision is explainable and stable for equivalent normalized input.

### S3 -- Reject unroutable input {#S3}
- **Do:** Submit unknown, ambiguous, or malformed candidate data.
- **Expect (negative):** Evaluation rejects or reports no route; it does not invent a route or mutate state.

## Success criteria
- SC1: S1: Canonical candidate identity, purchase type, and routing decision are returned deterministically.
- SC2: S2: Decision is explainable and stable for equivalent normalized input.
- SC3: S3: Evaluation rejects or reports no route; it does not invent a route or mutate state.

## Known gaps
- G1: Authoritative J01--J51 corpus and FORMAT/INDEX history are unavailable; this is a draft reconstruction. Runtime journey evidence and exact route fixtures remain unverified. Trace evidence: Beads `orc-n2q.379.20` (author contract; RO; canonical title). Beads artifact `local://journeys-J19-J27-beads.json` (record and readiness/review evidence). `finance_tracker/cashback.py` and `finance_tracker/rules.py` are current implementation surfaces.

## Delta log
- No behavior delta; structural normalization only.
