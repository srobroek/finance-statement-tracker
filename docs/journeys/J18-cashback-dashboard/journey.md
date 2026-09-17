---
id: J18
title: Cashback dashboard
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RO]
trace: []
---
# J18 -- Cashback dashboard

- **Stable ID:** J18
- **Profile:** RO
- **Status:** draft / blocked
- **Owner scope:** `orc-n2q.379.19`

## Goal
View cashback totals and supporting transactions in a dashboard with correct filtering and no state changes.

## Prerequisites and surfaces
Use a fixed reporting period and cashback dataset; exercise dashboard/read-only surfaces and capture before/after state.

## Steps and assertions
1. Open the dashboard for a known period; expect totals and counts derived from the source ledger.
2. Filter by account/tag/status; expect cards, rows, and totals to update consistently.
3. Open a supporting transaction; expect provenance and amount to match the source.
4. Exercise empty/error state; expect an explicit message, not fabricated zeros.
5. Refresh/repeat; expect deterministic output and zero writes.

## Evidence and gaps
Beads author `orc-n2q.379.19`, fix `.175`, and revalidation `.227` establish title, RO profile, and independent no-mutation revalidation requirement. Historical revision `4f115c64351b24554ec9b2ada6b0166786fda727` is draft evidence only; authoritative refs `2cd7612`/`161de41` are unavailable, so readiness is blocked.
