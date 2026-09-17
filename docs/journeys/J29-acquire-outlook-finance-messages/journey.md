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
# J29 — Acquire Outlook finance messages

- **Stable ID:** J29
- **Profile:** RW-O
- **Beads contract:** `orc-n2q.379.30`
- **Lane:** exclusive-write-serial
- **Status:** draft (behavioral validation not performed)

## Goal
As an operator, acquire the bounded set of Outlook finance messages and attachments needed for statement processing, preserving immutable source identity and an auditable cursor.

## Preconditions and surfaces
- An authenticated, approved Outlook/Graph read scope and bounded date window exist.
- Outlook acquisition, cursor state, attachment selection, and OneDrive evidence archive are the touched surfaces.
- Evidence: `finance-statement-tracker/docs/browser-ingestion.md`, `finance-statement-tracker/docs/full-ingestion-validation.md`.

## Steps
1. **Do:** Freeze the account, folder, subject/date filters, and bounded acquisition window. **Expect:** The request records the exact boundary and cursor.
2. **Do:** Read pages until the bounded window is exhausted and select eligible messages/attachments by stable IDs. **Expect:** Counts, IDs, and source hashes are captured without plaintext credentials.
3. **Do (negative):** Encounter pagination failure, delayed arrival, duplicate message, or missing attachment. **Expect:** The run fails or reports review-required; it never advances the cursor as if complete.
4. **Do (negative):** Repeat the same acquisition. **Expect:** Idempotent source identity and no duplicate archive object.

## Evidence and acceptance
Capture pre-state cursor, per-page counts, selected IDs/hashes, archive receipt, post-cursor readback, and redacted failures. Acceptance requires complete pagination evidence, immutable archive identity, and zero ledger mutation.

## Known gaps
J29 remains draft because the authoritative journey corpus is absent and no behavioral run receipt is available in this checkout.
