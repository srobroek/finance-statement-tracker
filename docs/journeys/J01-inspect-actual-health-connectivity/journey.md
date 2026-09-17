---
id: J01
title: Inspect Actual health/connectivity
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RO]
trace: []
---
# J01 -- Inspect Actual health/connectivity

## Goal
As an operator, inspect Actual health and connectivity without changing the finance state, and distinguish a healthy read from unavailable or stale provider evidence.

## Preconditions
- P1: Actual deployment/configuration and configured project/account identity are available.
- P2: Use the read-only health/connectivity surface; do not submit, import, sync, or bootstrap.
- P3: Capture command, timestamp, endpoint/result class, and redacted evidence.
- P4: Surfaces include `finance_tracker/actual_snapshot.py`, `finance_tracker/actual_pipeline.py`, `integrations/actual/`, configured Actual health endpoint, and provider/API boundary.

## Steps
### S1 -- Read configured identity {#S1}
- **Do:** Read the configured Actual endpoint and runtime identity.
- **Expect:** Endpoint, transport, and project/account target are explicit; no secret is printed.
- **Expect (negative):** Missing configuration fails closed and is reported, not guessed.
### S2 -- Request provider health {#S2}
- **Do:** Request the provider health/connectivity read.
- **Expect:** A bounded response identifies reachable/unreachable, authentication failure, timeout, or malformed response.
- **Expect (negative):** The check does not create, update, import, sync, or mutate Actual data.
### S3 -- Record evidence {#S3}
- **Do:** Compare response with the source contract and record timestamp/status/evidence.
- **Expect:** A reproducible redacted receipt exists.
- **Expect (negative):** A cached or stale capture is not presented as a live pass.
### S4 -- Bound transient retry {#S4}
- **Do:** Re-run only when the first result is transient/ambiguous.
- **Expect:** Repeat results are classified consistently.
- **Expect (negative):** No retry becomes an unbounded loop.
### S5 -- Record outcome {#S5}
- **Do:** Record outcome and unresolved gap.
- **Expect:** PASS requires observable health/connectivity evidence; otherwise status remains draft/blocked.

## Success criteria
- SC1: S1 identifies endpoint and target without exposing secrets.
- SC2: S2-S3 produce bounded, timestamped, redacted evidence with no mutation.
- SC3: S4-S5 classify unavailable outcomes without claiming an unverified pass.

## Known gaps
- G1: Live provider health receipt and authoritative journey corpus are unavailable; status remains draft. Beads: `orc-n2q.379.2` (author J01), `orc-n2q.379.54` (review), `orc-n2q.379.320` (fix/research contract). Source refs: `finance_tracker/actual_snapshot.py`, `finance_tracker/actual_pipeline.py`, `integrations/actual/`. Historical `2d55dbd..4f115c6` is review-only and not authoritative.

## Delta log
- No behavior delta; structural normalization only.
