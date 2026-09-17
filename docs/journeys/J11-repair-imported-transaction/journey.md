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
# J11 — Repair imported transaction

- **Stable ID:** J11
- **Profile:** RW-S
- **Status:** draft / blocked
- **Owner scope:** `orc-n2q.379.12`

## Goal
Repair one imported transaction without hiding provenance or changing unrelated ledger rows.

## Prerequisites and surfaces
Capture immutable pre-state, transaction identity, source statement hash, and exclusive write lane. Use the transaction repair surface and fresh ledger readback.

## Steps and assertions
1. Inspect the candidate and pre-state; expect exactly one identity and an explicit repair reason.
2. Preview the repair; expect only the requested field/value delta and no unrelated rows.
3. Obtain approval and apply once; expect a receipt containing target, old/new values, input hash, and run ID.
4. Read back from a fresh process; expect corrected value and unchanged provenance/neighbor rows.
5. Negative: replay or alter identity; expect rejection/idempotency with no second mutation.
6. Negative: force failure and rollback; expect exact pre-state restoration and receipt.

## Evidence and gaps
Beads author `orc-n2q.379.12` establishes title, RW-S profile, and required evidence/negative assertions. Historical draft revision `4f115c64351b24554ec9b2ada6b0166786fda727` is non-authoritative. Authoritative corpus refs `2cd7612`/`161de41` are unavailable; commands and receipts require independent validation.
