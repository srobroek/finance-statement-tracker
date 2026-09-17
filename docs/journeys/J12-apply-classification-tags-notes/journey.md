---
id: J12
title: Apply classification/tags/notes
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RW-S]
trace: []
---
# J12 -- Apply classification/tags/notes

## Goal
Apply approved classification, tags, and notes to in-scope imported transactions without changing unrelated ledger data.

## Preconditions
- P1: Pin transaction identities, source/config hash, and pre-state.
- P2: Use the exclusive write-serial lane and classification/ledger readback surfaces.

## Steps
### S1 -- Inspect candidates and pre-state {#S1}
- **Do:** Inspect candidates and pre-state.
- **Expect:** Deterministic identities and explicit proposed deltas are recorded.
### S2 -- Preview approved changes {#S2}
- **Do:** Preview the changes.
- **Expect:** Only approved classification/tag/note changes are proposed and no mutation occurs.
### S3 -- Approve and apply changes {#S3}
- **Do:** Obtain consequential approval and apply once.
- **Expect:** A receipt contains target IDs, old/new values, and input hash.
### S4 -- Read back changes {#S4}
- **Do:** Perform fresh readback.
- **Expect:** Exact approved values and preserved provenance are present; neighbors are unchanged.
### S5 -- Reject invalid or replayed input {#S5}
- **Do:** Submit an unknown identity, stale input, invalid tag, or replay.
- **Expect:** Refusal or idempotency occurs with no extra write.
### S6 -- Roll back failure {#S6}
- **Do:** Exercise failure and rollback.
- **Expect:** Exact pre-state restoration and a linked receipt occur.

## Success criteria
- SC1: S1-S6: Approved classification, tag, and note changes are applied once with provenance preserved.
- SC2: S1-S6: Invalid/replayed input and failure rollback cause no unintended writes.

## Known gaps
- G1: Beads validation/fix records identify the canonical title, path, RW-S profile, and exclusive-write lane (`orc-n2q.379.13`, `.65`, `.117`, `.169`, `.221`).
- G2: Authoritative corpus refs `2cd7612`/`161de41` are unavailable, so source commands and final receipts remain unresolved; do not claim pass.

## Delta log
