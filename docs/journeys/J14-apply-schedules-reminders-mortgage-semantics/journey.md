---
id: J14
title: Apply schedules/reminders/mortgage semantics
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RW-S]
trace: []
---
# J14 -- Apply schedules/reminders/mortgage semantics

## Goal
Apply supported schedule, reminder, and mortgage transformations with deterministic recurrence, identity, approval, readback, receipts, and reversible restore.

## Preconditions
- P1: Use the recorded semantic contract from `orc-1e16`.
- P2: Pin fixed schedule/config identities and capture pre-state.
- P3: Use the exclusive write lane.

## Steps
### S1 -- Inspect semantic inputs {#S1}
- **Do:** Inspect recurrence boundary, reminder identity, mortgage input, and pre-state.
- **Expect:** A deterministic contract is recorded.
### S2 -- Preview transformations {#S2}
- **Do:** Preview transformations.
- **Expect:** Only in-scope schedule/reminder/mortgage changes are proposed.
### S3 -- Approve and apply transformations {#S3}
- **Do:** Approve and apply once.
- **Expect:** A receipt names target identities and config hash.
### S4 -- Read back transformations {#S4}
- **Do:** Perform fresh readback.
- **Expect:** Exact transformed values are present and unrelated records are unchanged.
### S5 -- Reject invalid or stale inputs {#S5}
- **Do:** Submit invalid recurrence, duplicate identity, or stale config.
- **Expect:** Refusal or idempotency occurs with no mutation.
### S6 -- Restore after failure {#S6}
- **Do:** Exercise failure and restore.
- **Expect:** Exact pre-state and linked receipts are preserved.

## Success criteria
- SC1: S1-S6: Supported transformations are applied once and readback matches the approved contract.
- SC2: S1-S6: Invalid or stale input cannot mutate unrelated state; restore is exact and auditable.

## Known gaps
- G1: Beads `orc-n2q.379.15`, fix `.318`, and researcher contract `orc-1e16` establish this title and semantic scope.
- G2: The fix record reports a 73-line historical catalog snapshot, but authoritative corpus refs `2cd7612`/`161de41` are unavailable; final commands and receipts remain draft.

## Delta log
