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
# J30 -- Acquire provider browser export

## Goal
As an operator, acquire a provider browser export or explicit visible-data capture as immutable evidence, without treating an inaccessible session as a successful export.

## Preconditions
- P1: An already-authorized provider browser session or owner-approved visible capture is available; no credentials are guessed or entered by this journey.
- P2: Browser acquisition, export/capture manifest, OneDrive evidence archive, and source identity are touched.
- P3: Evidence: `finance-statement-tracker/docs/browser-ingestion.md` and `finance-statement-tracker/docs/full-ingestion-validation.md`.
- P4: The journey uses the exclusive-write-serial lane.

## Steps
### S1 -- Record capture scope {#S1}
- **Do:** Record provider/account scope, capture boundary, and pre-existing session state.
- **Expect:** Scope and authority are explicit.

### S2 -- Acquire immutable export {#S2}
- **Do:** Acquire the export or visible rows and hash the immutable artifact.
- **Expect:** A manifest binds source identity, capture time, and artifact digest.

### S3 -- Stop without authority {#S3}
- **Do:** If no authorized session or export exists, stop.
- **Expect (negative):** A precise blocker, never a fabricated artifact or guessed credential.

### S4 -- Deduplicate artifact {#S4}
- **Do:** Re-submit an identical artifact.
- **Expect (negative):** Deduplication by source identity and no duplicate evidence object.

## Success criteria
- SC1: S1-S4: Require redacted session/authority receipt, artifact hash, manifest, archive readback, and no product/ledger writes.
- SC2: S1-S4: Acceptance is blocked unless the provider boundary and immutable source are proven.

## Known gaps
- G1: The original corpus is unavailable; no browser execution was performed for this reconstruction, so status remains draft. Trace evidence: Beads contract `orc-n2q.379.31`.

## Delta log
- No behavior delta; structural normalization only.
