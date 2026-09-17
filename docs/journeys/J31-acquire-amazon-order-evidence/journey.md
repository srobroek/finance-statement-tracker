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

- **Stable ID:** J31
- **Profile:** RW-O
- **Beads contract:** `orc-n2q.379.32`
- **Lane:** exclusive-write-serial
- **Status:** draft (behavioral validation not performed)

## Goal
As an operator, acquire Amazon order evidence that can explain a finance transaction while preserving the original source and avoiding assumptions about cashback.

## Preconditions and surfaces
- An owner-authorized Amazon visible capture or immutable export is available.
- Browser capture, order/source manifest, OneDrive evidence archive, and later matching are the surfaces.
- Evidence: `finance-statement-tracker/docs/browser-ingestion.md`, `finance-statement-tracker/docs/historical-import-audit-2026-08-18.md`.

## Steps
1. **Do:** Freeze account, order/date boundary, and capture method. **Expect:** The boundary is recorded before acquisition.
2. **Do:** Capture order identity, merchant, date, amount, currency, and source digest. **Expect:** A redacted immutable evidence record is archived.
3. **Do (negative):** If fields are missing, contradictory, or only inferred from a cashback row, mark review-required. **Expect:** No guessed order or cashback classification.
4. **Do (negative):** Re-acquire the same order. **Expect:** Stable identity and no duplicate archive record.

## Evidence and acceptance
Acceptance requires source hash, capture manifest, archive readback, and proof that no Actual transaction was written. Matching is a later journey and must not be smuggled into acquisition.

## Known gaps
No behavioral run receipt is present; authoritative J01--J51 source remains unavailable, so this is an honest draft.
