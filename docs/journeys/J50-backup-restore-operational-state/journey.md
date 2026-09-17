---
id: J50
title: Backup/restore operational state
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [backup-restore]
interfaces: [RW-S]
trace: [orc-n2q.379.51, orc-n2q.379.103]
---
## Goal
Capture a recoverable protected backup and, only with approval, restore operational state in a bounded disposable or approved target, proving parity and rollback.

## Preconditions
- Exact backup set, encryption/key authority, target identity, quiescence, and retention scope are known.
- Exclusive serial lane, approval, rollback/compensation, and post-restore readback are available.

## Steps
### S1 — Capture {#S1}
- **Do:** Quiesce the approved scope and create an encrypted protected backup with manifest/hash.
- **Expect:** Backup is complete, recoverable, access-controlled, and contains no plaintext secrets in the receipt.
- **Expect-negative:** Missing key, incomplete artifact, or active writer blocks capture.

### S2 — Validate in isolation {#S2}
- **Do:** Restore into disposable isolated state where permitted and check readiness/counts/identities.
- **Expect:** Restore is network-bounded and target does not mutate production.
- **Expect-negative:** Never test by overwriting the authoritative store.

### S3 — Restore approved target {#S3}
- **Do:** With explicit approval, restore only the fixed target and record pre-state.
- **Expect:** Operational state, credentials/config bindings, and service contracts regain parity.
- **Expect-negative:** No unreviewed migration, secret exposure, or broad cleanup.

### S4 — Roll back and verify {#S4}
- **Do:** On failure restore pre-state or compensation package; perform fresh readback and replay/no-op checks.
- **Expect:** Rollback preserves authority and produces a durable receipt.
- **Expect-negative:** Indeterminate restore is blocked, never called green.

## Evidence and acceptance
Evidence: `docs/backup-and-restore.md`; `AGENTS.md:9-13,29-36`; `deploy/`; `tests/test_backup_verifier.py`; Beads `orc-n2q.379.51`, review `orc-n2q.379.103`. Acceptance requires protected backup/restore/replay/rollback receipts and exact no-mutation boundaries.

## Known blockers
No current protected restore drill or authoritative journey corpus is available here; draft only.
