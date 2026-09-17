---
id: J07
title: Review classification queue
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RO]
trace: []
---
# J07 -- Review classification queue

## Goal
As an operator, review queued transaction classifications and their evidence, exposing low-confidence or ambiguous cases without applying classifications.

## Preconditions
- P1: Select an isolated queue snapshot and preserve its hash/timestamp.
- P2: Read-only classification-audit path is available; no queue write is permitted.
- P3: Surfaces include `finance_tracker/classification_audit.py`, `finance_tracker/rules.py`, `finance_tracker/ai_rules.py`, transaction fixtures, and classification queue/readback.

## Steps
### S1 -- Load queue {#S1}
- **Do:** Load queue snapshot and schema.
- **Expect:** Each item has stable identity, source, current classification, and confidence/evidence.
### S2 -- Re-evaluate evidence {#S2}
- **Do:** Re-evaluate rules/evidence read-only.
- **Expect:** Deterministic, explainable candidate outcomes are shown.
### S3 -- Partition cases {#S3}
- **Do:** Partition accepted, low-confidence, ambiguous, unsupported, and malformed cases.
- **Expect:** Unsupported cases remain unresolved.
- **Expect (negative):** No classification, queue, or ledger mutation occurs.
### S4 -- Record readiness {#S4}
- **Do:** Record a redacted review report and readiness result.
- **Expect:** Missing evidence or source ambiguity remains a blocker/draft.

## Success criteria
- SC1: S1 exposes stable identity and evidence for each queue item.
- SC2: S2-S3 produce deterministic partitions while preserving unsupported cases and making no writes.
- SC3: S4 records blockers for missing evidence or ambiguity.

## Known gaps
- G1: Current queue evidence and authoritative corpus are unavailable; status remains draft. Beads: `orc-n2q.379.8` author J07, review `orc-n2q.379.60`, and fix/revalidation records in packet. Source refs: `finance_tracker/classification_audit.py`, `finance_tracker/rules.py`, `finance_tracker/ai_rules.py`.

## Delta log
- No behavior delta; structural normalization only.
