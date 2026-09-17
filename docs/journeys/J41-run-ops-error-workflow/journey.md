---
id: J41
title: Run ops/error workflow
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [operations-operator]
surfaces: [n8n-orchestration]
interfaces: [RW-O]
trace: [orc-n2q.379.42, 4f115c64351b24554ec9b2ada6b0166786fda727]
---
# J41 -- Run ops/error workflow

## Goal
Run the operational error workflow for a failed finance operation and leave a deterministic, observable resolution or escalation.

## Preconditions
- P1: An attributable operation/error reference and current system health snapshot are available.
- P2: Any consequential retry, compensation, or rollback has an approval and exclusive lane.

## Steps
### S1 -- Inspect error {#S1}
- **Do:** Open the error workflow with the operation reference and inspect state, attempts, fence, and receipts.
- **Expect:** Error class and current recoverability are explicit.
- **Expect (negative):** Inspection alone causes no mutation.

### S2 -- Execute permitted branch {#S2}
- **Do:** Select retry, compensate, or escalate only when its preconditions are met.
- **Expect:** Invalid, stale, or unauthorized branches fail closed; accepted action emits an operation receipt.

### S3 -- Confirm closure {#S3}
- **Do:** Read back final operation and audit state.
- **Expect:** Success, compensated, or blocked/escalated status is visible with correlation and no duplicate effect.

## Success criteria
- SC1: S1-S3 produce an auditable error disposition and preserve fail-closed behavior.

## Known gaps
- G1: Product/runtime execution evidence is unavailable; this document is a draft contract, not validation.

## Delta log
- No behavior delta; structural normalization only.
