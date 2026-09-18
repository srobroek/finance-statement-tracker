---
id: J50
title: Backup/restore operational state
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [backup-restore]
interfaces: [RW-S]
trace: [orc-n2q.379.51, orc-n2q.379.103, orc-n2q.379.305, orc-q4di]
---
# J50 -- Backup/restore operational state

## Goal
The finance operator captures protected backups and proves a disposable restore of Actual, Cashback, and n8n without mutating source state.

## Preconditions
- P1: The operator has explicit approval, the exclusive restore lane, key authority, and a receipt location that excludes plaintext secrets.
- P2: The operator selects backups produced by `deploy/actual/backup.sh` and the pinned n8n `scripts/backup.sh`, with manifests, timestamps, and SHA-256 values.
- P3: Canonical source roots and newly created target roots are recorded separately for Actual data, Cashback data, and the n8n database.
- P4: Actual, the Actual proxy, Cashback, n8n, task runners, and every other writer can be stopped and observed as stopped.
- P5: Pre-restore safety copies, rollback owners, health probes, and the required domain readbacks are available.

## Steps
### S1 -- Lock and inspect the restore {#S1}
- **Do:** Acquire the exclusive restore lock and record approval, canonical source and target roots, snapshot identity, timestamp, manifest, and SHA-256.
- **Expect:** The receipt identifies three immutable sources and three new targets. Each target is empty and outside production. Its canonical path and filesystem identity differ from its source, with no overlap.
- **Expect (negative):** If any path, lock, manifest, or checksum guard fails, abort before quiescence.

### S2 -- Seal the selected backups {#S2}
- **Do:** Verify the Actual/Cashback archive with `deploy/actual/backup.sh` artifacts and verify the pinned n8n dump checksum before creating target state.
- **Expect:** The receipt binds each immutable artifact to its manifest, timestamp, SHA-256, and intended domain.
- **Expect (negative):** A compatibility entry point, mutable live directory, live Postgres data directory, unverified dump, or version 3 Cashback archive cannot become restore input.

### S3 -- Quiesce every writer {#S3}
- **Do:** Stop every writer: Actual, the Actual proxy, Cashback, n8n, and task runners. Keep only the PostgreSQL engine available.
- **Expect:** Service-state evidence shows no process or connection that can mutate a selected source or target.
- **Expect (negative):** If any service state or lock owner is uncertain, do not restore.

### S4 -- Restore into empty isolated targets {#S4}
- **Do:** Create empty Actual and Cashback roots and an empty n8n database. Recheck their identities before restoring only into them.
- **Expect:** Each domain enters only its assigned target. Source hashes and safety copies remain unchanged.
- **Expect (negative):** Never overwrite a source or reuse one root as source and target. Never copy live Postgres data or replace its active database.

### S5 -- Read back every domain {#S5}
- **Do:** Before cutover, compare the targets with the manifests and receipts. Include balances, counts, n8n objects, the cursor, and terminal receipts.
- **Expect:** Actual balances match, and the closed ADCB card is AED 0 and historical. Cashback event and period counts match. n8n exposes 19 workflows plus the required credentials, Data Tables, receipts, MCP status, and cursor continuity.
- **Expect (negative):** Any mismatch, duplicate write, changed source hash, exposed secret, or indeterminate result fails the restore.

### S6 -- Prove rollback and health {#S6}
- **Do:** If readback fails, discard the targets and retain every source and safety copy. Otherwise, start isolated services against the targets.
- **Expect:** Failure leaves the prior roots selected and recoverable. Success produces healthy service and route checks while sources remain unchanged.
- **Expect (negative):** Do not delete old state or safety copies. Do not restart against a failed target or report success before operator acceptance.

## Success criteria
- SC1: S1-S6 record `PRECHECK -> QUIESCE -> SNAPSHOT-SEALED -> TARGET-EMPTY -> RESTORE -> READBACK -> ROLLBACK-READY -> RESTARTED/HEALTHY`, or the failed guard and `ABORT`.
- SC2: The receipt proves three distinct source/target pairs, target emptiness before restore, verified artifact identities, and unchanged source hashes after restore.
- SC3: Readback records Actual balances, historical ADCB AED 0, Cashback event and period counts, and all n8n checks named in S5.
- SC4: A failed drill preserves the prior roots and safety copies. A successful drill records healthy Actual, Cashback, n8n, and external routes.

## Known gaps
- G1: No completed protected restore drill or redacted receipt is available. This unaccepted gap blocks validation and keeps the journey in draft.
- G2: The approved disposable roots and service-repoint procedure for Actual and Cashback are not recorded. This unaccepted gap blocks S1 and S6.
- G3: n8n key recovery depends on the rootless stack owner and 1Password evidence outside this repository. This unaccepted gap blocks S1.
- G4: No user acceptance with a date exists for G1-G3. The gaps remain unaccepted, and an agent cannot waive them.

## Delta log
- None.
