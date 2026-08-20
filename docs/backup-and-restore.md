# Backup and restore

Finance data has three independent persistence domains and must never be
presented as one database:

- Actual ledger files under `/opt/stacks/finance-actual-poc/data`;
- cashback operational SQLite under the configured cashback data directory;
- n8n Postgres plus the n8n persistent volume for workflows, credentials,
  cursors, receipts, and transient binary references.

OneDrive evidence follows OneDrive retention/versioning and is not copied into
container backups.

## Actual and cashback

`deploy/actual-poc/backup.sh` briefly pauses only Actual, its proxy when needed,
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

Backups live at `/opt/backups/finance-actual-poc/<UTC timestamp>/`. Restore only
after checksum verification, with Actual and cashback stopped, and retain the
pre-restore copies until UI/API balances and cashback event counts agree.

## n8n Postgres

The n8n deployment repository provides `scripts/backup-postgres.sh`. It creates
a PostgreSQL custom-format dump under `/opt/backups/finance-n8n`, writes a
SHA-256 sidecar, and retains 30 days by default. Schedule it independently of
the Actual backup.

Before restoring n8n:

1. stop n8n and task runners but keep Postgres running;
2. verify the selected dump checksum;
3. create a safety dump of the current database;
4. restore into a new empty database with `pg_restore`;
5. point n8n at that database and run `scripts/doctor.sh`;
6. verify workflow count, credentials availability, Data Tables, execution
   receipts, and MCP status before deleting the old database.

Never restore Postgres by copying its live data directory. Never restore a dump
over an active n8n main. The n8n encryption key must be preserved separately in
1Password; database credentials alone cannot decrypt n8n credentials.

## Required drill

Production readiness requires one disposable restore of all three domains and
verification that:

- Actual UI/API account balances match;
- the closed ADCB card is AED 0 and remains historical;
- cashback events and period states match their backup receipt;
- n8n can read its Data Tables and resume a cursor without duplicate writes;
- Cloudflare routes return healthy Actual and n8n pages.
