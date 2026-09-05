# Cashback deployment rollback

The deployment first stops `finance-cashback-control`, verifies its canonical
data mount and immutable image identity, and creates a private snapshot under
`/opt/backups/finance-cashback`. No mounted configuration or runtime environment
is replaced until snapshot verification succeeds. A failure leaves the writer
stopped. This is containment, not a completed rollback.

`manifest.json` records the original image digest, file hashes, original modes
and ownership, absent files, and SQLite table counts. The backup includes the
stack Compose/environment, all four mounted configurations, shared runtime
environment/template/renderer, and an exact SQLite backup including committed
WAL data and push state. It contains secrets and must remain root-private; never
upload it to Actions artifacts or attach it to an issue. Regular sanitized
archival backups remain a separate operation.

Verification checks hashes and SQLite integrity, restores SQLite into a
disposable file, and checks table readback. It does not claim a production
rollback, authenticated application readback, or restored external credentials.

For an operator-reviewed rollback:

1. Stop Cashback and confirm no writer or external process has the database
   open. Preserve a separate verified snapshot of the failed/current deployment
   first so any post-deployment transactions remain recoverable.
2. Run `sudo python3 deploy/cashback/predeploy-backup.py --verify-only
   --destination /opt/backups/finance-cashback/CHOSEN_SNAPSHOT` from the reviewed
   source checkout against the chosen private snapshot. Check its
   `verification.json` manifest hash, recorded image digest and current local
   image-store availability. Do not restore an unverified snapshot.
3. Review changes since the snapshot, particularly new transactions and shared
   `/opt/stacks/finance-runtime/.env` changes used by other services. Reconcile
   post-deployment rows before any database rollback; do not silently discard
   them. Coordinate any restoration of the shared environment.
4. Restore only the manifest-listed files from `files/` to their corresponding
   `/opt/stacks/` paths, retaining recorded uid/gid/mode. Review files recorded
   absent before removing newly installed counterparts. With writers stopped,
   preserve current SQLite/WAL/SHM files separately before installing the
   verified database; do not mix old WAL with a restored database.
5. Render and review restored Compose configuration using the manifest's exact
   `rollback_image` as `CASHBACK_IMAGE`. Start only the Cashback project with
   `--pull never`; do not start ingestion schedulers until authenticated
   dashboard/event readback, image digest, source counts and replay pass.

The workflow does not automatically restore financial state or start an
unverified prior configuration. Retain rollback evidence in rollout issue #87
using hashes/counts/status only.
