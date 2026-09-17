---
id: J33
title: Unlock/extract secure PDF
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [evidence-ingestion]
interfaces: [RW-O]
trace: []
---
# J33 -- Unlock/extract secure PDF

- **Stable ID:** J33
- **Profile:** RW-O
- **Beads contract:** `orc-n2q.379.34`
- **Lane:** exclusive-write-serial
- **Status:** draft (behavioral validation not performed)

## Goal
As an operator, unlock and extract a protected statement PDF through the approved utility, retaining the original and producing parseable evidence without altering the source.

## Preconditions and surfaces
- The source PDF is archived and its authorized unlock material is available through the protected runtime boundary.
- PDF utility, extraction manifest, parser handoff, and evidence archive are touched; secrets never appear in output.
- Evidence: `finance-statement-tracker/docs/full-ingestion-validation.md`, `finance-statement-tracker/docs/wealth-ingestion.md`.

## Steps
1. **Do:** Verify source digest and protected disposable workspace before extraction. **Expect:** Original identity is fixed and source remains unchanged.
2. **Do:** Run the approved extractor and validate page/text completeness. **Expect:** Redacted receipt binds source and extracted artifact hashes.
3. **Do (negative):** Wrong unlock material, malformed PDF, or incomplete extraction. **Expect:** Fail closed with no partial handoff or cursor advance.
4. **Do (negative):** Re-run the same source. **Expect:** Stable source identity and no duplicate downstream import.

## Evidence and acceptance
Require pre/post hashes, page counts, extraction receipt, redacted errors, and archive readback. Acceptance requires no plaintext unlock material and no ledger mutation.

## Known gaps
No execution receipt is available and the authoritative journey corpus is absent; this document is draft only.
