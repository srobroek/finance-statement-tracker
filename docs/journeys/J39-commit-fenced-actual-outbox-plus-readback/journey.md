---
id: J39
title: Commit fenced Actual outbox + readback
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [n8n-orchestration]
interfaces: [RW-S]
trace: [orc-n2q.379.40, orc-n2q.379.196, 4f115c64351b24554ec9b2ada6b0166786fda727]
---
## Goal
Commit the approved Actual outbox item under the writer fence exactly once, then read back the authoritative result.

## Preconditions
- P1: J37 lease is active and its fencing token is current; J38 candidate and pre-state are recorded.
- P2: Explicit point-of-risk approval and an exclusive write lane are present.

## Steps
### S1 -- Recheck pre-state {#S1}
- **Do:** Read lease, idempotency, and outbox state immediately before commit.
- **Expect:** State matches the approved candidate; stale, conflicting, or expired state blocks the write.

### S2 -- Commit fenced item {#S2}
- **Do:** Submit the candidate with its fencing token and idempotency key.
- **Expect:** The item is committed once, or a deterministic conflict/replay result is returned; stale writers cannot mutate state.

### S3 -- Read back and reconcile {#S3}
- **Do:** Fetch the authoritative outbox record and compare it with the approved candidate.
- **Expect:** Correlation, payload, fence, and receipt match; mismatch triggers rollback/compensation rather than silent acceptance.

## Success criteria
- SC1: S1-S3 establish single fenced commit, deterministic replay behavior, and fresh readback evidence.

## Known gaps
- G1: Runtime commit/readback and rollback receipts are not available in this workspace; status is draft.

## Delta log
- **Δ1** 2026-09-17 · S1-S3 · reconstructed from RW-S Beads contract.
  Evidence: orc-n2q.379.40; orc-n2q.379.196; 4f115c64351b24554ec9b2ada6b0166786fda727 · by: journey-scribe
