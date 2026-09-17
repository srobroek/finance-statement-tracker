---
id: J31
title: Acquire Amazon order evidence
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [evidence-ingestion]
interfaces: [RW-O]
trace: []
---
# J31 -- Acquire Amazon order evidence

## Goal
As an operator, acquire Amazon order evidence that can explain a finance transaction while preserving the original source and avoiding assumptions about cashback.

## Preconditions
- P1: An owner-authorized Amazon visible capture or immutable export is available.
- P2: Browser capture, order/source manifest, OneDrive evidence archive, and later matching are the surfaces.
- P3: Evidence: `finance-statement-tracker/docs/browser-ingestion.md`, `finance-statement-tracker/docs/historical-import-audit-2026-08-18.md`.
- P4: The journey uses the exclusive-write-serial lane.

## Steps
### S1 -- Fix order boundary {#S1}
- **Do:** Freeze account, order/date boundary, and capture method.
- **Expect:** The boundary is recorded before acquisition.

### S2 -- Capture order evidence {#S2}
- **Do:** Capture order identity, merchant, date, amount, currency, and source digest.
- **Expect:** A redacted immutable evidence record is archived.

### S3 -- Reject incomplete evidence {#S3}
- **Do:** If fields are missing, contradictory, or only inferred from a cashback row, mark review-required.
- **Expect (negative):** No guessed order or cashback classification.

### S4 -- Deduplicate order evidence {#S4}
- **Do:** Re-acquire the same order.
- **Expect (negative):** Stable identity and no duplicate archive record.

## Success criteria
- SC1: S1-S4: Acceptance requires source hash, capture manifest, archive readback, and proof that no Actual transaction was written.
- SC2: S1-S4: Matching is a later journey and must not be smuggled into acquisition.

## Known gaps
- G1: No behavioral run receipt is present; authoritative J01--J51 source remains unavailable, so this is an honest draft. Trace evidence: Beads contract `orc-n2q.379.32`.

## Delta log
- No behavior delta; structural normalization only.
