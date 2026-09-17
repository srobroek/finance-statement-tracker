---
id: J43
title: Rehearse schedule cutover
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [operations-operator]
surfaces: [n8n-orchestration]
interfaces: [RW-O]
trace: [orc-n2q.379.44, 4f115c64351b24554ec9b2ada6b0166786fda727]
---
# J43 -- Rehearse schedule cutover

## Goal
Rehearse a scheduled automation cutover so timing, ownership, rollback, and observability are proven before production activation.

## Preconditions
- P1: Current schedule, timezone, owner, dependencies, and rollback plan are captured.
- P2: Rehearsal mode and an approval boundary are available; production activation is out of scope.

## Steps
### S1 -- Capture schedule state {#S1}
- **Do:** Read the current schedule and dependent workflow readiness.
- **Expect:** A timestamped pre-state identifies the exact schedule and all dependencies.

### S2 -- Run rehearsal {#S2}
- **Do:** Execute the cutover rehearsal/dry run with a bounded test window.
- **Expect:** Planned triggers, handoff, misses, and rollback signals are observable without unintended production writes.

### S3 -- Read back and decide {#S3}
- **Do:** Inspect rehearsal receipts and compare with the approved plan.
- **Expect:** Readback supports proceed, remediate, or stop.
- **Expect (negative):** No production activation occurs without separate approval.

## Success criteria
- SC1: S1-S3 produce timing and rollback evidence sufficient for an explicit cutover decision.

## Known gaps
- G1: Scheduler runtime and deployment evidence are unavailable; production readiness remains unresolved/draft.

## Delta log
- No behavior delta; structural normalization only.
