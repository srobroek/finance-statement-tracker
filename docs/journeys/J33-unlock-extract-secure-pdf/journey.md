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

## Goal
As an operator, unlock and extract a protected statement PDF through the approved utility, retaining the original and producing parseable evidence without altering the source.

## Preconditions
- P1: The source PDF is archived and its authorized unlock material is available through the protected runtime boundary.
- P2: PDF utility, extraction manifest, parser handoff, and evidence archive are touched; secrets never appear in output.
- P3: Evidence: `finance-statement-tracker/docs/full-ingestion-validation.md`, `finance-statement-tracker/docs/wealth-ingestion.md`.
- P4: The journey uses the exclusive-write-serial lane.

## Steps
### S1 -- Verify source {#S1}
- **Do:** Verify source digest and protected disposable workspace before extraction.
- **Expect:** The original identity is fixed.
- **Expect (negative):** The source remains unchanged.

### S2 -- Extract protected PDF {#S2}
- **Do:** Run the approved extractor and validate page/text completeness.
- **Expect:** Redacted receipt binds source and extracted artifact hashes.

### S3 -- Reject failed extraction {#S3}
- **Do:** Wrong unlock material, malformed PDF, or incomplete extraction.
- **Expect (negative):** Fail closed with no partial handoff or cursor advance.

### S4 -- Replay extraction {#S4}
- **Do:** Re-run the same source.
- **Expect:** The replay preserves the source identity.
- **Expect (negative):** No duplicate downstream import occurs.

## Success criteria
- SC1: S1-S4: Require pre/post hashes, page counts, extraction receipt, redacted errors, and archive readback.
- SC2: S1-S4: Acceptance requires no plaintext unlock material and no ledger mutation.

## Known gaps
- G1: No execution receipt is available and the authoritative journey corpus is absent; this document is draft only. Trace evidence: Beads contract `orc-n2q.379.34`.

## Delta log
- No behavior delta; structural normalization only.
