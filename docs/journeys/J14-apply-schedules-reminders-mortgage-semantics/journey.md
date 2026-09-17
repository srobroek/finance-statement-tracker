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
# J14 — Apply schedules/reminders/mortgage semantics

- **Stable ID:** J14
- **Profile:** RW-S
- **Status:** draft / blocked
- **Owner scope:** `orc-n2q.379.15`

## Goal
Apply supported schedule, reminder, and mortgage transformations with deterministic recurrence, identity, approval, readback, receipts, and reversible restore.

## Prerequisites and surfaces
Use the recorded semantic contract from `orc-1e16`, fixed schedule/config identities, pre-state, and exclusive write lane.

## Steps and assertions
1. Inspect recurrence boundary, reminder identity, mortgage input, and pre-state; expect deterministic contract.
2. Preview transformations; expect only in-scope schedule/reminder/mortgage changes.
3. Approve and apply once; expect receipt naming target identities and config hash.
4. Fresh readback; expect exact transformed values and unchanged unrelated records.
5. Negative: invalid recurrence, duplicate identity, stale config; expect refusal/idempotency with no mutation.
6. Failure/restore; expect exact pre-state and linked receipts.

## Evidence and gaps
Beads `orc-n2q.379.15`, fix `.318`, and researcher contract `orc-1e16` establish this title and semantic scope. The fix record reports a 73-line historical catalog snapshot, but authoritative corpus refs `2cd7612`/`161de41` are unavailable; final commands and receipts remain draft.
