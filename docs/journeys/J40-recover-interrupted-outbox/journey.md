---
id: J40
title: Recover interrupted outbox
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [n8n-orchestration]
interfaces: [RW-O]
trace: [orc-n2q.379.41, orc-n2q.379.93, orc-bz2h, bbac5812bd9bf1aaf69758f6824b22e8481133fc]
---
# J40 -- Recover interrupted outbox

## Goal
The finance operator recovers one interrupted Actual outbox item to a durable terminal state. Done means one effect is tied to the stable item identity, its receipt precedes any cursor advance, and fresh readback proves the outcome.

## Preconditions
- P1: The fixed item scope includes `imported_id`, payload or document SHA-256, Actual file, account or card identity, and the persisted `outbox_id` when one exists.
- P2: The recovery view exposes the persisted recovery record and its retry metadata.
- P3: Recovery is approved for the Finance Actual Outbox Recovery workflow and runs in the exclusive serial lane.
- P4: The operator has a reviewed compensating action for the fixed item and can read its authoritative effect, receipt, and cursor without exposing secrets.

## Steps
### S1 -- Reconcile persisted state {#S1}
- **Do:** Read all recovery fields for the immutable identity from P1.
- **Expect:** The readback records the stable `outbox_id` and state. It records owner and fence. It records cursor, idempotency key, and receipt digest. It also records retry count and compensation status.
- **Expect (negative):** Inspection makes no state change. It dispatches no item and performs no retry or compensation.

### S2 -- Acquire exclusive ownership {#S2}
- **Do:** Atomically compare and swap the expected owner, fence, and cursor. Create `PREPARED` only when no row exists; otherwise preserve the persisted `outbox_id`.
- **Expect:** If ownership is stale, the lease is missing, or the compare-and-swap changes zero rows, the item enters `WAIT/RECONCILE`. Bounded backoff ends with fresh S1 readback.
- **Expect (negative):** No parallel or stale-fence apply occurs, and no retry replaces an existing `outbox_id`.

### S3 -- Resume with the stable key {#S3}
- **Do:** At the dispatch approval point, send a `PREPARED` item with the stable identity and digest as its idempotency key. Reconcile a timeout before any retry.
- **Expect:** A receipt-free interrupted item remains `PREPARED` and retries the same key only after S2 grants a fresh fence. A `COMMITTED` item takes the readback-only branch.
- **Expect (negative):** A timeout does not imply failure, change the key, or permit a blind reissue.

### S4 -- Persist the terminal receipt {#S4}
- **Do:** After an acknowledged effect, persist the receipt required by SC2. Advance the cursor only afterward.
- **Expect:** Fresh readback proves the receipt and cursor, then the item becomes `COMMITTED` and releases its fence. A deterministic rejection instead records terminal `FAILED` and releases the fence.
- **Expect (negative):** The cursor does not advance before a durable receipt, and a failed item is not reported as committed.

### S5 -- Compensate a partial effect {#S5}
- **Do:** If S4 cannot prove an effect's commit or readback, get approval. Then run only the P4 action under a fresh S2 fence and persist its receipt.
- **Expect:** Fresh authoritative readback proves `COMPENSATED`; otherwise the item ends in `FAILED_COMPENSATION_REQUIRED`.
- **Expect (negative):** An unproved compensation never becomes success and never triggers an unreviewed rollback.

### S6 -- Prove duplicate suppression {#S6}
- **Do:** Replay recovery with a different run identifier but the same immutable identity and digest. Read the effect, receipt, and cursor again.
- **Expect:** The replay preserves the original `outbox_id`, creates zero additional effects, and leaves the terminal receipt and cursor unchanged.
- **Expect (negative):** Retry or draft identifiers do not create another item or mutate a committed result.

## Success criteria
- SC1: S1-S6 leave exactly one persisted `outbox_id` and at most one attributable effect for the immutable identity.
- SC2: Every cursor advance has one durable receipt containing the same `outbox_id`, fence, and operation or result digest.
- SC3: Stale or zero-row ownership attempts dispatch zero effects and return to bounded `WAIT/RECONCILE`.
- SC4: A partial effect ends only as `COMPENSATED` or `FAILED_COMPENSATION_REQUIRED`, with a fresh compensation readback.
- SC5: Evidence excludes secrets and raw financial or document values.

## Known gaps
- G1: The workflow declares `setupRequired: true` and `fixtureExecuted: false`; no runtime recovery or readback receipt proves this journey.

## Delta log
- No behavior delta; this correction records the reviewed recovery contract.
