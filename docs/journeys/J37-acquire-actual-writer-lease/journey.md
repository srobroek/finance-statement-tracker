---
id: J37
title: Acquire Actual writer lease
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [n8n-orchestration]
interfaces: [RW-O]
trace: [orc-n2q.379.38, orc-giuj, 4f115c64351b24554ec9b2ada6b0166786fda727]
---
# J37 -- Acquire Actual writer lease

## Goal
Acquire an authenticated, fenced writer lease for Actual so one approved writer can proceed and competing or expired leases fail closed.

## Preconditions
- P1: Actual writer endpoint and the operator identity are configured; no unexpired lease is assumed.
- P2: A pre-state read and an approval for the consequential write are captured.

## Steps
### S1 -- Read current lease state {#S1}
- **Do:** Inspect the current lease owner, fencing version/token, expiry, and audit state.
- **Expect:** A fresh, attributable snapshot is returned; a missing or expired lease is distinguishable from an active lease.

### S2 -- Request the lease {#S2}
- **Do:** Authenticated operator requests acquisition with an idempotency key.
- **Expect:** The service grants one lease with owner identity, monotonically fencing token/version, expiry, and receipt.
- **Expect (negative):** An active competing lease is rejected without mutation.

### S3 -- Renew or release {#S3}
- **Do:** Before expiry, renew with the same authorized identity, or release the exact lease.
- **Expect:** Unauthorized, stale-token, and post-expiry requests fail closed; successful renewal/release emits an audit receipt and preserves fencing monotonicity.

## Success criteria
- SC1: S1-S3 show exclusive ownership, fencing, expiry, authorization, and observable audit receipts without accepting stale writers.

## Known gaps
- G1: Runtime deployment/readback evidence is unavailable; this journey remains draft and is not an execution claim.

## Delta log
- No behavior delta; structural normalization only.
