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
As an operator, plan deterministic replay and reconciliation of the finance corpus without changing production data. Identify source identities, ordering, deduplication, and discrepancy handling.

## Preconditions
- Identify the exact corpus snapshot and source hashes.
- Record the account scope and time window.
- Work in an isolated/read-only plan; preserve current state and receipts.

## Surfaces
- Statement and transaction ingestion code under `finance_tracker/`.
- Corpus fixtures, provider exports, and provenance or reconciliation artifacts.
- Ledger and account views.

## Steps
1. **Do:** Inventory corpus files and source identities. Record their hashes and schema versions. **Expect:** Inputs are complete or each gap is explicit.
2. **Do:** Derive replay ordering and normalization rules. Define deduplication and idempotency. **Expect:** Same inputs yield a deterministic plan. **Expect-negative:** No live import or mutation occurs.
3. **Do:** Compare planned outputs with current ledger and account state. **Expect:** Classify additions, matches, conflicts, and missing records separately.
4. **Do:** Define reconciliation approval and rollback gates. Define the post-replay readback gate. **Expect:** Writes are outside RO scope and need explicit approval.

## Evidence and trace
- Beads: `orc-n2q.379.6` (author J05); review/fix/revalidation records in supplied packet.
- Source trace: `finance_tracker/` ingestion and statement handling, transaction semantics, and provenance artifacts.

## Definition-of-ready audit
- [x] Stable ID/title/profile; deterministic plan and no-write boundary.
- [x] Corpus and replay steps. Reconciliation, discrepancy, and rollback steps.
- [x] Beads/source traces and explicit unknown handling.
- [ ] Authoritative corpus/live replay receipt unavailable; status is draft.
