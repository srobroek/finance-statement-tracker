---
id: J27
title: Deliver deduplicated push
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [cashback-control]
interfaces: [RW-O]
trace: [docs/project-audit/production-readiness-gap-audit-2026-08-19.md]
---
# J27 -- Deliver deduplicated push

## Goal
As a finance operator, submit one reviewed push candidate to one reviewed subscription and observe one local delivery record. Confirm that replaying the identical request produces one skip without claiming provider-level exactly-once delivery.

## Preconditions
- P1: Use this immutable, non-production fixture: `PushCandidate(key="j27:eligible:2026-09-18", title="J27 test alert", body="Disposable deduplicated push", screen="routing")` and subscription `{"endpoint":"https://push.example/j27","keys":{"p256dh":"p256dh-j27","auth":"auth-j27"}}`. The recipient is the endpoint. The idempotency source is `PushCandidate.key`, stored as `notification_key` and serialized as `notification.tag`. Its scope is the `(notification_key, endpoint)` tuple.
- P2: Exercise `WebPushStore` and `WebPushDispatcher.send()` from `finance_tracker/web_push.py` in a fresh disposable SQLite database with an injected sender. The sender records its subscription and payload without contacting a push provider.
- P3: Run alone in the `exclusive-write-serial` lane. Until S4 completes, prohibit all other access to the disposable database.
- P4: Before the first write or sender call, record the fixture's canonical JSON SHA-256. Record the exact tuple and immutable pre-state. Get finance-operator approval for those records. The approval applies to the fixture hash and tuple. It also applies to the disposable database and injected sender.
- P5: Retain every artifact named by S1 through S4 as the run evidence.

## Steps
### S1 -- Deliver approved push {#S1}
- **Do:** In the exclusive lane, read `push_subscriptions` for the fixture endpoint and `push_deliveries` for the fixture tuple before mutation. Store the canonical JSON results and SHA-256. After approval, upsert only the fixture subscription and call `send()` once with only the fixture candidate.
- **Expect:** Both pre-state queries return no rows. The dispatcher returns `{"sent":1,"failed":0,"skipped":0}`.
- **Expect:** The injected sender records one call for the fixture subscription. Its parsed payload matches the fixture fields and `notification.tag`.
- **Expect:** Fresh readback returns one `SENT` row for the tuple. Its `payload_hash` equals the captured payload's SHA-256.
- **Expect (negative):** No mutation or sender call occurs before approval. No non-fixture row changes, and no live endpoint, VAPID credential, device, or provider is used.
- **Trace:** `finance_tracker/web_push.py` (`PushCandidate`, `WebPushStore.record_delivery`, and `WebPushDispatcher.send`).

### S2 -- Replay identical delivery {#S2}
- **Do:** Replay the identical candidate against the identical endpoint without changing the database or fixture.
- **Expect:** The dispatcher returns `{"sent":0,"failed":0,"skipped":1}`. The injected sender still has one call. Fresh readback of the existing `SENT` row, including `payload_hash`, is byte-for-byte equal to the S1 readback.
- **Expect (negative):** The replay creates no second sender call or delivery row.
- **Trace:** `finance_tracker/web_push.py` (`WebPushStore.delivered` and `WebPushDispatcher.send`).

### S3 -- Reject or record unsafe delivery {#S3}
- **Do:** In a separate fresh database, submit a subscription with a missing recipient.
- **Do:** In another fresh database, make the injected sender raise an exception for the approved fixture.
- **Expect:** Missing or non-HTTPS endpoints fail subscription validation before any sender call. A sender exception returns `{"sent":0,"failed":1,"skipped":0}`, records one local `FAILED` row for the tuple, and increments only that subscription's `failure_count`.
- **Expect (negative):** Neither branch records `SENT`. The failure branch does not assert whether a real provider accepted a request before an exception.
- **Trace:** `finance_tracker/web_push.py` (`_subscription`, `WebPushStore.record_failure`, and `WebPushDispatcher.send`).

### S4 -- Compensate disposable local state {#S4}
- **Do:** After S2, delete only the fixture tuple from `push_deliveries`. Delete only the fixture endpoint from `push_subscriptions`.
- **Do:** Read both targets again and compare their canonical JSON with the S1 pre-state. Compare every non-fixture row checksum before and after compensation.
- **Expect:** Both target readbacks equal the immutable empty pre-state. Every non-fixture checksum is unchanged.
- **Expect:** The retained sender log contains exactly the single approved stub call.
- **Expect (negative):** Compensation makes no additional sender call and does not claim to recall an external notification. The journey does not exercise production compensation or backup restoration.
- **Trace:** `finance_tracker/web_push.py` (`push_deliveries` tuple key and `WebPushStore.remove_subscription`).

## Success criteria
- SC1: S1 records one approved injected-sender call and one local `SENT` row for the exact fixture tuple with a matching payload SHA-256.
- SC2: S2 returns one skip, zero sends, and zero failures while preserving the S1 sender-call count and readback.
- SC3: S3 rejects an invalid recipient before send and records an injected failure without a local `SENT` outcome.
- SC4: S4 restores the two fixture-owned local row sets to their immutable pre-state without changing a non-fixture row or making another sender call.

## Known gaps
- G1: No accepted provider fixture, provider message identifier or receipt, transactional outbox, or external recall API exists. A provider may accept a request before the client records `SENT`, so provider-level exactly-once delivery and post-send rollback remain unaccepted. The journey stays `draft`. Evidence: `docs/project-audit/production-readiness-gap-audit-2026-08-19.md`, `finance_tracker/web_push.py`, and Beads `orc-n2q.379.28`.
- G2: Reusing one key with a different title or body has no conflict contract. Local deduplication skips any existing `SENT` tuple without comparing `payload_hash`. This journey tests only a byte-identical replay.

## Delta log
- No behavior delta. Structural normalization only.
