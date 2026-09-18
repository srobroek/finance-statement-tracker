---
id: J41
title: Run ops/error workflow
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [operations-operator]
surfaces: [n8n-orchestration]
interfaces: [RW-O]
trace: [orc-n2q.379.42, orc-n2q.379.306, orc-qgff]
---
# J41 -- Run ops/error workflow

## Goal
As an operations operator, resolve one failed finance operation with one attributable receipt and either a verified recovery or a blocked escalation.

## Preconditions
- P1: Fix `operation_identity = (execution_id, workflow_id, failure_class, correlation_key)` for the run.
- P2: Capture `state_bundle = (receipt, circuit, cursor, outbox, Actual, Cashback)` as protected pre-state.
- P3: Reserve the exclusive serial lane for the fixed scope.
- P4: Before approval, define one bounded recovery and its exact rollback.
- P5: An authorized approver is available at the point of risk.

## Steps
### S1 -- Freeze identity and pre-state {#S1}
- **Do:** Read the managed execution and `state_bundle`. Freeze `operation_identity` and the target versions.
- **Expect:** The protected pre-state identifies every target and fence for later steps.
- **Expect (negative):** Any mismatch in the frozen run data blocks the journey without mutation.
- **Trace:** `integrations/n8n/workflows/16-operations-error-handler.json`.

### S2 -- Prepare the failure disposition {#S2}
- **Do:** Classify the failure under `policy = (retry, circuit, stale_cursor, deadline)`. Prepare a redacted receipt without writing it.
- **Expect:** The proposal fixes all receipt and branch parameters, including the stop condition.
- **Expect (negative):** If redaction fails, the operator blocks every write. An ambiguous class has the same result.
- **Trace:** `integrations/n8n/workflows/16-operations-error-handler.json`.

### S3 -- Approve the mutation boundary {#S3}
- **Do:** Review the current `approval_scope = (receipt, branch, rollback)` at the point of risk. Before S4, get explicit approval.
- **Expect:** Approval covers the durable receipt and one selected recovery attempt in the fixed scope.
- **Expect (negative):** Missing, stale, or scope-mismatched approval writes neither a receipt nor recovery state.

### S4 -- Record and run the approved branch {#S4}
- **Do:** In the exclusive lane, write one receipt keyed by `operation_identity`.
- **Do:** Independently read the receipt. If it matches the approved proposal, run the bounded recovery or escalation branch.
- **Expect:** A recovery obeys `policy` and the current fence.
- **Expect (negative):** A failed or mismatched receipt readback prevents recovery. A duplicate key or stale fence fails closed.

### S5 -- Read back the disposition {#S5}
- **Do:** After S4, read `state_bundle` from the authoritative surfaces.
- **Expect:** Readback shows one attributable receipt. It also shows one disposition: recovered, compensated, or blocked.
- **Expect (negative):** Missing readback, a duplicate effect, or an out-of-scope change prevents success.

### S6 -- Compensate a failed effect {#S6}
- **Do:** If the approved recovery is partial or fails, run only its reviewed rollback.
- **Do:** Restore each changed target in `state_bundle` to its S1 value. Use the same identity and fence.
- **Do:** Keep the failure receipt. Read every affected target again.
- **Expect:** Each restored target matches its protected pre-state. The receipt records the failed action and rollback.
- **Expect (negative):** An incomplete rollback remains blocked. The operator does not retry it as a new operation.

## Success criteria
- SC1: S1 proves the exact identity and protected pre-state without mutation.
- SC2: S3 records approval before the first durable write in S4.
- SC3: S4 leaves one redacted receipt for `operation_identity`, or it performs no recovery.
- SC4: S5 shows one disposition with no duplicate or out-of-scope effect.
- SC5: When S6 applies, fresh readback matches the protected pre-state for every affected target.

## Known gaps
- G1: `orc-qgff` provides no live n8n execution receipt or production mutation receipt. Runtime behavior remains an unconfirmed blocker.
- G2: The workflow does not redact all reviewed secret forms. Its deduplication key uses only the execution ID.
- G3: The circuit update does not use atomic compare-and-set. No fenced rollback proof exists.

## Delta log
- No behavior delta. This correction records the approval boundary and evidence limits.
