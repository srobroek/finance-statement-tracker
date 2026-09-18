---
id: J20
title: Card cycle/history
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [cashback-control]
interfaces: [RO]
trace: []
---
# J20 -- Card cycle/history

## Goal
As a finance operator, inspect current card positions and finalized statement-cycle history while proving that every inspected authority remains unchanged.

## Preconditions
- P1: Cashback Control is available through a read-only session, and its SQLite store contains a known card with at least one `FINALIZED` `card_periods` row.
- P2: The operator can read `/api/dashboard`, `/api/periods`, and historical dashboard snapshots without invoking ingestion, reconciliation, finalization, or acknowledgment routes.
- P3: Capture the Cashback store identity, relevant row counts, and content hashes for dashboard events, finalized periods, cursor state, and sync state.
- P4: Capture the Actual ledger identity, transaction count, and content hash. Capture each immutable source artifact's identity, artifact count, and content hash.
- P5: Record the checkout commit and service identity. If any authority in P3 or P4 cannot provide the required readback, block the journey instead of relying on a negative assertion.

## Steps
### S1 -- Capture the read-only baseline {#S1}
- **Do:** Record the P3 through P5 identities, counts, and hashes before opening the card views.
- **Expect:** Cashback, Actual, and source-artifact authorities each have a timestamped baseline for exact post-read comparison.
- **Expect:** The baseline also identifies the checkout and service runtime.
- **Expect (negative):** If any baseline is absent, ambiguous, or write-capable, stop the journey.

### S2 -- Inspect the current card position {#S2}
- **Do:** Open the Cards view and compare the selected card with its `/api/dashboard` entry.
- **Expect:** The card name, tier or pace, spend, bucket usage, and refund amount match the dashboard response.
- **Expect (negative):** Do not treat the Cards view as evidence of cycle boundaries because that view does not display `period_start` or `period_end`.
- **Trace:** `apps/cashback-control/web/app.js` (`buildCardNode`).

### S3 -- Inspect finalized cycle history {#S3}
- **Do:** Read `/api/periods`, choose the known card's finalized cycle, and select its historical snapshot in the Period history view.
- **Expect:** The cycle shows its card, date range, and status.
- **Expect:** Spend and expected cashback match the summary. Tier, reconciliation state, and settlement state also match.
- **Expect:** Each nonzero or capped bucket matches the summary.
- **Expect:** The backend calculates the selected snapshot at that cycle's `period_end` and matches the selected card.
- **Trace:** `apps/cashback-control/server.py` (`historical_periods`, `_historical_dashboard`) and `apps/cashback-control/web/app.js` (`renderPeriodHistory`).

### S4 -- Observe unavailable history {#S4}
- **Do:** Use an established empty-history fixture, then an established read-only period-history error fixture.
- **Expect:** Empty history displays `No finalized cashback periods` and explains that historical calculations need an earlier backend snapshot.
- **Expect:** A period-history request failure displays `History unavailable`. The error detail identifies the failed refresh instead of showing fabricated cycle data.
- **Expect (negative):** Neither branch falls back to a different card, period, or mutable recovery action.

### S5 -- Prove no authority changed {#S5}
- **Do:** Repeat every P3 through P5 readback after S2 through S4 and compare it with the baseline.
- **Expect:** Cashback state and Actual ledger state match their baselines.
- **Expect:** Source artifacts, the checkout, and the runtime match their baselines.
- **Expect (negative):** No provider, ledger, or event mutation is present.
- **Expect (negative):** No finalized-period, cursor, or sync mutation is present.
- **Expect (negative):** No source-artifact, checkout, or runtime mutation is present.

## Success criteria
- SC1: S1 and S5 match the pre-state and post-state evidence for Cashback and Actual. Source artifacts, the checkout, and the runtime also match.
- SC2: S2 matches only the card fields rendered by the Cards view and makes no unsupported claim that the view displays cycle boundaries.
- SC3: S3 shows the selected finalized cycle's boundaries and summary from `/api/periods`, calculated at `period_end` for the matching card.
- SC4: S4 distinguishes empty history from request failure without fabricated data, fallback selection, or recovery writes.

## Known gaps
- G1: No live validation run exists. The required fixtures and authority readbacks are unavailable, so the journey remains in `draft`.
- G2: Beads `orc-rl4l` and the PR149 implementation provide the contract evidence. The implementation is in `apps/cashback-control/server.py` and `apps/cashback-control/web/app.js`. Neither source proves that anyone ran the journey.

## Delta log
