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

## greenfield rebuild

Use [`full-ingestion-validation.md`](full-ingestion-validation.md) for the
ledger rebuild audit. Keep Actual, Cashback Control, and n8n as separate
restore domains. Install the Actual runner's locked dependencies before the
first run; it imports `@actual-app/api`:

```sh
npm ci --prefix integrations/actual
```

Run the disposable rebuild from the repository root. All seven options are
required: `--root`, `--validation`, `--bootstrap`, `--start`, `--end`,
`--snapshot`, and `--result`.

```sh
node integrations/actual/full-rebuild.mjs \
  --root . \
  --validation config/full-ingestion-validation.json \
  --bootstrap config/actual-bootstrap.json \
  --start 2026-01-01 \
  --end 2026-08-31 \
  --snapshot runtime/audit/disposable-full-rebuild-snapshot.json \
  --result runtime/audit/disposable-full-rebuild-result.json
```

The command creates a temporary Actual data directory and does not clear
production data. A passing result receipt has this shape; accept it only when
the full-ingestion audit and both replay statuses pass:

```json
{
  "schema_version": "actual-disposable-full-rebuild-v1",
  "status": "PASS",
  "replay": {"verification": {"status": "PASS"}}
}
```

Complete the greenfield recovery in this order:

1. Before any reset, snapshot Actual, Cashback Control, and n8n. Save the
   verified Actual archive, Cashback archive, n8n dump, and checksum sidecar.
2. Restore n8n into a new empty disposable database with
   `restore-disposable.sh`; import the exact reviewed manifest set into the
   disposable Actual target.
3. Run the seven-flag disposable rebuild command. Compare its Actual snapshot
   with API/UI readback, compare Cashback events and period state with the
   backup receipt, and run the n8n Data Table check twice. The second run must
   be a no-op.
4. Record the disposable result and readbacks in the acceptance ledger. Do not
   apply to production until the ledger has the required evidence.
5. For production, follow the guarded [Actual production procedure](actual-production.md):
   plan first with the same arguments but without `--apply`, then run the
   exact production apply below with
   `ALLOW_ACTUAL_LEDGER_REPLACEMENT=true`. If the plan reports preservation
   blockers, also provide `ALLOW_ACTUAL_MANUAL_STATE_REPLACEMENT=true` and the
   exact `--approve-preservation-sha256` value printed by the plan:

   ```sh
   export ALLOW_ACTUAL_LEDGER_REPLACEMENT=true
   node integrations/actual/production-rebuild.mjs \
     --root . \
     --validation config/full-ingestion-validation.json \
     --bootstrap config/actual-bootstrap.json \
     --start 2026-01-01 \
     --end 2026-08-31 \
     --backup runtime/audit/actual-production-pre-apply.backup \
     --snapshot runtime/audit/actual-production-post-apply-snapshot.json \
     --result runtime/audit/actual-production-apply-result.json \
     --apply
   ```

   Add `--approve-preservation-sha256 <plan preservation sha256>` to this
   command only when the reviewed plan requires the manual-state override.
6. Read back Actual through the API and UI, verify the production result is
   `status: APPLIED` with preservation verification `PASS`, and repeat the
   Cashback and n8n readbacks. Record every receipt in the acceptance ledger.
7. Retain the pre-apply Actual archive and checksum sidecar until all readbacks
   pass. If any readback fails, stop further apply operations and use the
   retained archive with the operator restore procedure, then repeat readback.
8. The four-table runner is disposable-only. Use its `rollback` only after the
   forward receipt and named-operator acknowledgment exist; retain both source
   files until rollback readback passes.

## Required drill

Production readiness requires one disposable restore of all three domains and
verification that:

- Actual UI/API account balances match;
- the closed ADCB card is AED 0 and remains historical;
- cashback events and period states match their backup receipt;
- n8n can read its Data Tables and resume a cursor without duplicate writes;
- Cloudflare routes return healthy Actual and n8n pages.
