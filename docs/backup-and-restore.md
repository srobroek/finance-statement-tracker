# Backup and restore

The CI host runs a cold filesystem backup at approximately 03:15 Asia/Dubai. Actual, Cashback Control, and the ingestion worker stop briefly so the Actual files, cashback SQLite database, and durable ingestion jobs are captured consistently. The stateless Nginx proxy stops first and starts last. The backup restarts the existing containers by exact name; it never invokes Compose or pulls an image, so a registry login cannot strand services after a successful archive. Only services that were running before the backup are restarted.

Backups are stored outside the stack at `/opt/backups/finance-actual-poc/<UTC timestamp>/` and contain:

- `finance-data.tar.gz` with `actual-data/`, `cashback-data/`, `ingestion-data/`, and a secret-free `configuration/` snapshot of all three Compose projects and mounted cashback configuration.
- `SHA256SUMS` for integrity verification.
- `manifest.json` with schema, scope, and container metadata.

The stack `.env` is deliberately excluded. Back up its secret values separately in the approved password manager. The default retention is 30 days and can be changed with `FINANCE_BACKUP_RETENTION_DAYS` in a systemd override.

## Verify a backup

```bash
cd /opt/backups/finance-actual-poc/<timestamp>
sha256sum -c SHA256SUMS
tar -tzf finance-data.tar.gz | head
```

## Restore

Restoration replaces live data. Confirm the exact timestamp first, then:

```bash
sudo podman stop finance-actual-proxy finance-actual-ingestion finance-actual-poc finance-cashback-control
sudo cp -a /opt/stacks/finance-actual-poc/data "/opt/stacks/finance-actual-poc/data.pre-restore.$(date -u +%Y%m%dT%H%M%SZ)"
sudo cp -a /opt/stacks/finance-actual-poc/cashback-data "/opt/stacks/finance-actual-poc/cashback-data.pre-restore.$(date -u +%Y%m%dT%H%M%SZ)"
sudo cp -a /opt/stacks/finance-actual-poc/ingestion-data "/opt/stacks/finance-actual-poc/ingestion-data.pre-restore.$(date -u +%Y%m%dT%H%M%SZ)"
cd /opt/backups/finance-actual-poc/<timestamp>
sha256sum -c SHA256SUMS
restore_root="$(sudo mktemp -d /opt/backups/finance-restore.XXXXXX)"
sudo tar -C "${restore_root}" -xzf finance-data.tar.gz
sudo rsync -a --delete "${restore_root}/actual-data/" /opt/stacks/finance-actual-poc/data/
sudo rsync -a --delete "${restore_root}/cashback-data/" /opt/stacks/finance-actual-poc/cashback-data/
sudo rsync -a --delete "${restore_root}/ingestion-data/" /opt/stacks/finance-actual-poc/ingestion-data/
sudo podman start finance-actual-poc finance-cashback-control finance-actual-ingestion finance-actual-proxy
sudo podman ps --filter name=finance-
curl -fsS http://127.0.0.1:5006/ >/dev/null
curl -fsS http://127.0.0.1:5010/api/health
curl -fsS http://127.0.0.1:5020/api/health
```

Never restore over running containers, never restore a backup with a failed checksum, and retain the pre-restore copies until balances and event counts have been verified.

## Password reset recovery copy

An Actual password reset may create a one-off sibling copy of
`data/server-files/account.sqlite`. That file is not part of the normal backup
contract and must not be treated as a substitute for a complete cold backup.
Remove it only after the new login is verified and at least one scheduled cold
backup has completed successfully.
