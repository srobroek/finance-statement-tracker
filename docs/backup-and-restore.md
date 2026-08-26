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

Use one real production ingestion followed by an identical replay and controlled
`n8n` restart for semantic acceptance. Keep an `Actual` reset as a fallback-only
recovery action. A reset does not replace the ingestion, replay, or restart
evidence.

### reset after login failure

Confirm that current authentication is unusable. Record that failure with the
shared ingestion `run_id`. Bind the reset receipt to that same `run_id`.
Do not reset `Actual` by default. Use the supported reset at version `26.8.1`.

Keep runtime data under `/opt/stacks/finance-actual-poc/data`. This checkout
provides no reset command. Keep storage private. Record only redacted receipt
fields. Keep this credential reset runtime-only. Do not reset ledger data, Data
Tables, or source cursors.

Before reset, retain these protected files:

- mode-`0600` prestate receipt
- verified archive under `/opt/backups/finance-actual-poc`
- its `SHA256SUMS` checksum

After reset, check that these readbacks succeed:

- `@actual-app/api` reads the expected budget and its account balances
- the ledger UI reads the same budget and account balances
- API and UI authentication both succeed
- API and UI identify the same data

Reset is not acceptance evidence. After reset, repeat these checks:

- one real ingestion
- identical replay
- controlled `n8n` restart

### production apply stays disabled

This checkout keeps production apply disabled. The production CLI accepts no
disposable result input. It has no export restore command. Its result receipt does
not prove server, sync, or budget identity. Keep `--apply` out of commands from
this checkout.

Keep the production target unchanged. This checkout has no operator-owned apply
procedure or tested receipt. Such a procedure binds the `Actual` target to a
pre-apply export. It validates the export checksum. It restores that exact export.
It reads the budget back through the API and UI.

The restore proof compares archive and target identity. An archive checksum
proves only the bytes. It cannot identify the server, sync session, or budget.
Record the target and prestate in an operator receipt. Read the restored budget
through the API and UI. Keep the exact checksum in that receipt.

The four-table runner supports disposable and production modes. This checkout
documents the disposable path only. Its rollback is not an `Actual` restore. Retain
source files until a separate operator readback passes.

## required drill

Complete one disposable restore for each domain. Verify that these results hold:

- ledger UI and API balances agree
- the closed ADCB card has an AED 0 balance
- Cashback events and period state match the backup receipt
- `n8n` reads Data Tables and resumes a cursor without duplicate writes
