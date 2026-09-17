---
id: J40
title: Recover interrupted outbox
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [n8n-orchestration]
interfaces: [RW-O]
trace: [orc-n2q.379.41, 4f115c64351b24554ec9b2ada6b0166786fda727]
---
## Goal
Safely recover an interrupted Actual outbox operation without duplicating a committed item or losing its audit trail.

## Preconditions
- P1: The interrupted operation's correlation/idempotency key and last known pre-state are available.
- P2: Recovery is approved and runs in the exclusive serial lane.

## Steps
### S1 -- Inspect operation state {#S1}
- **Do:** Read outbox, lease, and audit state for the correlation key.
- **Expect:** The operation is classified as committed, pending, failed, expired, or unknown; no write occurs.

### S2 -- Choose safe recovery {#S2}
- **Do:** For committed state, reconcile; for pending/failed state, retry only with the current fence; for unknown state, stop and escalate.
- **Expect:** Recovery refuses duplicate or stale-fence actions and records the chosen branch.

### S3 -- Verify outcome {#S3}
- **Do:** Freshly read the authoritative record and audit receipt after recovery.
- **Expect:** Exactly one outcome is attributable to the original key, or an explicit unresolved state remains visible.

## Success criteria
- SC1: S1-S3 recover only when state is proven safe and preserve idempotency and auditability.

## Known gaps
- G1: No runtime recovery/readback receipt is available; unresolved deployment behavior remains draft.

## Delta log
- **Δ1** 2026-09-17 · S1-S3 · reconstructed from Beads author contract.
  Evidence: orc-n2q.379.41; 4f115c64351b24554ec9b2ada6b0166786fda727 · by: journey-scribe
