---
id: J15
title: Project FAB/Sarwa wealth accounts
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RW-S]
trace: []
---
# J15 -- Project FAB/Sarwa wealth accounts

## Goal
Project FAB and Sarwa wealth-account data into the supported finance view without changing source truth or unrelated accounts.

## Preconditions
- P1: Pin account identities, period, source hash, and pre-state.
- P2: Use the exclusive write lane and wealth projection/readback surfaces.

## Steps
### S1 -- Inspect source accounts and mapping {#S1}
- **Do:** Inspect source accounts and mapping.
- **Expect:** Explicit identity is recorded and no account is ambiguous.
### S2 -- Preview projection {#S2}
- **Do:** Preview the projection.
- **Expect:** Deterministic rows are shown and no mutation occurs.
### S3 -- Approve and apply projection {#S3}
- **Do:** Approve and apply once.
- **Expect:** A receipt contains source/config hashes and affected accounts.
### S4 -- Read back projection {#S4}
- **Do:** Perform fresh readback.
- **Expect:** Projected balances/holdings match the approved preview and source provenance remains intact.
### S5 -- Reject invalid or replayed projection {#S5}
- **Do:** Submit an unknown account, stale source, or replay.
- **Expect:** Refusal or idempotency occurs with no duplicates.
### S6 -- Roll back projection {#S6}
- **Do:** Roll back the projection.
- **Expect:** Exact pre-state and an auditable restore receipt are present.

## Success criteria
- SC1: S1-S6: Approved FAB/Sarwa projection is applied once with source truth and provenance preserved.
- SC2: S1-S6: Invalid or replayed input cannot create duplicates and rollback is exact.

## Known gaps
- G1: Beads author `orc-n2q.379.16` establishes title/profile and RW-S controls.
- G2: Historical revision `4f115c64351b24554ec9b2ada6b0166786fda727` is draft evidence only. Authoritative refs `2cd7612`/`161de41` are unavailable; validation remains blocked.

## Delta log
