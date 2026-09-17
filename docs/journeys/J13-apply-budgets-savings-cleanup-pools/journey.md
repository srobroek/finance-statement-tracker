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
# J13 — Apply budgets/savings/cleanup pools

- **Stable ID:** J13
- **Profile:** RW-S
- **Status:** draft / blocked
- **Owner scope:** `orc-n2q.379.14`

## Goal
Apply the supported month budget, savings, and cleanup-pool operation deterministically, with preview, approval, receipt, readback, and rollback.

## Prerequisites and surfaces
Use fixed month/config/fixture identity, a captured pre-state, and the exclusive write lane. Do not infer unsupported APIs or amounts.

## Steps and assertions
1. Inspect configured month and supported operation; expect exact contract and pre-state.
2. Preview; expect deterministic proposed allocations and no mutation.
3. Approve and apply once; expect receipt with month, fixture/config hash, and affected identities.
4. Fresh readback; expect balances/pools equal approved preview.
5. Negative: unsupported operation, stale config, or replay; expect refusal/idempotency and no mutation.
6. Rollback failure path; expect exact pre-state restoration.

## Evidence and gaps
Beads author `orc-n2q.379.14` and contract dependency `orc-wk3u` identify the scope; the recorded notes require removing unsupported claims. Authoritative corpus refs `2cd7612`/`161de41` are unavailable, so this remains draft until the contract and receipts are independently confirmed.
