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
## Goal
As an operator, prove that provider, API, and UI balances agree for the selected account and period, with discrepancies observable and no mutation.

## Preconditions
- Select one account, currency, period, and timestamped read window.
- Capture provider/API/UI responses without credentials or transaction secrets.

## Surfaces
Actual/provider API, finance integration adapters, UI readback, cursor/ledger views, and `finance_tracker/` balance/statement code.

## Steps
1. **Do:** Read provider balance and currency/account identity. **Expect:** Balance, currency, timestamp, and source are explicit.
2. **Do:** Read the API representation for the same identity/window. **Expect:** Values and precision reconcile or produce a quantified discrepancy. **Expect-negative:** Pagination/cursor errors fail closed.
3. **Do:** Read the UI value and selected account/window. **Expect:** UI selection and displayed balance are observable and traceable to API data.
4. **Do:** Compare all three using documented precision, FX, rounding, and tolerance rules. **Expect:** PASS only when parity is within the rule; otherwise discrepancy is retained. **Expect-negative:** No repair, write, sync, or cursor advancement occurs.
5. **Do:** Re-read after any transient failure. **Expect:** Repeated evidence is timestamped and bounded.

## Evidence and trace
- Beads: `orc-n2q.379.4` (author J03), `orc-n2q.379.56` review, `orc-n2q.379.296` fix/research contract.
- Source trace: `finance_tracker/actual_pipeline.py`, `finance_tracker/actual_snapshot.py`, provider integration/API/UI surfaces.

## Definition-of-ready audit
- [x] Stable identity, ordered S1-style observable steps, RO/no-mutation guards, parity rules.
- [x] Discrepancy and transport/cursor failure branches.
- [x] Evidence references recorded.
- [ ] Live provider/API/UI readback and authoritative corpus unavailable; status is draft.
