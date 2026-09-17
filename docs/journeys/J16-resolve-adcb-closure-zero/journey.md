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
# J16 -- Resolve ADCB closure/zero

## Goal
Resolve an ADCB closure/zero state while preserving account identity, closure semantics, provenance, and rollback safety.

## Preconditions
- P1: Pin the ADCB account and closure evidence.
- P2: Capture pre-state.
- P3: Use the exclusive write lane with account and ledger readback.

## Steps
### S1 -- Inspect ADCB closure state {#S1}
- **Do:** Inspect account identity, closure evidence, and current zero state.
- **Expect:** A deterministic target is identified.
### S2 -- Preview closure correction {#S2}
- **Do:** Preview the closure/zero correction.
- **Expect:** Only the named ADCB account is proposed to change.
### S3 -- Approve and apply correction {#S3}
- **Do:** Approve and apply once.
- **Expect:** A receipt contains old/new state and evidence hash.
### S4 -- Read back account state {#S4}
- **Do:** Perform fresh readback.
- **Expect:** Closed/zero semantics are present and unrelated accounts are unchanged.
### S5 -- Reject unsafe or replayed correction {#S5}
- **Do:** Submit a wrong identity, nonzero balance, stale evidence, or replay.
- **Expect:** Refusal or idempotency occurs with no mutation.
### S6 -- Restore on failure {#S6}
- **Do:** Restore after failure.
- **Expect:** Exact pre-state and a linked receipt are present.

## Success criteria
- SC1: S1-S6: The named ADCB account reaches the supported closed/zero state without changing unrelated accounts.
- SC2: S1-S6: Unsafe input is refused and failure restore is exact and auditable.

## Known gaps
- G1: Beads author `orc-n2q.379.17` establishes the stable title and RW-S profile.
- G2: A dangling historical draft was reported, but authoritative refs `2cd7612`/`161de41` are unavailable. Exact source semantics and validation receipts remain unresolved.

## Delta log
