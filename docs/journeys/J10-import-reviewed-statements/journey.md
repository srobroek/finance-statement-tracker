---
id: J10
title: Import reviewed statements
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RW-S]
trace: []
---
# J10 -- Import reviewed statements

## Goal
As an operator, import a reviewed statement into the finance ledger so accepted rows are applied deterministically, with a pre-state, approval, receipt, fresh readback, and reversible rollback boundary.

## Preconditions
- P1: Reviewed statement fixture and fixed account/period scope are available.
- P2: Use the statement import surface and ledger readback; do not alter shared journey package files.
- P3: Capture a pre-state and run in the exclusive write-serial lane.

## Steps
### S1 -- Inspect fixture, identity, and pre-state {#S1}
- **Do:** Inspect the fixture, target identity, and pre-state.
- **Expect:** Scope, row count, and hash are recorded; no mutation occurs.
### S2 -- Preview reviewed import {#S2}
- **Do:** Preview the reviewed import.
- **Expect:** Only approved rows are proposed and duplicates/out-of-scope rows are rejected.
### S3 -- Approve and apply import {#S3}
- **Do:** Obtain consequential approval, then apply once.
- **Expect:** A receipt identifies target, input hash, changes, and run identity.
### S4 -- Read back ledger {#S4}
- **Do:** Perform fresh ledger readback.
- **Expect:** Applied rows and balances match the approved preview.
### S5 -- Prevent replay mutation {#S5}
- **Do:** Replay the same input or change its identity.
- **Expect:** Replay is rejected or idempotent; no duplicate writes occur.
### S6 -- Exercise rollback boundary {#S6}
- **Do:** Exercise the failure/rollback boundary.
- **Expect:** Rollback restores the recorded pre-state and leaves an auditable receipt.

## Success criteria
- SC1: S1-S6: Approved rows are applied once and fresh readback matches the approved preview.
- SC2: S1-S6: Replay and rollback preserve ledger integrity and auditable receipts.

## Known gaps
- G1: Beads author record `orc-n2q.379.11` supplies the stable title/profile and required sections; historical draft evidence is revision `4f115c64351b24554ec9b2ada6b0166786fda727`.
- G2: The authoritative J01--J51 corpus refs `2cd7612`/`161de41` are unavailable, so exact source commands and final validation receipts remain unresolved. Do not claim pass until independently validated.

## Delta log
