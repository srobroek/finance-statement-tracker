---
id: J47
title: Deploy inactive stack
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [deployment]
interfaces: [RW-S]
trace: [orc-n2q.379.48, orc-n2q.379.100]
---
## Goal
Prepare and deploy the reviewed finance stack while keeping workflows inactive/write-disabled until explicit promotion gates pass.

## Preconditions
- Exact reviewed image/config digests, target host, backup, and rollback package are bound.
- The exclusive deployment lane and approval boundary are available; no concurrent writer is active.

## Steps
### S1 — Preflight {#S1}
- **Do:** Verify target identity, image/config digests, compose topology, backup, and inactive desired state.
- **Expect:** Pre-state is captured and all required inputs are exact.
- **Expect-negative:** Missing digest, stale backup, drift, or active unapproved workflow stops.

### S2 — Deploy inactive {#S2}
- **Do:** Apply only the reviewed stack definition in the exclusive lane.
- **Expect:** Services start with workflows inactive and writes disabled; receipt records exact identities.
- **Expect-negative:** No duplicate stack, listener, runner, or credential path is created.

### S3 — Read back {#S3}
- **Do:** Check health, network/listener identity, workflow activation, and bounded logs.
- **Expect:** Health and topology match the reviewed contract; no financial mutation occurred.
- **Expect-negative:** Any drift fails closed and triggers rollback.

### S4 — Roll back {#S4}
- **Do:** Restore the protected pre-state on approved failure and verify it.
- **Expect:** Previous identities, health, and inactive state return.
- **Expect-negative:** Do not promote or retry after indeterminate readback.

## Evidence and acceptance
Evidence: `AGENTS.md:29-36`; `README.md:88-99`; `integrations/n8n/`; `config/codex-automations.json`; Beads `orc-n2q.379.48`, review `orc-n2q.379.100`. Acceptance is exact digest/topology parity, inactive/write-disabled state, and protected rollback proof.

## Known blockers
The authoritative journey corpus and deployment receipt are unavailable locally; source and tests are not production proof. Keep draft until current host evidence exists.
