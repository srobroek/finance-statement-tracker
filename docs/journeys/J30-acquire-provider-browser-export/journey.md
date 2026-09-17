---
id: J30
title: Acquire provider browser export
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [evidence-ingestion]
interfaces: [RW-O]
trace: []
---
# J30 — Acquire provider browser export

- **Stable ID:** J30
- **Profile:** RW-O
- **Beads contract:** `orc-n2q.379.31`
- **Lane:** exclusive-write-serial
- **Status:** draft (behavioral validation not performed)

## Goal
As an operator, acquire a provider browser export or explicit visible-data capture as immutable evidence, without treating an inaccessible session as a successful export.

## Preconditions and surfaces
- An already-authorized provider browser session or owner-approved visible capture is available; no credentials are guessed or entered by this journey.
- Browser acquisition, export/capture manifest, OneDrive evidence archive, and source identity are touched.
- Evidence: `finance-statement-tracker/docs/browser-ingestion.md` and `finance-statement-tracker/docs/full-ingestion-validation.md`.

## Steps
1. **Do:** Record provider/account scope, capture boundary, and pre-existing session state. **Expect:** Scope and authority are explicit.
2. **Do:** Acquire the export or visible rows and hash the immutable artifact. **Expect:** A manifest binds source identity, capture time, and artifact digest.
3. **Do (negative):** If no authorized session or export exists, stop. **Expect:** A precise blocker, never a fabricated artifact or guessed credential.
4. **Do (negative):** Re-submit an identical artifact. **Expect:** Deduplication by source identity and no duplicate evidence object.

## Evidence and acceptance
Require redacted session/authority receipt, artifact hash, manifest, archive readback, and no product/ledger writes. Acceptance is blocked unless the provider boundary and immutable source are proven.

## Known gaps
The original corpus is unavailable; no browser execution was performed for this reconstruction, so status remains draft.
