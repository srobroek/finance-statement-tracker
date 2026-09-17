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
# J04 -- Plan bootstrap

## Goal
As an operator, plan an Actual bootstrap from current configuration and evidence, identifying prerequisites and exact write boundaries without executing it.

## Preconditions
- P1: Read bootstrap configuration, project identity, account mappings, and existing state.
- P2: No bootstrap/import/sync command may run in this journey.
- P3: Surfaces include `finance_tracker/actual_pipeline.py`, `finance_tracker/actual_snapshot.py`, `integrations/actual/`, bootstrap configuration, and deployment/runbook evidence.

## Steps
### S1 -- Inspect prerequisites {#S1}
- **Do:** Inspect current project/account identity and connectivity prerequisites.
- **Expect:** Required values and missing prerequisites are listed.
### S2 -- Derive plan {#S2}
- **Do:** Derive ordered bootstrap plan and candidate inputs.
- **Expect:** Plan separates read-only checks from write-capable actions and names pre-state capture.
### S3 -- Review write gates {#S3}
- **Do:** Review write boundary, approval, rollback, and post-bootstrap readback requirements.
- **Expect:** Consequential actions require explicit approval and rollback evidence.
- **Expect (negative):** Planning does not mutate Actual.
### S4 -- Record readiness {#S4}
- **Do:** Record blockers and readiness outcome.
- **Expect:** Unknown provider/runtime evidence leaves the journey draft.

## Success criteria
- SC1: S1 lists required identity/connectivity values and missing prerequisites.
- SC2: S2-S3 produce an ordered plan with pre-state, approval, rollback, and readback gates without mutation.
- SC3: S4 records blockers and keeps status draft when evidence is unknown.

## Known gaps
- G1: Bootstrap runtime contract/readback is unavailable; status remains draft. Beads: `orc-n2q.379.5` author J04; review/fix `orc-n2q.379.57`/`orc-n2q.379.161`. Source refs: `finance_tracker/actual_pipeline.py`, `finance_tracker/actual_snapshot.py`, `integrations/actual/README.md` where present.

## Delta log
- No behavior delta; structural normalization only.
