---
id: J29
title: Acquire Outlook finance messages
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [evidence-ingestion]
interfaces: [RW-O]
trace: []
---
# J29 -- Acquire Outlook finance messages

## Goal
As an operator, acquire the bounded set of Outlook finance messages and attachments needed for statement processing, preserving immutable source identity and an auditable cursor.

## Preconditions
- P1: An authenticated, approved Outlook/Graph read scope and bounded date window exist.
- P2: Outlook acquisition, cursor state, attachment selection, and OneDrive evidence archive are the touched surfaces.
- P3: Evidence: `finance-statement-tracker/docs/browser-ingestion.md`, `finance-statement-tracker/docs/full-ingestion-validation.md`.
- P4: The journey uses the exclusive-write-serial lane.

## Steps
### S1 -- Fix acquisition boundary {#S1}
- **Do:** Freeze the account, folder, subject/date filters, and bounded acquisition window.
- **Expect:** The request records the exact boundary and cursor.

### S2 -- Acquire bounded messages {#S2}
- **Do:** Read pages until the bounded window is exhausted and select eligible messages/attachments by stable IDs.
- **Expect:** Counts, IDs, and source hashes are captured.
- **Expect (negative):** Plaintext credentials are not captured.

### S3 -- Handle incomplete acquisition {#S3}
- **Do:** Encounter pagination failure, delayed arrival, duplicate message, or missing attachment.
- **Expect (negative):** The run fails or reports review-required; it never advances the cursor as if complete.

### S4 -- Replay acquisition {#S4}
- **Do:** Repeat the same acquisition.
- **Expect:** Repeated acquisition preserves the source identity.
- **Expect (negative):** No duplicate archive object is created.

## Success criteria
- SC1: S1-S4: Capture pre-state cursor, per-page counts, selected IDs/hashes, archive receipt, post-cursor readback, and redacted failures.
- SC2: S1-S4: Acceptance requires complete pagination evidence, immutable archive identity, and zero ledger mutation.

## Known gaps
- G1: J29 remains draft because the authoritative journey corpus is absent and no behavioral run receipt is available in this checkout. Trace evidence: Beads contract `orc-n2q.379.30`.

## Delta log
- No behavior delta; structural normalization only.
