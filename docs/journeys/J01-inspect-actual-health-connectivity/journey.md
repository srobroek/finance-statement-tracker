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
## Goal
As an operator, inspect Actual health and connectivity without changing the finance state, and distinguish a healthy read from unavailable or stale provider evidence.

## Preconditions
- Actual deployment/configuration and its configured project/account identity are available.
- Use the read-only health/connectivity surface; do not submit, import, sync, or bootstrap.
- Capture command, timestamp, endpoint/result class, and redacted evidence.

## Surfaces
`finance_tracker/actual_snapshot.py`, `finance_tracker/actual_pipeline.py`, `integrations/actual/`, configured Actual health endpoint, and the provider/API boundary.

## Steps
1. **Do:** Read the configured Actual endpoint and runtime identity. **Expect:** The endpoint, transport, and project/account target are explicit; no secret is printed. **Expect-negative:** Missing configuration fails closed and is reported, not guessed.
2. **Do:** Request the provider health/connectivity read. **Expect:** A bounded response identifies reachable/unreachable, authentication failure, timeout, or malformed response. **Expect-negative:** The check does not create, update, import, sync, or mutate Actual data.
3. **Do:** Compare the response with the current source contract and record timestamp/status/evidence. **Expect:** A reproducible redacted receipt exists. **Expect-negative:** A cached or stale capture is not presented as a live pass.
4. **Do:** Re-run only when the first result is transient/ambiguous. **Expect:** Repeat results are classified consistently. **Expect-negative:** No retry becomes an unbounded loop.
5. **Do:** Record outcome and unresolved gap. **Expect:** PASS requires observable health/connectivity evidence; otherwise status remains draft/blocked.

## Evidence and trace
- Beads: `orc-n2q.379.2` (author J01), `orc-n2q.379.54` (review), `orc-n2q.379.320` (fix/research contract).
- Source trace: `finance_tracker/actual_snapshot.py`; `finance_tracker/actual_pipeline.py`; `integrations/actual/`.
- Historical commit range `2d55dbd..4f115c6` is cited by review only and is not treated as authoritative.

## Definition-of-ready audit
- [x] Stable ID/title/profile and RO boundary.
- [x] Preconditions, surfaces, ordered Do/Expect/negative steps.
- [x] Trace references and explicit no-mutation assertions.
- [ ] Live provider health receipt and authoritative journey corpus are unavailable; status is draft.
