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
# Goal
As the ingestion pipeline, commit an Outlook cursor only after successful processing so retries are idempotent and failed work remains recoverable.

## Preconditions and surfaces
- A processed Outlook envelope has a verified source cursor and receipt.
- Surface: Outlook cursor/receipt integration (provider adapter; current ingestion code in `finance_tracker/browser_ingestion.py`).
- Cursor commit is consequential and requires pre-state, point-of-risk approval, readback, and rollback/compensation.

## Journey
1. **Do:** Submit the exact cursor with its source/version/fence and receipt acknowledgement.
   **Expect:** Cursor advances once and binds to the processed receipt.
2. **Do:** Re-submit the same request.
   **Expect:** Idempotent acknowledgement; no second ledger/event effect.
3. **Expect-negative:** Use stale cursor, mismatched CAS/version, missing acknowledgement, or conflicting receipt.
   **Expect:** Commit is rejected and cursor remains unchanged.
4. **Do:** Roll back/compensate in the declared disposable scope and read back.
   **Expect:** Cursor and associated state restore exactly.

## Evidence
- Beads `orc-n2q.379.24` and research contract `orc-n2q.379.300` (POST cursor request, CAS/version/fence, retry and compensation semantics).
- Beads artifact `local://journeys-J19-J27-beads.json`.

## Known gaps
- Provider endpoint and runtime receipt are not available locally; exact POST contract remains an explicit unresolved gap.
