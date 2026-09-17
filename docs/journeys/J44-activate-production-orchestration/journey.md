---
id: J44
title: Activate production orchestration
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [release-operator]
surfaces: [n8n-orchestration]
interfaces: [RW-S]
trace: [orc-n2q.379.45, orc-n2q.379.201, 4f115c64351b24554ec9b2ada6b0166786fda727]
---
# J44 -- Activate production orchestration

## Goal
Activate approved production orchestration exactly once with guarded deployment, clear ownership, and a tested rollback path.

## Preconditions
- P1: J43 rehearsal is complete; artifact/config hashes, dependency health, and rollback target are recorded.
- P2: Consequential point-of-risk approval and the exclusive write lane are present.

## Steps
### S1 -- Verify release pre-state {#S1}
- **Do:** Read deployment, schedule, credential, and orchestration state immediately before activation.
- **Expect:** State matches the approved release.
- **Expect (negative):** Drift, missing dependencies, or hash mismatch blocks activation.

### S2 -- Activate orchestration {#S2}
- **Do:** Submit the approved activation with idempotency key and release receipt.
- **Expect:** One bounded activation is applied.
- **Expect (negative):** Unauthorized, duplicate, or stale requests fail closed.

### S3 -- Verify or roll back {#S3}
- **Do:** Read back health, scheduled ownership, and audit state; invoke approved rollback if criteria fail.
- **Expect:** Healthy activation or exact rollback is observable with receipt and final state.
- **Expect (negative):** Uncertainty remains blocked, never silently accepted.

## Success criteria
- SC1: S1-S3 prove approved single activation, health readback, and compensating rollback evidence.

## Known gaps
- G1: Production credentials/runtime and rollback receipts are unavailable; activation is not claimed and journey remains draft.

## Delta log
- No behavior delta; structural normalization only.
