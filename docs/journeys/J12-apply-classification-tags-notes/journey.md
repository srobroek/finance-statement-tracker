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
# J12 — Apply classification/tags/notes

- **Stable ID:** J12
- **Profile:** RW-S
- **Status:** draft / blocked
- **Owner scope:** `orc-n2q.379.13`

## Goal
Apply approved classification, tags, and notes to in-scope imported transactions without changing unrelated ledger data.

## Prerequisites and surfaces
Pin transaction identities, source/config hash, and pre-state; use the exclusive write-serial lane and classification/ledger readback surfaces.

## Steps and assertions
1. Inspect candidates and pre-state; expect deterministic identities and explicit proposed deltas.
2. Preview; expect only approved classification/tag/note changes and no mutation.
3. Obtain consequential approval and apply once; expect receipt with target IDs, old/new values, and input hash.
4. Fresh readback; expect exact approved values, preserved provenance, and unchanged neighbors.
5. Negative: unknown identity, stale input, invalid tag, or replay; expect refusal/idempotency and no extra write.
6. Failure/rollback; expect exact pre-state restoration and linked receipt.

## Evidence and gaps
Beads validation/fix records identify the canonical title, path, RW-S profile, and exclusive-write lane (`orc-n2q.379.13`, `.65`, `.117`, `.169`, `.221`). Authoritative corpus refs `2cd7612`/`161de41` are unavailable, so source commands and final receipts remain unresolved; do not claim pass.
