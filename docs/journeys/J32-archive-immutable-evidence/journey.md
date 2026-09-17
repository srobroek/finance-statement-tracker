---
id: J32
title: Archive immutable evidence
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [evidence-ingestion]
interfaces: [RW-O]
trace: []
---
# J32 -- Archive immutable evidence

## Goal
As an operator, archive statements, captures, and receipts immutably so every downstream decision can be traced to an exact source artifact.

## Preconditions
- P1: A source artifact and its identity metadata are available; destination retention/versioning policy is known.
- P2: OneDrive evidence archive, manifest/hash generation, and readback are touched.
- P3: Evidence: `finance-statement-tracker/docs/full-ingestion-validation.md`, `finance-statement-tracker/docs/wealth-ingestion.md`.
- P4: The journey uses the exclusive-write-serial lane.

## Steps
### S1 -- Manifest source {#S1}
- **Do:** Hash and manifest the source before upload.
- **Expect:** Source identity is fixed before any side effect.

### S2 -- Archive and read back {#S2}
- **Do:** Archive to the configured evidence destination and read it back.
- **Expect:** Destination identity and digest equal the manifest.

### S3 -- Stop on archive failure {#S3}
- **Do:** If upload/readback or retention proof fails, stop.
- **Expect (negative):** No success receipt and no cursor advancement.

### S4 -- Deduplicate source archive {#S4}
- **Do:** Archive the same source again.
- **Expect (negative):** Idempotent identity with no duplicate logical evidence.

## Success criteria
- SC1: S1-S4: Capture source/destination hashes, retention/version receipt, pre/post inventory, and redacted errors.
- SC2: S1-S4: Acceptance requires immutable readback and zero transaction writes.

## Known gaps
- G1: The catalog’s authoritative FORMAT and prior journey body are missing; archive execution remains unverified and draft. Trace evidence: Beads contract `orc-n2q.379.33`.

## Delta log
- No behavior delta; structural normalization only.
