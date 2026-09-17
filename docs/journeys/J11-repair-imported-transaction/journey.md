---
id: J11
title: Repair imported transaction
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RW-S]
trace: []
---
# J11 -- Repair imported transaction

## Goal
Repair one imported transaction without hiding provenance or changing unrelated ledger rows.

## Preconditions
- P1: Capture immutable pre-state, transaction identity, and source statement hash.
- P2: Use the transaction repair surface and fresh ledger readback.
- P3: Run in the exclusive write lane.

## Steps
### S1 -- Inspect candidate and pre-state {#S1}
- **Do:** Inspect the candidate and pre-state.
- **Expect:** Exactly one identity and an explicit repair reason are recorded.
### S2 -- Preview repair {#S2}
- **Do:** Preview the repair.
- **Expect:** Only the requested field/value delta is proposed and no unrelated rows change.
### S3 -- Approve and apply repair {#S3}
- **Do:** Obtain approval and apply once.
- **Expect:** A receipt contains target, old/new values, input hash, and run ID.
### S4 -- Read back repaired transaction {#S4}
- **Do:** Read back from a fresh process.
- **Expect:** The corrected value is present and provenance/neighbor rows are unchanged.
### S5 -- Prevent replay or identity alteration {#S5}
- **Do:** Replay or alter the identity.
- **Expect:** Rejection or idempotency occurs with no second mutation.
### S6 -- Roll back failure {#S6}
- **Do:** Force failure and rollback.
- **Expect:** Exact pre-state restoration and a receipt occur.

## Success criteria
- SC1: S1-S6: The requested transaction repair is applied once while provenance and unrelated rows remain unchanged.
- SC2: S1-S6: Replay protection and rollback restore the expected state.

## Known gaps
- G1: Beads author `orc-n2q.379.12` establishes title, RW-S profile, and required evidence/negative assertions.
- G2: Historical draft revision `4f115c64351b24554ec9b2ada6b0166786fda727` is non-authoritative. Authoritative corpus refs `2cd7612`/`161de41` are unavailable; commands and receipts require independent validation.

## Delta log
