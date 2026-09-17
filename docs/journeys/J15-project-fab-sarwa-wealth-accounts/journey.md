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
# J15 — Project FAB/Sarwa wealth accounts

- **Stable ID:** J15
- **Profile:** RW-S
- **Status:** draft / blocked
- **Owner scope:** `orc-n2q.379.16`

## Goal
Project FAB and Sarwa wealth-account data into the supported finance view without changing source truth or unrelated accounts.

## Prerequisites and surfaces
Pin account identities, period, source hash, and pre-state; use the exclusive write lane and the wealth projection/readback surfaces.

## Steps and assertions
1. Inspect source accounts and mapping; expect explicit identity and no ambiguous account.
2. Preview projection; expect deterministic rows and no mutation.
3. Approve and apply once; expect receipt with source/config hashes and affected accounts.
4. Fresh readback; expect projected balances/holdings match the approved preview and source provenance remains intact.
5. Negative: unknown account, stale source, or replay; expect refusal/idempotency and no duplicates.
6. Rollback; expect exact pre-state and auditable restore receipt.

## Evidence and gaps
Beads author `orc-n2q.379.16` establishes title/profile and RW-S controls. Historical revision `4f115c64351b24554ec9b2ada6b0166786fda727` is draft evidence only. Authoritative refs `2cd7612`/`161de41` are unavailable; validation remains blocked.
