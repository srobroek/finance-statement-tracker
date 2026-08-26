# backup and restore

Finance uses three persistence domains:

- `Actual Budget` ledger files
- Cashback Control SQLite state
- `n8n` Postgres and its persistent volume

Keep each domain separate. `OneDrive` holds source evidence. The backup process
does not copy that evidence into container archives.

## ledger and cashback

[`deploy/actual-poc/backup.sh`](../deploy/actual-poc/backup.sh) pauses the
ledger and Cashback Control. It copies both data stores. It writes checksums. It
runs [`verify-backup.py`](../deploy/actual-poc/verify-backup.py) in a disposable
directory. It does not pause `n8n`.

The version 4 Cashback archive excludes these tables:

- `push_subscriptions`
- `push_deliveries`
- `push_state`

Those tables hold browser push credentials or delivery state. It also excludes
disposable `pre-deploy-*.sqlite3*` files and SQLite sidecars. The
verifier rejects an archive that contains excluded rows.

After browser registration, push subscriptions return. The archive retains
Cashback events.
The archive retains period state. The archive retains configuration.

Version 3 manifests need push-state classification. Use a version 4 archive for
a restore.

Archives live under `/opt/backups/finance-actual-poc/<UTC timestamp>/`. Verify
that the checksum is valid. Stop the ledger and Cashback Control. Retain the
pre-restore copy.
Compare ledger balances through UI and API. Compare Cashback event counts.

## workflow database

The pinned `n8n` platform commit owns the backup scripts:
[`a3fa5487b250dc46c14ee460a4dc2d34a22c3867`](https://github.com/srobroek/n8n/tree/a3fa5487b250dc46c14ee460a4dc2d34a22c3867).

- [`backup.sh`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/scripts/backup.sh)
- [`doctor.sh`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/scripts/doctor.sh)
- [`restore-disposable.sh`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/scripts/restore-disposable.sh)
- [`recover-retained-n8n-key.sh`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/scripts/recover-retained-n8n-key.sh)

The backup script writes a PostgreSQL custom-format dump under `/opt/backups/n8n`.
It writes a SHA-256 sidecar. It retains 30 days by default. Schedule this
backup separately from the ledger backup.

Restore into a new empty database:

1. stop `n8n` and its task runners
2. keep Postgres running
3. verify that the dump checksum is valid
4. create a safety dump of the existing database
5. restore the dump with `pg_restore`
6. point `n8n` at the new database
7. run `scripts/doctor.sh`
8. check that the workflow count is 19
9. check credential availability
10. check Data Tables
11. check execution receipts
12. check MCP status

The stack owner handles key recovery with
[`recover-retained-n8n-key.sh`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/scripts/recover-retained-n8n-key.sh).
The finance checkout does not recreate, rotate, or print the encryption key.
Keep the pre-restore database and safety dump until a recovery receipt exists.

Do not copy a live Postgres data directory. Do not restore over an active main
process. Store the encryption key in 1Password. Database credentials cannot
decrypt `n8n` credentials.

Deletion checklist:

- workflow count is 19
- inactive and unpublished state
- four-table digest
- source cursor
- terminal receipts
- Cloudflare route status
- rollback remains open when readback lacks evidence

## greenfield rebuild

Use [`full-ingestion-validation.md`](full-ingestion-validation.md) for the
rebuild audit. Keep the ledger, Cashback Control, and `n8n` as separate restore
domains. Install the locked `Actual` dependencies:

```sh
npm ci --prefix integrations/actual
```

`full-rebuild.mjs` imports `@actual-app/api`. Run the disposable rebuild from the
repository root. Supply all seven required options:

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

The command needs these options:

- `--root`
- `--validation`
- `--bootstrap`
- `--start`
- `--end`
- `--snapshot`
- `--result`

The command creates a temporary data directory. It does not clear production
data.

Use this result receipt shape for a passing run:

```json
{
  "schema_version": "actual-disposable-full-rebuild-v1",
  "status": "PASS",
  "replay": {"verification": {"status": "PASS"}}
}
```

Accept the rebuild only with a passing audit and replay verification.

### production apply

Production gate steps:

- use the disposable result as the input to a production plan
- run the production command without `--apply`
- review the preservation report
- export the replacement gate
- run the guarded apply

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

If the plan reports preservation blockers, export
`ALLOW_ACTUAL_MANUAL_STATE_REPLACEMENT=true`. Add the exact
`--approve-preservation-sha256` value from the reviewed plan.

Read the ledger through API and UI. Read Cashback events and period state. Read
the `n8n` Data Tables twice. Confirm that the second read is a no-op. An apply
result is acceptable with `status: APPLIED` and preservation verification at
`status: PASS`.

### ledger rollback

Bind each restore receipt to these values:

- target: `finance-actual-poc` with data under `/opt/stacks/finance-actual-poc/data`
- prestate: `runtime/audit/actual-production-pre-apply.backup`
- checksum: `runtime/audit/actual-production-pre-apply.backup.sha256.json`
- result: `runtime/audit/actual-production-apply-result.json`

The `--backup` path names the pre-apply archive. The operator receipt names the
target service. It names the archive path and SHA-256. Before apply, the receipt
records the target state. It records the restore archive and its checksum sidecar.
These bindings prevent a restore for another ledger.

When readback fails, stop further apply operations. Restore the prestate archive
through the operator-owned `Actual` restore procedure. Read the API and UI
again. After a restore, repeat every readback. Keep the receipt and archive
until those checks pass.

The four-table runner is disposable-only. No production four-table `n8n`
cutover script exists in this repository. Use the runner rollback only with a
forward receipt and a named operator acknowledgment. Retain source files until
the rollback readback passes.

## required drill

Complete one disposable restore for each domain. Verify that these results hold:

- ledger UI and API balances agree
- the closed ADCB card has an AED 0 balance
- Cashback events and period state match the backup receipt
- `n8n` reads Data Tables and resumes a cursor without duplicate writes
- Cloudflare routes return the expected ledger and `n8n` pages
