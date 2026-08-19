# Actual Budget bridge

The Python worker owns PDF parsing, normalization, evidence matching, static rules, and cashback arithmetic. This optional Node bridge performs the authenticated write through Actual Budget's official `@actual-app/api` package.

`actualctl.mjs` is the supported command surface. It provides health inspection, declarative bootstrap, read-only snapshots, and two-phase imports. Each import uses stable `imported_id` values and sets `reimportDeleted=false`. A committed statement with an evidenced future due date and a positive balance also creates or updates a one-time, non-posting payment reminder in Actual; forecasts and past dates do not create reminders.

```powershell
npm install
$env:ACTUAL_SERVER_URL = "https://actual.example"
$env:ACTUAL_PASSWORD = "..."
$env:ACTUAL_SYNC_ID = "..."
node actualctl.mjs doctor
node actualctl.mjs bootstrap --config ..\..\config\actual-bootstrap.json
node actualctl.mjs import --input .\statement-run.json
```

`bootstrap` plans by default and mutates only with `--apply`. `import` performs a dry-run by default. A low-level commit requires both `--commit` and `ALLOW_ACTUAL_WRITES=true`, and always repeats the complete preflight before writing. Operator-facing statement and browser imports must use the PowerShell ingestion wrappers so AI completion, evidence linkage, review state, source identity, and container-level write gates are enforced before this bridge is reached.

`repair-transactions` is the guarded exception for correcting an already imported row when Actual's import deduplication intentionally refuses to update it. Its versioned plan must identify every row by account, date, and `imported_id`, state the exact current amount, and provide only an exact sign reversal. It plans by default; `--apply` additionally requires `ALLOW_ACTUAL_WRITES=true`. The command refuses missing, duplicate, transferred, or drifted rows, re-reads every target after syncing, and is idempotent after a successful repair.

There is no second standalone import executable or `ACTUAL_DRY_RUN=false`
compatibility path. `actualctl.mjs` is the only bridge command that can import
transactions, and production reaches it only through the ingestion worker.

See `docs/actual-production.md` for the operating procedure and PowerShell wrappers.

Before planning or applying a production replacement, generate a manual-state
preservation report from the exact production snapshot and rebuild manifests:

```powershell
node manual-state-audit.mjs --root ../.. --validation ../../config/full-ingestion-validation.json --snapshot ../../runtime/audit/live-full-chat-audit-snapshot.json --output ../../runtime/audit/manual-state-preservation.json
```

The report flags reconciliations, transfers, schedules, splits, and managed-field
drift, then fingerprints every row that must remain untouched. A replacement
with blocking rows requires `ALLOW_ACTUAL_MANUAL_STATE_REPLACEMENT=true` and the
exact reviewed report digest via `--approve-preservation-sha256`. Preserved rows
are verified again after import. This guard does not replace review of the exact
production delta.

## Read-only tag reports

`actualctl.mjs tag-report` provides `any`, `all`, and excluded-tag filters plus grouping by category, payee, account, or tag. It reads the authoritative Actual budget and never creates a companion ledger. See `docs/tag-reporting.md` for examples and the documented native-report limitations.
