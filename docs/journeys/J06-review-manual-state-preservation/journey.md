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
## Goal
As an operator, review whether manually maintained state survives read-only inspection and proposed reconciliation, with before/after evidence and no unintended writes.

## Preconditions
- Capture a redacted pre-state fingerprint for manual fields, cursors, settings, and ledger markers.
- Select the exact review scope and current implementation.

## Surfaces
Manual-state/configuration files, cursor/settings views, ledger/account readbacks, `finance_tracker/` readers, and reconciliation evidence.

## Steps
1. **Do:** Capture pre-state and ownership of manual fields. **Expect:** Every field has a stable identity and source.
2. **Do:** Perform the scoped read-only review. **Expect:** Manual values, cursors, and settings remain unchanged. **Expect-negative:** No migration, normalization, or auto-repair runs.
3. **Do:** Compare post-state fingerprint with pre-state. **Expect:** Exact equality or a documented externally caused delta.
4. **Do:** Record findings and blockers. **Expect:** Any unexplained delta fails readiness; no silent conversion to pass.

## Evidence and trace
- Beads: `orc-n2q.379.7` (author J06), review `orc-n2q.379.59` and associated J06 fix/revalidation records.
- Source trace: `finance_tracker/actual_snapshot.py`, `finance_tracker/actual_pipeline.py`, configuration/cursor/ledger surfaces.

## Definition-of-ready audit
- [x] Stable RO identity, pre/post fingerprint, ordered steps, no-write assertions.
- [x] Manual-state and cursor/settings preservation explicitly observable.
- [x] Traces and failure handling recorded.
- [ ] Current runtime readback and authoritative corpus unavailable; status is draft.
