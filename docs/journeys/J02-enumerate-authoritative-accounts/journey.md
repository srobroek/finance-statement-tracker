---
id: J02
title: Enumerate authoritative accounts
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RO]
trace: []
---
## Goal
As an operator, enumerate configured finance accounts and their authoritative source, identity, and ownership without changing provider or ledger state.

## Preconditions
- Read repository configuration and provider metadata only.
- Redact credentials, tokens, account numbers, and transaction content.

## Surfaces
`finance_tracker/properties.py`, `finance_tracker/platforms.py`, `finance_tracker/actual_snapshot.py`, provider/API configuration, and account-mapping evidence.

## Steps
1. **Do:** Read configured providers, account mappings, and source-of-truth declarations. **Expect:** Each account has an explicit provider, identity, and authority. **Expect-negative:** Missing or duplicate authority is reported, not inferred.
2. **Do:** Read provider/API/UI metadata for each configured account. **Expect:** IDs and labels reconcile with local configuration. **Expect-negative:** Enumeration performs no writes or syncs.
3. **Do:** Compare current metadata with captured evidence. **Expect:** Differences are classified as stale, drift, or unknown with timestamps. **Expect-negative:** A stale capture is not called current.
4. **Do:** Produce a redacted account inventory. **Expect:** Inventory is reproducible and names unresolved mappings.

## Evidence and trace
- Beads: `orc-n2q.379.3` (author J02); review/fix contract in supplied journey Beads packet.
- Source trace: `finance_tracker/properties.py`, `finance_tracker/platforms.py`, `finance_tracker/actual_snapshot.py`.

## Definition-of-ready audit
- [x] Stable ID/title/profile, RO boundary, prerequisites, surfaces, and observable steps.
- [x] Explicit unknown/duplicate and no-write branches.
- [x] Source and Beads traces recorded.
- [ ] Live provider enumeration and authoritative J01-J51 corpus are unavailable; status is draft.
