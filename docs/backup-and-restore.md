# Backup and restore

Finance data has three independent persistence domains and must never be
presented as one database. The canonical Actual stack is
`/opt/stacks/finance-actual` and its backups are stored under
`/opt/backups/finance-actual`.

- Actual ledger files under `/opt/stacks/finance-actual/data`;
- cashback operational SQLite under the configured cashback data directory;
- n8n Postgres plus the n8n persistent volume for workflows, credentials,
  cursors, receipts, and transient binary references.

OneDrive evidence follows OneDrive retention/versioning and is not copied into
container backups.

## Actual and cashback

`deploy/actual/backup.sh` briefly pauses only Actual, its proxy when needed,
and cashback, copies the two data stores plus secret-free configuration, writes
checksums, and runs `verify-backup.py` in a disposable extraction directory. It
does not know about or pause n8n.

The version 4 cashback archive excludes `push_subscriptions`, `push_deliveries`, and
`push_state` from `cashback-events.sqlite3`. These tables contain browser push
credentials or ephemeral delivery state. The backup manifest lists the three
exclusions. It also excludes disposable `pre-deploy-*.sqlite3*` snapshots and
all SQLite sidecars. The verifier rejects an archive that contains rows in the
push tables or any member of that historical snapshot family.
Push subscriptions are recreated by the browser after restore; cashback events,
period state, and configuration remain restorable.

The verifier recognizes legacy version 3 manifests but rejects one without the
push-state classification. Create a new version 4 backup before restore.

Backups live at `/opt/backups/finance-actual/<UTC timestamp>/`. Restore only
after checksum verification, with Actual and cashback stopped, and retain the
pre-restore copies until UI/API balances and cashback event counts agree.

## n8n Postgres

The pinned n8n platform commit
[`a3fa5487b250dc46c14ee460a4dc2d34a22c3867`](https://github.com/srobroek/n8n/tree/a3fa5487b250dc46c14ee460a4dc2d34a22c3867)
owns [`scripts/backup.sh`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/scripts/backup.sh),
[`scripts/doctor.sh`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/scripts/doctor.sh),
and [`scripts/restore-disposable.sh`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/scripts/restore-disposable.sh).
The backup creates a PostgreSQL custom-format dump under
`/opt/backups/n8n`, writes a SHA-256 sidecar, and retains 30 days by
default. Schedule it independently of the Actual backup.

Before restoring n8n:

1. stop n8n and task runners but keep Postgres running;
2. verify the selected dump checksum;
3. create a safety dump of the current database;
4. restore into a new empty database with `pg_restore`;
5. point n8n at that database and run `scripts/doctor.sh`;
6. verify workflow count.
7. verify credential availability.
8. verify Data Tables.
9. verify execution receipts.
10. verify MCP status before deleting the old database.

The rootless stack owner performs key recovery with the pinned platform
[`scripts/recover-retained-n8n-key.sh`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/scripts/recover-retained-n8n-key.sh)
procedure. The finance checkout does not recreate, rotate, or print the n8n
encryption key. A failed restore retains the pre-restore database and safety
dump until the owner records a redacted recovery receipt.

Never restore Postgres by copying its live data directory. Never restore a dump
over an active n8n main. Store the n8n encryption key separately in 1Password.
Database credentials alone cannot decrypt n8n credentials.

Before deleting old state:

- Keep the workflow count at 19.
- Check inactive and unpublished state.
- Review four-table digest.
- Review source cursor.
- Check terminal receipts.
- Check Cloudflare route status.
- When readback lacks evidence, keep rollback open.

## Required drill

Production readiness requires one disposable restore of all three domains and
verification that:

- Actual UI/API account balances match;
- the closed ADCB card is AED 0 and remains historical;
- cashback events and period states match their backup receipt;
- n8n can read its Data Tables and resume a cursor without duplicate writes;
- Cloudflare routes return healthy Actual and n8n pages.
