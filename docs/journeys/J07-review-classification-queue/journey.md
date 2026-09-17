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
## Goal
As an operator, review queued transaction classifications and their evidence, exposing low-confidence or ambiguous cases without applying classifications.

## Preconditions
- Select an isolated queue snapshot and preserve its hash/timestamp.
- Read-only classification-audit path is available; no queue write is permitted.

## Surfaces
`finance_tracker/classification_audit.py`, `finance_tracker/rules.py`, `finance_tracker/ai_rules.py`, transaction fixtures, and classification queue/readback.

## Steps
1. **Do:** Load the queue snapshot and schema. **Expect:** Each item has stable identity, source, current classification, and confidence/evidence.
2. **Do:** Re-evaluate rules/evidence read-only. **Expect:** Deterministic, explainable candidate outcomes are shown.
3. **Do:** Partition accepted, low-confidence, ambiguous, unsupported, and malformed cases. **Expect:** Unsupported cases remain unresolved. **Expect-negative:** No classification, queue, or ledger mutation occurs.
4. **Do:** Record a redacted review report and readiness result. **Expect:** Any missing evidence or source ambiguity remains a blocker/draft.

## Evidence and trace
- Beads: `orc-n2q.379.8` (author J07), review `orc-n2q.379.60`, and fix/revalidation records in packet.
- Source trace: `finance_tracker/classification_audit.py`, `finance_tracker/rules.py`, `finance_tracker/ai_rules.py`.

## Definition-of-ready audit
- [x] Stable ID/title/profile, queue surfaces, ordered Do/Expect/negative steps.
- [x] Confidence/ambiguity branches and explicit no-write proof.
- [x] Beads and source traces recorded.
- [ ] Current queue evidence and authoritative corpus unavailable; status is draft.
