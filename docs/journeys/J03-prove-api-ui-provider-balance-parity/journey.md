---
id: J03
title: Prove API/UI/provider balance parity
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RO]
trace: []
---
# J03 -- Prove API/UI/provider balance parity

## Goal
As an operator, prove that provider, API, and UI balances agree for the selected account and period, with discrepancies observable and no mutation.

## Preconditions
- P1: Select one account, currency, period, and timestamped read window.
- P2: Capture provider/API/UI responses without credentials or transaction secrets.
- P3: Surfaces include provider API, finance adapters, UI readback, cursor/ledger views, and `finance_tracker/` balance/statement code.

## Steps
### S1 -- Read provider balance {#S1}
- **Do:** Read provider balance and currency/account identity.
- **Expect:** Balance, currency, timestamp, and source are explicit.
### S2 -- Read API balance {#S2}
- **Do:** Read API representation for the same identity/window.
- **Expect:** Values and precision reconcile or produce a quantified discrepancy.
- **Expect (negative):** Pagination/cursor errors fail closed.
### S3 -- Read UI balance {#S3}
- **Do:** Read UI value and selected account/window.
- **Expect:** UI selection and displayed balance are observable and traceable to API data.
### S4 -- Compare balances {#S4}
- **Do:** Compare all three using documented precision, FX, rounding, and tolerance rules.
- **Expect:** PASS only when parity is within the rule; otherwise discrepancy is retained.
- **Expect (negative):** No repair, write, sync, or cursor advancement occurs.
### S5 -- Bound transient reread {#S5}
- **Do:** Re-read after any transient failure.
- **Expect:** Repeated evidence is timestamped and bounded.

## Success criteria
- SC1: S1-S3 provide timestamped provider, API, and UI values for one identity/window.
- SC2: S4 yields PASS only within documented tolerance, or retains a quantified discrepancy.
- SC3: S2-S5 fail closed and perform no repair, write, sync, or unbounded retry.

## Known gaps
- G1: Live provider/API/UI readback and authoritative corpus are unavailable; status remains draft. Beads: `orc-n2q.379.4` author J03, `orc-n2q.379.56` review, `orc-n2q.379.296` fix/research contract. Source refs: `finance_tracker/actual_pipeline.py`, `finance_tracker/actual_snapshot.py`, provider integration/API/UI surfaces.

## Delta log
- No behavior delta; structural normalization only.
