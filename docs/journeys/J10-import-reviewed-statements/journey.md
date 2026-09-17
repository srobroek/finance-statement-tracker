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
# J10 — Import reviewed statements

- **Stable ID:** J10
- **Profile:** RW-S
- **Status:** draft / blocked
- **Owner scope:** `orc-n2q.379.11`

## Goal
As an operator, import a reviewed statement into the finance ledger so accepted rows are applied deterministically, with a pre-state, approval, receipt, fresh readback, and reversible rollback boundary.

## Prerequisites and surfaces
- Reviewed statement fixture and fixed account/period scope are available.
- Use the statement import surface and ledger readback; do not alter shared journey package files.
- Capture a pre-state and run in the exclusive write-serial lane.

## Steps and assertions
1. **Do:** inspect the fixture, target identity, and pre-state. **Expect:** scope, row count, and hash are recorded; no mutation occurs.
2. **Do:** preview the reviewed import. **Expect:** only approved rows are proposed and duplicates/out-of-scope rows are rejected.
3. **Do:** obtain consequential approval, then apply once. **Expect:** a receipt identifies target, input hash, changes, and run identity.
4. **Do:** perform fresh ledger readback. **Expect:** applied rows and balances match the approved preview.
5. **Do (negative):** replay the same input or change its identity. **Expect:** replay is rejected or idempotent; no duplicate writes occur.
6. **Do (negative):** exercise failure/rollback boundary. **Expect:** rollback restores the recorded pre-state and leaves an auditable receipt.

## Evidence and gaps
Beads author record `orc-n2q.379.11` supplies the stable title/profile and required sections; historical draft evidence is revision `4f115c64351b24554ec9b2ada6b0166786fda727`. The authoritative J01–J51 corpus refs `2cd7612`/`161de41` are unavailable, so exact source commands and final validation receipts remain unresolved. Do not claim pass until independently validated.
