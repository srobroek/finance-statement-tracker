---
id: J09
title: Apply Actual bootstrap
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RW-S]
trace: []
---
# J09 -- Apply Actual bootstrap

## Goal
As an authorized operator, apply a reviewed Actual bootstrap exactly once, preserving rollback evidence and proving post-bootstrap state.

## Preconditions
- P1: J04 plan is complete and independently reviewed; exact target/project/account identity is fixed.
- P2: Capture protected pre-state/backup and obtain explicit approval for this consequential write.
- P3: Confirm exclusive write lane, rollback command, and post-write readback.
- P4: Surfaces include `finance_tracker/actual_pipeline.py`, `finance_tracker/actual_snapshot.py`, `integrations/actual/`, Actual API/UI, backup/rollback artifacts, and deployment runtime.

## Steps
### S1 -- Verify write gates {#S1}
- **Do:** Verify plan hash, target identity, pre-state, approval, and exclusive writer.
- **Expect:** All gates pass before mutation.
- **Expect (negative):** Mismatch or missing approval hard-stops with no write.
### S2 -- Apply bootstrap once {#S2}
- **Do:** Execute bounded bootstrap/import operation once.
- **Expect:** Operation receipt records exact target, counts, and result without secrets.
### S3 -- Verify post-state {#S3}
- **Do:** Verify post-state via independent readback and compare to pre-state/plan.
- **Expect:** Intended state exists and unrelated/manual state is preserved.
### S4 -- Roll back partial effect {#S4}
- **Do:** On failure or partial effect, invoke reviewed rollback/compensation path.
- **Expect:** Rollback receipt and fresh readback prove restoration.
- **Expect (negative):** Never retry blindly or claim success from a partial result.
### S5 -- Record handoff {#S5}
- **Do:** Record final readiness and handoff.
- **Expect:** PASS requires complete apply, readback, and rollback evidence; otherwise draft/blocked.

## Success criteria
- SC1: S1 proves approval, pre-state, identity, and exclusive writer before mutation.
- SC2: S2 records one bounded apply receipt with exact target and counts.
- SC3: S3-S5 prove intended state, preservation, and rollback evidence or retain draft/blocked status.

## Known gaps
- G1: Authorized live write and post-bootstrap receipt are unavailable; status remains draft. Beads: `orc-n2q.379.10` author J09; green-loop/revalidation `orc-n2q.379.166` and `orc-n2q.379.218`. Source refs: `finance_tracker/actual_pipeline.py`, `finance_tracker/actual_snapshot.py`, `integrations/actual/`.

## Delta log
- No behavior delta; structural normalization only.
