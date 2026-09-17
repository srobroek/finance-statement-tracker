---
id: J23
title: Commit Outlook cursor
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [cashback-control]
interfaces: [RW-O]
trace: []
---
# J23 -- Commit Outlook cursor

## Goal
As the ingestion pipeline, commit an Outlook cursor only after successful processing so retries are idempotent and failed work remains recoverable.

## Preconditions
- P1: A processed Outlook envelope has a verified source cursor and receipt.
- P2: Surface: Outlook cursor/receipt integration (provider adapter; current ingestion code in `finance_tracker/browser_ingestion.py`).
- P3: Cursor commit is consequential and requires pre-state, point-of-risk approval, readback, and rollback/compensation.

## Steps
### S1 -- Commit cursor {#S1}
- **Do:** Submit the exact cursor with its source/version/fence and receipt acknowledgement.
- **Expect:** Cursor advances once and binds to the processed receipt.

### S2 -- Replay cursor commit {#S2}
- **Do:** Re-submit the same request.
- **Expect:** The request receives an idempotent acknowledgement.
- **Expect (negative):** No second ledger or event effect occurs.

### S3 -- Reject unsafe cursor {#S3}
- **Do:** Use stale cursor, mismatched CAS/version, missing acknowledgement, or conflicting receipt.
- **Expect:** The cursor commit is rejected.
- **Expect (negative):** The cursor remains unchanged.

### S4 -- Restore cursor state {#S4}
- **Do:** Roll back/compensate in the declared disposable scope and read back.
- **Expect:** Cursor and associated state restore exactly.

## Success criteria
- SC1: S1: Cursor advances once and binds to the processed receipt.
- SC2: S2: Idempotent acknowledgement; no second ledger/event effect.
- SC3: S3: Commit is rejected and cursor remains unchanged.
- SC4: S4: Cursor and associated state restore exactly.

## Known gaps
- G1: Provider endpoint and runtime receipt are not available locally; exact POST contract remains an explicit unresolved gap. Trace evidence: Beads `orc-n2q.379.24` and research contract `orc-n2q.379.300` (POST cursor request, CAS/version/fence, retry and compensation semantics). Beads artifact `local://journeys-J19-J27-beads.json`.

## Delta log
- No behavior delta; structural normalization only.
