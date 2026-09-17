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
# J05 -- Plan corpus replay/reconciliation

## Goal
As an operator, plan deterministic replay and reconciliation of the finance corpus without changing production data. Identify source identities, ordering, deduplication, and discrepancy handling.

## Preconditions
- P1: Identify exact corpus snapshot and source hashes.
- P2: Record account scope and time window.
- P3: Work in an isolated/read-only plan; preserve current state and receipts.
- P4: Surfaces include statement/transaction ingestion under `finance_tracker/`, corpus fixtures, provider exports, provenance/reconciliation artifacts, and ledger/account views.

## Steps
### S1 -- Inventory corpus {#S1}
- **Do:** Inventory corpus files and source identities; record hashes and schema versions.
- **Expect:** Inputs are complete or each gap is explicit.
### S2 -- Derive replay rules {#S2}
- **Do:** Derive replay ordering and normalization rules; define deduplication and idempotency.
- **Expect:** Same inputs yield a deterministic plan.
- **Expect (negative):** No live import or mutation occurs.
### S3 -- Compare planned state {#S3}
- **Do:** Compare planned outputs with current ledger and account state.
- **Expect:** Additions, matches, conflicts, and missing records are classified separately.
### S4 -- Define reconciliation gates {#S4}
- **Do:** Define reconciliation approval and rollback gates and post-replay readback gate.
- **Expect:** Writes are outside RO scope and need explicit approval.

## Success criteria
- SC1: S1 records source identities, hashes, schemas, or explicit gaps.
- SC2: S2 produces deterministic replay and idempotency rules without mutation.
- SC3: S3-S4 classify discrepancies and define approval, rollback, and readback gates.

## Known gaps
- G1: Authoritative corpus/live replay receipt is unavailable; status remains draft. Beads: `orc-n2q.379.6` author J05; review/fix/revalidation records in supplied packet. Source refs: `finance_tracker/` ingestion, statement handling, transaction semantics, and provenance artifacts.

## Delta log
- No behavior delta; structural normalization only.
