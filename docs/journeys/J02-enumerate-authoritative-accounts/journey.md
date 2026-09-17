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
# J02 -- Enumerate authoritative accounts

## Goal
As an operator, enumerate configured finance accounts and their authoritative source, identity, and ownership without changing provider or ledger state.

## Preconditions
- P1: Read repository configuration and provider metadata only.
- P2: Redact credentials, tokens, account numbers, and transaction content.
- P3: Sources include `finance_tracker/properties.py`, `finance_tracker/platforms.py`, `finance_tracker/actual_snapshot.py`, provider/API configuration, and account-mapping evidence.

## Steps
### S1 -- Read authority declarations {#S1}
- **Do:** Read configured providers, account mappings, and source-of-truth declarations.
- **Expect:** Each account has explicit provider, identity, and authority.
- **Expect (negative):** Missing or duplicate authority is reported, not inferred.
### S2 -- Reconcile metadata {#S2}
- **Do:** Read provider/API/UI metadata for each configured account.
- **Expect:** IDs and labels reconcile with local configuration.
- **Expect (negative):** Enumeration performs no writes or syncs.
### S3 -- Classify differences {#S3}
- **Do:** Compare current metadata with captured evidence.
- **Expect:** Differences are classified as stale, drift, or unknown with timestamps.
- **Expect (negative):** A stale capture is not called current.
### S4 -- Produce inventory {#S4}
- **Do:** Produce a redacted account inventory.
- **Expect:** Inventory is reproducible and names unresolved mappings.

## Success criteria
- SC1: S1-S2 identify each account's provider, identity, and authority without writes.
- SC2: S3 classifies differences as stale, drift, or unknown with timestamps.
- SC3: S4 produces a reproducible redacted inventory naming unresolved mappings.

## Known gaps
- G1: Live provider enumeration and authoritative J01-J51 corpus are unavailable; status remains draft. Beads: `orc-n2q.379.3` author J02; review/fix contract in supplied journey Beads packet. Source refs: `finance_tracker/properties.py`, `finance_tracker/platforms.py`, `finance_tracker/actual_snapshot.py`.

## Delta log
- No behavior delta; structural normalization only.
