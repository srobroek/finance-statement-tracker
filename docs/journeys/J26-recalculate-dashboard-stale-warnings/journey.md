---
id: J26
title: Recalculate dashboard/stale warnings
version: 1
status: draft
last_reviewed: 2026-09-16
actors: [operations-user]
surfaces: [cashback, n8n]
interfaces: [RW-O]
trace:
  - finance-statement-tracker/apps/cashback-control/README.md
  - finance-statement-tracker/apps/cashback-control/server.py
  - finance-statement-tracker/finance_tracker/cashback_events.py
  - finance-statement-tracker/finance_tracker/web_push.py
  - finance-statement-tracker/docs/actual-production.md
  - finance-statement-tracker/docs/finance-execution-plan.md
---

## Goal

The operations user verifies that the managed Cashback Control service's configured scheduler rebuilds the dashboard from the authoritative event store. Done means one observed `refresh_dashboard_periodically()` cycle produces a readable derived snapshot and matching stale-warning/push evaluation for the pinned scope. The cycle must not mutate the Actual ledger, source events, or ingestion cursor. If reversal is required, the prior derived artifact and warning state are restored and independently verified.

## Preconditions

- P1: Authenticated read-only probes identify the managed Cashback Control service and its `GET /api/dashboard` surface, configured database and dashboard paths, n8n receipt owner, and source-of-truth identity. No second writer, compatibility bridge, or substitute service is running.
- P2: The existing service runs `refresh_dashboard_periodically()` with the deployed positive `CASHBACK_REFRESH_SECONDS=60`; this scheduler is the reviewed rebuild control. The RW-O lane is uncontended, the service is healthy, and the run blocks if the scheduler, interval, or exact runtime identity cannot be proved.
- P3: Any required authentication or MFA is completed interactively in the existing managed boundary. No password, OTP, cookie, token, PIN, CVV, or full account number is persisted in journey or run evidence.
- P4: The operator has a temporary evidence location outside the checkout. Fresh pre-state records runtime and source identity, writer lease, database and derived-dashboard artifact paths and hashes, event and period counts and IDs, cursor and version, last successful scheduled check, warning state, push configuration, and Actual identity and hash.
- P5: The reviewed mutation is one scheduler-driven dashboard rebuild for the pinned database snapshot, configured 60-second interval, 90-minute scheduled-check grace, and current event-store scope. The deterministic delta is one derived dashboard artifact plus warning and push evaluation. No source event, cursor, period, Actual row, or unrelated alert is in scope.
- P6: Immediately before the selected scheduler cycle, the operator obtains explicit point-of-risk approval for that exact runtime, snapshot identity, scheduled-check grace, scope, and expected dashboard and warning delta. Rollback restores the captured derived-dashboard artifact and acknowledgement or push state. A reviewed event correction, if separately in scope, uses only its event-ID-scoped inverse.

## Steps

### S1 -- Establish the managed runtime and capture the baseline {#S1}

- **Do:** Read health, `GET /api/dashboard`, ingest state, period state, push configuration, and event-store responses through the approved RW-O interface. Confirm that `refresh_dashboard_periodically()` is active with `CASHBACK_REFRESH_SECONDS=60`. Freeze the database, configuration, and dashboard identities. Acquire the writer lease, then record the pre-state and deterministic expected output in temporary evidence.
- **Expect:** Exactly one intended service and source identity are returned. The lease is exclusive. The dashboard artifact, event-store statistics, cursor and version, last successful scheduled check, schedule status, 90-minute grace, alert state, push configuration, and Actual identity and hash have stable IDs or hashes. The reviewed scheduler cycle and expected warning state are explicit.
- **Expect (negative):** Missing or stale pre-state, identity drift, cursor uncertainty, an inactive scheduler, a nonpositive or changed interval, an unavailable restore target, a write-disabled gate, a concurrent writer, or a health failure causes a hard stop before the selected cycle. Baseline reads do not change the checkout, database, dashboard artifact, cursor, push state, n8n receipt, or Actual ledger.
- **Trace:** `finance-statement-tracker/apps/cashback-control/server.py`; `finance-statement-tracker/deploy/cashback/compose.yaml`; `docs/journeys/README.md`.

### S2 -- Diagnose freshness and pin the recalculation input {#S2}

- **Do:** Independently read the live event-store snapshot and current dashboard. Compare event counts and IDs, bucket and card inputs, the successful scheduled-check receipt, `data_status.is_stale`, `check_status`, `last_successful_check_at`, `expected_due_at`, `next_scheduled_check_at`, timezone, `check_grace_minutes`, and existing warning keys. Freeze the exact source snapshot and calculation date for the rebuild.
- **Expect:** The evidence identifies whether the source is healthy, overdue, never checked, invalid, or unavailable from its configured daily schedule and 90-minute grace. Every recalculation input has a source identity. The planned warning set explains every stale or variance warning. The pinned input contains no unreviewed event or message.
- **Expect (negative):** No input broadens because a UI, log, or provider suggests it. Missing or failed checks, unknown or paused sources, malformed schedules, invalid timestamps, ambiguous multi-source selection, or contradictory metadata block the write and cannot claim freshness. Diagnosis performs no mailbox search, cursor advance, event insertion, period finalization, Actual import, alert acknowledgement, or push subscription.
- **Trace:** `finance-statement-tracker/docs/cashback-sync-health.md`; `finance-statement-tracker/tests/test_sync_health.py`; `finance-statement-tracker/apps/cashback-control/web/app.js`.

### S3 -- Approve and perform the bounded dashboard rebuild {#S3}

- **Do:** Re-read S1 pre-state immediately before the selected scheduled cycle. Verify the lease, positive 60-second interval, active `refresh_dashboard_periodically()` thread, and pinned scope. Obtain explicit point-of-risk approval, then observe the next scheduler call to `rebuild_dashboard()`. Record before-and-after artifact identities, API readbacks, and available push evaluation evidence.
- **Expect:** The selected cycle writes one derived dashboard snapshot through `write_dashboard()` for the pinned event-store and configuration state. Card totals, bucket spend and cap, pace, headroom, recommendations, alerts, `data_status.is_stale`, and scheduled-check fields derive from the pinned inputs. The before-and-after hashes and `GET /api/dashboard` readback identify the resulting artifact and warning evaluation.
- **Expect (negative):** Without matching fresh pre-state, an exclusive lease, fixed scope, positive configured interval, active scheduler, and explicit approval, the run does not select a cycle. The validator never invents a manual endpoint or command, calls `rebuild_dashboard()` directly, ingests mail, alters events, advances a cursor, finalizes a period, writes to Actual, changes source configuration, acknowledges alerts, subscribes devices, or starts another writer.
- **Trace:** `finance-statement-tracker/apps/cashback-control/server.py`; `finance-statement-tracker/deploy/cashback/compose.yaml`.

### S4 -- Read back stale warnings and affected dashboard state {#S4}

- **Do:** Independently fetch `GET /api/dashboard` and read the dashboard artifact, health, event-store, and ingest-state responses after the selected cycle. Compare the warning keys and `data_status.is_stale`, `check_status`, `last_successful_check_at`, `expected_due_at`, `next_scheduled_check_at`, timezone, and `check_grace_minutes` with the pinned calculation. Inspect the n8n operational record when the deployment exposes one.
- **Expect:** The fresh dashboard hash and API values agree. `data_status.is_stale` changes only according to the source schedule and 90-minute post-check grace. A successful check accepting zero events counts as healthy. Transaction age does not control health. Configured variance, close-window, and other warnings match their source conditions.
- **Expect (negative):** A missing, partial, duplicate, or mismatched artifact or readback fails the run. A warning is not silently cleared, hidden, or treated as fresh. No push is sent outside the approved evaluation, and no Actual row, cursor, event, period, source receipt, or unrelated dashboard changes.
- **Trace:** `finance-statement-tracker/apps/cashback-control/web/app.js`; `finance-statement-tracker/docs/cashback-sync-health.md`; `finance-statement-tracker/finance_tracker/web_push.py`.

### S5 -- Exercise the unavailable and stale edge branches {#S5}

- **Do:** Without modifying managed state, observe the documented response when the dashboard artifact is absent or unreadable or scheduled-check evidence is unavailable. Record the HTTP status, error text, `data_status` fields, schedule and grace values, and affected views. Stop the affected branch.
- **Expect:** An absent or unreadable artifact returns an explicit unavailable or error result. Missing or failed checks, unknown or paused sources, malformed schedules, and invalid timestamps remain stale or unavailable rather than producing a complete current-data claim. The branch is visible in API and UI responses where both are available.
- **Expect (negative):** The operator does not bypass the branch by starting a writer, substituting historical values, changing the schedule or grace, acknowledging a warning, or editing a fixture. No error branch mutates the event store, cursor, n8n receipt, push subscription, Actual ledger, source artifact, or derived snapshot.
- **Trace:** `finance-statement-tracker/apps/cashback-control/server.py`; `finance-statement-tracker/docs/cashback-sync-health.md`; `finance-statement-tracker/tests/test_sync_health.py`.

### S6 -- Roll back when required and prove final state {#S6}

- **Do:** If the selected scheduler rebuild must be reversed or fails after persistence, prevent another scheduler write within the rollback window. Restore the captured dashboard artifact and acknowledgement or push state. If a separately reviewed run included an event correction, execute only its event-ID-scoped inverse. Independently re-read the artifact, `GET /api/dashboard`, event store, cursor, scheduled-check fields, warning state, push state, receipts, and Actual state before releasing the writer lease.
- **Expect:** A successful cycle has one expected derived artifact hash and matching API readback. Rollback restores the exact prior artifact hash and warning state, leaves source event IDs and counts, cursor and version, periods, push subscriptions, n8n records, and Actual identity unchanged, and produces fresh post-rollback proof with no partial effects.
- **Expect (negative):** An incomplete rollback, failed readback, scope or identity drift, concurrent writer, unapproved action, duplicate selected-cycle evidence, or cursor advancement is never marked successful. The validator does not use a synthetic balancing transaction, broad correction, legacy bridge, or second writer.
- **Trace:** `finance-statement-tracker/docs/journeys/README.md`; `finance-statement-tracker/docs/actual-production.md`; `finance-statement-tracker/apps/cashback-control/server.py`.

## Success criteria

- SC1: S1-S2 capture one healthy intended runtime, active 60-second scheduler, complete pinned input, and deterministic warning and dashboard delta without source mutation.
- SC2: S3 observes one approved `refresh_dashboard_periodically()` cycle and records one derived dashboard artifact for the fixed scope. No manual endpoint, command, or direct function call is invented.
- SC3: S4 proves dashboard artifact and API parity plus schedule-based `data_status.is_stale` behavior, including healthy zero-event checks and explicit stale or unavailable states.
- SC4: S5 records missing-check, failed-check, unknown-source, paused-source, malformed-schedule, invalid-timestamp, and unreadable-artifact branches as visible non-success states.
- SC5: S6 proves the approved post-cycle state or exact derived-artifact rollback with unchanged source events, cursors, periods, subscriptions, receipts, and Actual state.

## Evidence

- `pre-state.json`: redacted runtime and source identity, writer lease, database and dashboard artifact paths and hashes, event and period IDs and counts, cursor and version, scheduler identity and interval, schedule and grace fields, warning and push state, Actual identity and hash, and fixed scope.
- `rebuild-plan.json` and `approval-receipt.json`: pinned input and configuration identities, calculation date, expected warning delta, selected scheduler cycle, exact target, rollback, and point-of-risk approval.
- `rebuild-receipt.json`: before-and-after artifact hashes, selected cycle timing, deterministic `data_status` fields, warning and push evaluation, and available n8n receipt identity.
- `post-readback.json`: independent API, dashboard, event-store, ingest, period, push, and Actual comparisons, including schedule fields and stale or unavailable branch responses.
- `rollback-receipt.json` and `post-rollback.json` when used: exact restored artifact identity, inverse-operation receipt when separately applicable, and proof of no partial effects or cursor advancement. Evidence contains no secrets, credentials, cookies, tokens, or full account numbers.

## Known gaps

- G1: Authoring does not validate the managed runtime. A fresh exclusive RW-O run must prove runtime identity, writer lease, active scheduler, positive deployed interval, derived-dashboard path, authenticated readback, and interactive approval before promotion from `draft`.
- G2: The repository has no user-facing production endpoint or command for manual recalculation. Validation must use the existing `refresh_dashboard_periodically()` scheduler with deployed `CASHBACK_REFRESH_SECONDS=60`; it must not call `rebuild_dashboard()` directly or invent another trigger.
- G3: Push delivery and alert acknowledgement are deployment-specific. Validation must record whether evaluation is disabled, emitted, or acknowledged and must not claim external delivery without an independent receipt.
- G4: No committed fixture guarantees a particular stale state, warning, card, bucket, event set, or scheduler timing. The validator must use the approved deployment's returned values and record unavailable or blocked branches when required evidence is absent.

## Delta log
