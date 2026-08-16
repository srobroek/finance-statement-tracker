# Backup and restore

The CI host runs a cold filesystem backup at approximately 03:15 Asia/Dubai. Actual and Cashback Control stop briefly so the Actual files and cashback SQLite database are captured consistently. The stateless Nginx proxy stops first and starts last: Podman may give Actual a new address after restart, so restarting Nginx forces a fresh service-name lookup and prevents a persistent post-backup `502`. The timer restarts only services that were running before the backup.

Backups are stored outside the stack at `/opt/backups/finance-actual-poc/<UTC timestamp>/` and contain:

- `finance-data.tar.gz` with `data/` and `cashback-data/`.
- `SHA256SUMS` for integrity verification.
- `manifest.json` with image and scope metadata.

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
cd /opt/stacks/finance-actual-poc
sudo docker compose stop actual cashback-control
sudo cp -a data "data.pre-restore.$(date -u +%Y%m%dT%H%M%SZ)"
sudo cp -a cashback-data "cashback-data.pre-restore.$(date -u +%Y%m%dT%H%M%SZ)"
cd /opt/backups/finance-actual-poc/<timestamp>
sha256sum -c SHA256SUMS
sudo tar -C /opt/stacks/finance-actual-poc -xzf finance-data.tar.gz
cd /opt/stacks/finance-actual-poc
sudo docker compose up -d
sudo docker compose ps
curl -fsS http://127.0.0.1:5006/ >/dev/null
curl -fsS http://127.0.0.1:5010/api/health
```

Never restore over running containers, never restore a backup with a failed checksum, and retain the pre-restore copies until balances and event counts have been verified.

## Password reset recovery copy

An Actual password reset may create a one-off sibling copy of
`data/server-files/account.sqlite`. That file is not part of the normal backup
contract and must not be treated as a substitute for a complete cold backup.
Remove it only after the new login is verified and at least one scheduled cold
backup has completed successfully.
