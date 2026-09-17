---
id: J04
title: Plan bootstrap
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RO]
trace: []
---
## Goal
As an operator, plan an Actual bootstrap from current configuration and evidence, identifying prerequisites and exact write boundaries without executing it.

## Preconditions
- Read bootstrap configuration, project identity, account mappings, and existing state.
- No bootstrap/import/sync command may run in this journey.

## Surfaces
`finance_tracker/actual_pipeline.py`, `finance_tracker/actual_snapshot.py`, `integrations/actual/`, bootstrap configuration, and deployment/runbook evidence.

## Steps
1. **Do:** Inspect current project/account identity and connectivity prerequisites. **Expect:** Required values and missing prerequisites are listed.
2. **Do:** Derive the ordered bootstrap plan and candidate inputs. **Expect:** Plan separates read-only checks from write-capable actions and names pre-state capture.
3. **Do:** Review write boundary, approval, rollback, and post-bootstrap readback requirements. **Expect:** Consequential actions require explicit approval and rollback evidence. **Expect-negative:** Planning does not mutate Actual.
4. **Do:** Record blockers and readiness outcome. **Expect:** Unknown provider/runtime evidence leaves the journey draft.

## Evidence and trace
- Beads: `orc-n2q.379.5` (author J04); review/fix record in supplied packet (`orc-n2q.379.57`/`orc-n2q.379.161`).
- Source trace: `finance_tracker/actual_pipeline.py`, `finance_tracker/actual_snapshot.py`, `integrations/actual/README.md` where present.

## Definition-of-ready audit
- [x] Stable ID/title/profile and read-only planning boundary.
- [x] Prerequisites, interfaces, ordered steps, approval/rollback/no-write assertions.
- [x] Trace references and blockers.
- [ ] Bootstrap runtime contract/readback is unavailable; status is draft.
