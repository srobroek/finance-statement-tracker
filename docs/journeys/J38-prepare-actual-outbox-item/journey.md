---
id: J38
title: Prepare Actual outbox item
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [n8n-orchestration]
interfaces: [RW-O]
trace: [orc-n2q.379.39, orc-n2q.379.91, orc-qipq, 4f115c64351b24554ec9b2ada6b0166786fda727]
---
# J38 -- Prepare Actual outbox item

## Goal
The finance operator prepares one `PREPARED` `finance_actual_outbox` row for each approved `imported_id`. Fresh readback proves zero Actual ledger and cursor delta.

## Preconditions
- P1: The supported n8n/Postgres path and accepted direct Actual node are deployed and healthy. Their absence blocks this journey.
- P2: The exclusive Actual writer lease from J37 can be acquired with a current fence, and fresh pre-state is available.
- P3: The batch has an immutable run identity. Its artifact, payload, and imported-ID identities are also immutable. Explicit approval binds those exact values.
- P4: Pre-state records the Actual imported IDs, transaction count, and amount. It also records the balance, invariants, and source cursor.

## Steps
### S1 -- Bind fresh pre-state and fence {#S1}
- **Do:** Check runtime health and authorization, then acquire or reacquire the exclusive writer lease and read the authoritative pre-state.
- **Expect:** The snapshot identifies the lease owner, fence, and expiry. It records fresh Actual values and the cursor.
- **Expect (negative):** An absent runtime or failed authorization stops without mutation. An active competitor, stale fence, or expired lease has the same result.

### S2 -- Assemble one row per imported ID {#S2}
- **Do:** Build one candidate row for each `imported_id`. Include the required identities and digests. Add versions, lease fields, and attempt count.
- **Expect:** Every approved `imported_id` maps to one `PREPARED` candidate, and no candidate represents multiple imported IDs.
- **Expect (negative):** A malformed value, digest mismatch, or conflicting `imported_id` is rejected without outbox, ledger, or cursor mutation.

### S3 -- Bind approval to the candidates {#S3}
- **Do:** Present the candidates, immutable payload and artifact identities, run identity, fresh pre-state, and current fence for explicit approval.
- **Expect:** Approval is recorded after the latest lease handoff and readback, and before any outbox write.
- **Expect (negative):** Missing, stale, or mismatched approval stops preparation without creating a row.

### S4 -- Prepare the outbox rows {#S4}
- **Do:** Insert each approved candidate atomically, or read the existing row by the `imported_id` idempotency key.
- **Expect:** Exactly one row per approved `imported_id` is in `PREPARED`; replay with the same immutable identity creates no duplicate.
- **Expect (negative):** A conflicting replay, lost fence, or post-approval digest change fails closed and cannot write to Actual or advance the cursor.

### S5 -- Fail and compensate safely {#S5}
- **Do:** On failure, fence-check and retain the exact attempt. Mark an existing row `FAILED` with a redacted error class, then release the lease.
- **Expect:** If atomic preparation created no row, none is invented. Cleanup removes only preparation artifacts and proves zero ledger and cursor delta.
- **Expect:** If an Actual write began unexpectedly, approved compensation and fresh readback prove zero unauthorized ledger and cursor delta.
- **Expect (negative):** The outbox never emits `CANCELLED`, creates a balancing entry, or advances the cursor.

### S6 -- Read back preparation evidence {#S6}
- **Do:** Read back every outbox row and the released lease.
- **Do:** Compare the Actual pre-state and cursor. Attach the result to the review handoff.
- **Expect:** The evidence identifies each row and shows expected and observed hashes, counts, amounts, balances, invariants, and zero cursor delta.
- **Expect (negative):** Cursor advancement remains blocked until J39 persists and reads back its durable Actual verification receipt.

## Success criteria
- SC1: S2 and S4 produce exactly one `PREPARED` row per approved `imported_id`, with deterministic replay and no multi-ID row.
- SC2: S1, S3, and S6 bind the rows to a current fence, fresh pre-state, exact approval, and zero Actual ledger or cursor delta.
- SC3: S5 leaves either no row or a `FAILED` row, releases the lease, and proves cleanup did not create a balancing entry.
- SC4: S6 presents reviewable preparation evidence while keeping the cursor blocked until durable Actual verification.

## Known gaps
- G1: `actual-outbox-recovery` is `IMPLEMENTED_NOT_DEPLOYED`; this journey blocks without the deployed n8n/Postgres path and accepted direct Actual node.
- G2: Disposable Actual and kill/concurrency runtime proof remain unavailable, so this journey does not claim live execution evidence.

## Delta log
- None.
