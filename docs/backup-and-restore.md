# Backup and restore

The CI host runs a quiesced filesystem backup at approximately 03:15 Asia/Dubai. Actual, Cashback Control, and the ingestion worker are paused briefly, then the host flushes pending filesystem writes before copying the Actual files, cashback SQLite database, and durable ingestion jobs. The same containers are unpaused after the snapshot. The backup never stops, recreates, or pulls a container, so a registry login or runtime monitor cannot strand services after a successful archive. Only services that were running before the backup are paused.

Backups are stored outside the stack at `/opt/backups/finance-actual-poc/<UTC timestamp>/` and contain:

- `finance-data.tar.gz` with `actual-data/`, `cashback-data/`, `ingestion-data/`, and a secret-free `configuration/` snapshot of all three Compose projects and mounted cashback configuration.
- `SHA256SUMS` for integrity verification.
- `manifest.json` with schema, scope, and container metadata.
- `verification.json` with the archive digest, extraction count, parsed JSON
  count, and every SQLite database that passed `PRAGMA integrity_check`.

The stack `.env` is deliberately excluded. Back up its secret values separately in the approved password manager. The default retention is 30 days and can be changed with `FINANCE_BACKUP_RETENTION_DAYS` in a systemd override.

Every scheduled backup is restored into a disposable temporary directory before
the backup job succeeds. `verify-backup.py` rejects checksum drift, path
traversal, links and special files, a missing state store, malformed JSON, an
included `.env`, or any SQLite integrity failure. It never writes to the live
Actual, cashback, or ingestion directories. The five-minute health monitor
requires a matching successful verification receipt on the newest backup, so a
fresh archive without proven readable contents is unhealthy.

## Verify a backup

```bash
cd /opt/backups/finance-actual-poc/<timestamp>
sha256sum -c SHA256SUMS
tar -tzf finance-data.tar.gz | head
cat verification.json

sudo /opt/stacks/finance-actual-poc/verify-backup.py \
  --backup-root /opt/backups/finance-actual-poc \
  --backup-path /opt/backups/finance-actual-poc/<timestamp>
```

Pause/unpause is intentional on the Podman-backed host: restarting a container
from a one-shot systemd service can attach its `conmon` process to the backup
unit's cgroup. Quiescing avoids that lifecycle coupling while preserving a
stable, flushable snapshot boundary. Podman can still replace `conmon` while
unpausing, so the systemd unit uses `KillMode=process`; systemd supervises the
backup script but does not terminate the independent container monitor when the
one-shot completes.

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
