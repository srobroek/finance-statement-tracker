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
## Goal
As an authorized operator, apply a reviewed Actual bootstrap exactly once, preserving rollback evidence and proving post-bootstrap state.

## Preconditions
- J04 plan is complete and independently reviewed; exact target/project/account identity is fixed.
- Capture protected pre-state/backup and obtain explicit approval for this consequential write.
- Confirm exclusive write lane, rollback command, and post-write readback.

## Surfaces
`finance_tracker/actual_pipeline.py`, `finance_tracker/actual_snapshot.py`, `integrations/actual/`, Actual API/UI, backup/rollback artifacts, and deployment runtime.

## Steps
1. **Do:** Verify plan hash, target identity, pre-state, approval, and exclusive writer. **Expect:** All gates pass before mutation. **Expect-negative:** Mismatch or missing approval hard-stops with no write.
2. **Do:** Execute the bounded bootstrap/import operation once. **Expect:** Operation receipt records exact target, counts, and result without secrets.
3. **Do:** Verify post-state via independent readback and compare to pre-state/plan. **Expect:** Intended state exists and unrelated/manual state is preserved.
4. **Do:** On failure or partial effect, invoke the reviewed rollback/compensation path. **Expect:** Rollback receipt and fresh readback prove restoration. **Expect-negative:** Never retry blindly or claim success from a partial result.
5. **Do:** Record final readiness and handoff. **Expect:** PASS requires complete apply, readback, and rollback evidence; otherwise draft/blocked.

## Evidence and trace
- Beads: `orc-n2q.379.10` (author J09), green-loop/revalidation `orc-n2q.379.166` and `orc-n2q.379.218`.
- Source trace: `finance_tracker/actual_pipeline.py`, `finance_tracker/actual_snapshot.py`, `integrations/actual/`.

## Definition-of-ready audit
- [x] Stable ID/title/profile, explicit RW-S boundary, approval/pre-state/rollback gates.
- [x] Ordered apply/readback/rollback steps and hard-stop branches.
- [x] Beads and source traces recorded.
- [ ] Authorized live write and post-bootstrap receipt are unavailable; status is draft.
