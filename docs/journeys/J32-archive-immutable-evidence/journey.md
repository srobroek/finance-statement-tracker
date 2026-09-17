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

- **Stable ID:** J32
- **Profile:** RW-O
- **Beads contract:** `orc-n2q.379.33`
- **Lane:** exclusive-write-serial
- **Status:** draft (behavioral validation not performed)

## Goal
As an operator, archive statements, captures, and receipts immutably so every downstream decision can be traced to an exact source artifact.

## Preconditions and surfaces
- A source artifact and its identity metadata are available; destination retention/versioning policy is known.
- OneDrive evidence archive, manifest/hash generation, and readback are touched.
- Evidence: `finance-statement-tracker/docs/full-ingestion-validation.md`, `finance-statement-tracker/docs/wealth-ingestion.md`.

## Steps
1. **Do:** Hash and manifest the source before upload. **Expect:** Source identity is fixed before any side effect.
2. **Do:** Archive to the configured evidence destination and read it back. **Expect:** Destination identity and digest equal the manifest.
3. **Do (negative):** If upload/readback or retention proof fails, stop. **Expect:** No success receipt and no cursor advancement.
4. **Do (negative):** Archive the same source again. **Expect:** Idempotent identity with no duplicate logical evidence.

## Evidence and acceptance
Capture source/destination hashes, retention/version receipt, pre/post inventory, and redacted errors. Acceptance requires immutable readback and zero transaction writes.

## Known gaps
The catalog’s authoritative FORMAT and prior journey body are missing; archive execution remains unverified and draft.
