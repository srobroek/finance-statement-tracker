---
id: J06
title: Review manual-state preservation
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RO]
trace: []
---
# J06 -- Review manual-state preservation

## Goal
As an operator, review whether manually maintained state survives read-only inspection and proposed reconciliation, with before/after evidence and no unintended writes.

## Preconditions
- P1: Capture a redacted pre-state fingerprint for manual fields, cursors, settings, and ledger markers.
- P2: Select the exact review scope and current implementation.
- P3: Surfaces include manual-state/configuration files, cursor/settings views, ledger/account readbacks, `finance_tracker/` readers, and reconciliation evidence.

## Steps
### S1 -- Capture pre-state {#S1}
- **Do:** Capture pre-state and ownership of manual fields.
- **Expect:** Every field has a stable identity and source.
### S2 -- Perform read-only review {#S2}
- **Do:** Perform the scoped read-only review.
- **Expect:** Manual values, cursors, and settings remain unchanged.
- **Expect (negative):** No migration, normalization, or auto-repair runs.
### S3 -- Compare fingerprints {#S3}
- **Do:** Compare post-state fingerprint with pre-state.
- **Expect:** Exact equality or a documented externally caused delta.
### S4 -- Record blockers {#S4}
- **Do:** Record findings and blockers.
- **Expect:** Any unexplained delta fails readiness; no silent conversion to pass.

## Success criteria
- SC1: S1 identifies every reviewed manual field and its owner.
- SC2: S2-S3 prove exact preservation or document an external delta without writes.
- SC3: S4 records unexplained deltas as blockers.

## Known gaps
- G1: Current runtime readback and authoritative corpus are unavailable; status remains draft. Beads: `orc-n2q.379.7` author J06, review `orc-n2q.379.59`, and associated fix/revalidation records. Source refs: `finance_tracker/actual_snapshot.py`, `finance_tracker/actual_pipeline.py`, configuration/cursor/ledger surfaces.

## Delta log
- No behavior delta; structural normalization only.
