---
id: J23
title: Commit Outlook cursor
version: 1
status: draft
last_reviewed: 2026-09-18
actors: [finance-operator]
surfaces: [cashback-control]
interfaces: [RW-O]
trace: [orc-vxqa, orc-n2q.379.76, orc-n2q.379.267, integrations/n8n/workflows/12-outlook-message-sweep.json, finance_tracker/cashback_events.py, finance_tracker/server.py]
---
# J23 -- Commit Outlook cursor

## Goal
Commit one frozen Outlook cursor envelope after downstream processing, with a durable acknowledgment that makes restart and replay idempotent.

## Preconditions
- P1: One exclusive writer lane has captured the `outlook` cursor row, its `cursor_value`, and its `cursor_version`.
- P2: The run freezes `run_id`/`correlation_id`, `run_upper_bound`, `scanned_count`, `accepted_count`, `downstream_receipt_sha256`, and the reviewed message/event payload.
- P3: The timestamps are finite, `run_upper_bound >= cursor_value`, `accepted_count <= scanned_count`, and the source identity is `outlook`.
- P4: `downstream_receipt_sha256` identifies the durable downstream result for this immutable run envelope.
- P5: Corrective rollback uses separate authorization and is never part of ingest retry.

## Steps
### S1 -- Capture the frozen envelope {#S1}
- **Do:** Read and retain the pre-state cursor row and any existing durable receipt for `(source_code, run_id)`.
- **Expect:** The envelope records `source_code='outlook'`, pre-state `cursor_value`, `expected_cursor_version`, bounds, counts, run identity, payload, and receipt hash.
- **Expect (negative):** Abort before mutation if authority, pre-state, schema, source, timestamp, bound, count, or fence validation fails.

### S2 -- Validate downstream durability {#S2}
- **Do:** Verify the reviewed payload and `downstream_receipt_sha256` against the completed downstream result.
- **Expect:** The downstream result is durable before cursor mutation.
- **Expect (negative):** No cursor or acknowledgment changes when downstream durability is absent or mismatched.

### S3 -- Commit and read back the cursor {#S3}
- **Do:** Immediately before mutation, get point-of-risk approval naming `source_code`, pre-state cursor/version, `run_upper_bound`, both counts, and `downstream_receipt_sha256`.
- **Do:** POST `/api/outlook/messages` exactly once for the run envelope with `source_code='outlook'`, `run_id`/`correlation_id`, frozen `cursor_value`, `expected_cursor_version`, `run_upper_bound`, `scanned_count`, `accepted_count`, `downstream_receipt_sha256`, and the reviewed message/event payload.
- **Expect:** The handler performs one atomic CAS on `(source_code, expected_cursor_version)`, writes `cursor_value=run_upper_bound`, `cursor_version=expected_cursor_version+1`, `committed_run_id`, both counts, and the downstream receipt identity.
- **Expect:** Immediate readback exactly matches the upper bound, `expected_cursor_version+1`, run ID, and downstream receipt hash.
- **Expect:** Only after exact readback, the handler persists and returns `{status:'CURSOR_COMMITTED', source_code, run_id, cursor_value, cursor_version, scanned_count, accepted_count, downstream_receipt_sha256, cursor_commit_eligible:true}`.
- **Expect (negative):** Readback mismatch returns `SOURCE_CURSOR_CAS_READBACK_MISMATCH`, withholds acknowledgment, and escalates for corrective operation.

### S4 -- Verify conflicts, restart, and replay {#S4}
- **Do:** Exercise a zero-row CAS, a version/fence mismatch, restart after CAS/readback, an identical replay, and a changed payload under the same `run_id`.
- **Expect:** Zero-row CAS or version/fence mismatch returns `SOURCE_CURSOR_VERSION_CONFLICT` with a redacted conflict receipt.
- **Expect:** Restart locates the durable receipt by `(source_code, run_id)` and returns it without another CAS.
- **Expect:** An identical replay returns the same verified receipt without another cursor, ledger, event, or acknowledgment effect.
- **Expect:** A changed envelope under the same `run_id` returns `RUN_ID_PAYLOAD_CONFLICT`.
- **Expect (negative):** Conflict creates no mutation or acknowledgment. Retry re-reads current state and plans a new envelope. It never blindly resubmits.
- **Expect (negative):** Receipt persistence failure retains the committed cursor and receipt state. Resume uses the same `run_id`. It never moves the cursor backward.

### S5 -- Apply corrective rollback {#S5}
- **Do:** Capture the committed row and receipt, get separate corrective approval, then CAS against the current version to restore the captured pre-state.
- **Expect:** Fresh readback verifies source, version, cursor, run identity, receipt identity, and the durable post-rollback receipt.
- **Expect (negative):** No inverse cursor write occurs through ingest retry or without corrective authorization.

## Success criteria
- SC1: S1 preserves one immutable envelope and rejects invalid authority, identity, schema, bounds, counts, or version fences before mutation.
- SC2: S2 proves the downstream result and receipt identity are durable before cursor mutation.
- SC3: S3 performs the exact POST, advances the cursor once by CAS, verifies exact readback, and returns the durable `CURSOR_COMMITTED` receipt.
- SC4: S4 proves conflict safety, restart recovery, identical replay idempotency, changed-payload rejection, and the absence of duplicate effects.
- SC5: S5 restores captured pre-state only through a separately approved corrective CAS and proves restoration with fresh readback and a durable receipt.

## Known gaps
- G1: The field-level POST, CAS, readback, receipt, retry, and corrective rollback contract is recorded in Beads `orc-vxqa`. A live provider endpoint and runtime receipt are unavailable locally, so this journey does not claim provider execution evidence.
- G2: `finance_tracker/cashback_events.py:273-283,428-463` and `finance_tracker/server.py:317-328` document the current implementation gap; this journey does not claim those paths enforce the contract.

## Delta log
- The journey now records the researched Outlook cursor contract and the missing `POST /api/outlook/messages` action.
