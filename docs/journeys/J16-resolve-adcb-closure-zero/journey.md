---
id: J16
title: Resolve ADCB closure/zero
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RW-S]
trace: []
---
# J16 — Resolve ADCB closure/zero

- **Stable ID:** J16
- **Profile:** RW-S
- **Status:** draft / blocked
- **Owner scope:** `orc-n2q.379.17`

## Goal
Resolve an ADCB closure/zero state while preserving account identity, closure semantics, provenance, and rollback safety.

## Prerequisites and surfaces
Pin the ADCB account and closure evidence, capture pre-state, and use the exclusive write lane with account and ledger readback.

## Steps and assertions
1. Inspect account identity, closure evidence, and current zero state; expect deterministic target.
2. Preview the closure/zero correction; expect only the named ADCB account to change.
3. Approve and apply once; expect receipt with old/new state and evidence hash.
4. Fresh readback; expect closed/zero semantics and unchanged unrelated accounts.
5. Negative: wrong identity, nonzero balance, stale evidence, or replay; expect refusal/idempotency and no mutation.
6. Restore on failure; expect exact pre-state and linked receipt.

## Evidence and gaps
Beads author `orc-n2q.379.17` establishes the stable title and RW-S profile. A dangling historical draft was reported, but authoritative refs `2cd7612`/`161de41` are unavailable. Exact source semantics and validation receipts remain unresolved.
