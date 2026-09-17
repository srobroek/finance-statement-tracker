---
id: J38
title: Prepare Actual outbox item
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [n8n-orchestration]
interfaces: [RW-O]
trace: [orc-n2q.379.39, 4f115c64351b24554ec9b2ada6b0166786fda727]
---
## Goal
Prepare one schema-valid, attributable Actual outbox item without committing it or mutating external state.

## Preconditions
- P1: A fenced writer lease (J37) and source transaction intent are available.
- P2: The operator has captured the input/pre-state and an approval boundary for later commit.

## Steps
### S1 -- Assemble item {#S1}
- **Do:** Map the approved transaction intent into the Actual outbox schema, including correlation and idempotency fields.
- **Expect:** A deterministic candidate item is produced with required fields, provenance, and the lease fencing token.

### S2 -- Validate candidate {#S2}
- **Do:** Validate schema, amounts, dates, account references, and duplicate/idempotency constraints.
- **Expect:** Valid input is accepted for review; malformed, non-finite, stale-lease, or duplicate input is rejected with no outbox mutation.

### S3 -- Review handoff {#S3}
- **Do:** Present the candidate and pre-state for explicit commit approval.
- **Expect:** The item remains uncommitted and a reviewable receipt identifies the candidate and approval requirement.

## Success criteria
- SC1: S1-S3 produce exactly one reviewable candidate and prove preparation is side-effect free.

## Known gaps
- G1: Canonical J01-J51 corpus and runtime deployment evidence are unavailable; no product execution is claimed.

## Delta log
- **Δ1** 2026-09-17 · S1-S3 · reconstructed from assigned Bead contract.
  Evidence: orc-n2q.379.39; 4f115c64351b24554ec9b2ada6b0166786fda727 · by: journey-scribe
