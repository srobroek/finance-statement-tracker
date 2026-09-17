---
id: J49
title: Restart/recover services
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [service-recovery]
interfaces: [RW-O]
trace: [orc-n2q.379.50, orc-n2q.379.102]
---
## Goal
Recover only the named unhealthy service, or prove a safe restart is impossible, while preserving data, identities, and rollback evidence.

## Preconditions
- Exact service/container identity, health symptom, protected backup, and operation scope are captured.
- The exclusive serial lane, approval, rollback/compensation, and fresh post-readback checks are ready.

## Steps
### S1 -- Diagnose {#S1}
- **Do:** Read health, restart count, listeners, dependencies, logs, and process identity.
- **Expect:** One attributable service and bounded failure mode are identified.
- **Expect-negative:** Do not restart based on stale or ambiguous identity.

### S2 -- Capture pre-state {#S2}
- **Do:** Record protected backup and non-sensitive topology/health receipt.
- **Expect:** Recovery is reversible and scope is fixed.
- **Expect-negative:** No broad stack restart or credential reset.

### S3 -- Recover {#S3}
- **Do:** With explicit approval, restart/recreate only the named service using the existing definition.
- **Expect:** Service returns with same image, mounts, network, and configuration authority.
- **Expect-negative:** No duplicate service, new listener, or replacement runtime.

### S4 -- Verify and compensate {#S4}
- **Do:** Read back health, identity, listeners, data reachability, and no-mutation markers; roll back if required.
- **Expect:** Recovery is proven or operation is classified failed/blocked.
- **Expect-negative:** Never convert indeterminate output into success.

## Evidence and acceptance
Evidence: `AGENTS.md:29-39`, `README.md:97-100`, `deploy/`, `scripts/`, Beads `orc-n2q.379.50`, and review `orc-n2q.379.102`. Acceptance requires exact identity and protected pre/post receipts. Restart and recovery parity must also match.

## Known blockers
Current retained-host runtime evidence is unavailable in this checkout; source readiness is not operational proof.
