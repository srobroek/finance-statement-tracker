---
id: J43
title: Rehearse schedule cutover
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [operations-operator]
surfaces: [n8n-orchestration]
interfaces: [RW-O]
trace: [orc-n2q.379.44, orc-sv7t, 4f115c64351b24554ec9b2ada6b0166786fda727]
---
# J43 -- Rehearse schedule cutover

## Goal
Rehearse an approved schedule handoff so one fenced scheduler owns J43 at every observable state. Independent readback must prove the new owner or restored old owner.

## Preconditions
- P1: A protected pre-state receipt identifies both schedulers, their schedule IDs, and enabled states. It also records current owner, lease key, and fencing generation.
- P2: The reviewed scope fixes the rehearsal target, desired owner, and expected pre-state. It also fixes the approval nonce and exact compensation before mutation.
- P3: Both scheduler runtimes use one durable, uniquely keyed J43 lease record and reject every execution or side effect carrying a stale generation.
- P4: One exclusive writer can lock the lease record. Production activation remains out of scope.

## Steps
### S1 -- Capture schedule state {#S1}
- **Do:** Read both scheduler APIs and the J43 lease record into a timestamped pre-state receipt.
- **Expect:** The receipt identifies both schedulers, their schedule IDs, and enabled states. It also records current owner, lease key, and fencing generation.
- **Expect (negative):** State capture does not renew a lease, enable a schedule, or change ownership.

### S2 -- Approve the fixed handoff {#S2}
- **Do:** Compare the pre-state with the reviewed target and desired owner. Confirm the approval nonce and exact compensation. Record point-of-risk approval.
- **Expect:** Approval binds one expected pre-state and one handoff scope to the nonce before the exclusive writer proceeds.
- **Expect (negative):** A changed pre-state, missing nonce, or scope mismatch stops the rehearsal without mutation.

### S3 -- Commit the fenced handoff {#S3}
- **Do:** Under one row or advisory lock, verify the expected pre-state and approval nonce. Increment the fencing generation. Install the desired owner. Retire the old owner. Commit one receipt.
- **Expect:** The committed lease record names exactly one owner at generation N, or records deliberate quiescence approved in S2.
- **Expect (negative):** No externally visible enable-then-disable pair occurs, and both runtimes reject executions or side effects from generations below N.

### S4 -- Read back both schedulers {#S4}
- **Do:** Read the committed receipt and independently query both scheduler APIs after the handoff.
- **Expect:** The desired owner is active at generation N, while the old owner is inactive and rejects every generation below N.
- **Expect (negative):** A missing receipt, mismatched owner, or stale-generation acceptance fails the rehearsal. Dual ownership or unapproved absence of an owner also fails it.

### S5 -- Compensate a failed readback {#S5}
- **Do:** If S4 fails, lock the same lease record. Increment the generation. Restore the captured owner and schedule state. Commit a compensation receipt. Query both scheduler APIs again.
- **Expect:** Fresh readback proves the captured owner is solely active at the compensation generation and the attempted new owner is inactive or rejected.
- **Expect (negative):** Do not declare rollback complete from the transaction response alone. Retain the handoff and compensation receipts.

## Success criteria
- SC1: S1-S4 retain the pre-state, approval, and handoff receipt. Independent readbacks prove one fenced owner or approved quiescence.
- SC2: When S4 fails, S5 proves the captured owner is solely active at a newer generation and preserves both receipts.
- SC3: No accepted execution or side effect uses a generation older than the authoritative J43 lease record.

## Known gaps
- G1: Scheduler runtime, lease-record implementation, and deployment receipts are unavailable. Atomic handoff readiness remains unresolved, so the journey stays draft.
- G2: Production activation is outside this rehearsal and requires separate approval against the recorded production target.

## Delta log
- No behavior delta. Structural normalization only.
