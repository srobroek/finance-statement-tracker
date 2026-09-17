---
id: J05
title: Plan corpus replay/reconciliation
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RO]
trace: []
---
## Goal
As an operator, plan deterministic replay and reconciliation of the finance corpus, identifying source identities, ordering, deduplication, and discrepancy handling without changing production data.

## Preconditions
- Identify the exact corpus snapshot, source hashes, account scope, and time window.
- Work in an isolated/read-only plan; preserve current state and receipts.

## Surfaces
Statement/transaction ingestion code under `finance_tracker/`, corpus fixtures, provider exports, provenance/reconciliation artifacts, and ledger/account views.

## Steps
1. **Do:** Inventory corpus files, source identities, hashes, and schema versions. **Expect:** Inputs are complete or each gap is explicit.
2. **Do:** Derive replay ordering, normalization, deduplication, and idempotency rules. **Expect:** Same inputs yield a deterministic plan. **Expect-negative:** No live import or mutation occurs.
3. **Do:** Compare planned outputs with current ledger/account state. **Expect:** Additions, matches, conflicts, and missing records are separately classified.
4. **Do:** Define reconciliation approval, rollback, and post-replay readback gates. **Expect:** Writes are outside RO scope and require explicit approval.

## Evidence and trace
- Beads: `orc-n2q.379.6` (author J05); review/fix/revalidation records in supplied packet.
- Source trace: `finance_tracker/` ingestion, statements, transaction semantics, and provenance artifacts.

## Definition-of-ready audit
- [x] Stable ID/title/profile; deterministic plan and no-write boundary.
- [x] Corpus, replay, reconciliation, discrepancy, and rollback steps.
- [x] Beads/source traces and explicit unknown handling.
- [ ] Authoritative corpus/live replay receipt unavailable; status is draft.
