---
id: J13
title: Apply budgets/savings/cleanup pools
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RW-S]
trace: []
---
# J13 -- Apply budgets/savings/cleanup pools

## Goal
Apply the supported month budget, savings, and cleanup-pool operation deterministically, with preview, approval, receipt, readback, and rollback.

## Preconditions
- P1: Use fixed month/config/fixture identity and a captured pre-state.
- P2: Use the exclusive write lane.
- P3: Do not infer unsupported APIs or amounts.

## Steps
### S1 -- Inspect configured operation {#S1}
- **Do:** Inspect the configured month and supported operation.
- **Expect:** The exact contract and pre-state are recorded.
### S2 -- Preview operation {#S2}
- **Do:** Preview the operation.
- **Expect:** Deterministic proposed allocations are shown and no mutation occurs.
### S3 -- Approve and apply operation {#S3}
- **Do:** Approve and apply once.
- **Expect:** A receipt identifies month, fixture/config hash, and affected identities.
### S4 -- Read back operation {#S4}
- **Do:** Perform fresh readback.
- **Expect:** Balances and pools equal the approved preview.
### S5 -- Reject unsupported or stale input {#S5}
- **Do:** Submit an unsupported operation, stale config, or replay.
- **Expect:** Refusal or idempotency occurs with no mutation.
### S6 -- Roll back failure path {#S6}
- **Do:** Exercise the failure path and rollback.
- **Expect:** Exact pre-state restoration occurs.

## Success criteria
- SC1: S1-S6: The supported operation is applied once and readback equals the approved preview.
- SC2: S1-S6: Unsupported, stale, or replayed input cannot mutate state; rollback restores pre-state.

## Known gaps
- G1: Beads author `orc-n2q.379.14` and contract dependency `orc-wk3u` identify the scope; recorded notes require removing unsupported claims.
- G2: Authoritative corpus refs `2cd7612`/`161de41` are unavailable, so this remains draft until the contract and receipts are independently confirmed.

## Delta log
