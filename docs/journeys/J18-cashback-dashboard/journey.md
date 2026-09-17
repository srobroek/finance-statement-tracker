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

## Goal
View cashback totals and supporting transactions in a dashboard with correct filtering and no state changes.

## Preconditions
- P1: Use a fixed reporting period and cashback dataset.
- P2: Exercise dashboard/read-only surfaces and capture before/after state.

## Steps
### S1 -- Open dashboard period {#S1}
- **Do:** Open the dashboard for a known period.
- **Expect:** Totals and counts are derived from the source ledger.
### S2 -- Filter dashboard {#S2}
- **Do:** Filter by account, tag, or status.
- **Expect:** Cards, rows, and totals update consistently.
### S3 -- Open supporting transaction {#S3}
- **Do:** Open a supporting transaction.
- **Expect:** Provenance and amount match the source.
### S4 -- Exercise empty or error state {#S4}
- **Do:** Exercise an empty/error state.
- **Expect:** An explicit message appears, not fabricated zeros.
### S5 -- Refresh and repeat {#S5}
- **Do:** Refresh and repeat the view.
- **Expect:** Output is deterministic and zero writes occur.

## Success criteria
- SC1: S1-S5: Dashboard totals, counts, filters, and supporting transactions consistently reflect the source ledger.
- SC2: S1-S5: Empty/error states are explicit and all dashboard actions remain read-only.

## Known gaps
- G1: Beads author `orc-n2q.379.19`, fix `.175`, and revalidation `.227` establish title, RO profile, and independent no-mutation revalidation requirement.
- G2: Historical revision `4f115c64351b24554ec9b2ada6b0166786fda727` is draft evidence only; authoritative refs `2cd7612`/`161de41` are unavailable, so readiness is blocked.

## Delta log
