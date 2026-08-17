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

There is no second standalone import executable or `ACTUAL_DRY_RUN=false`
compatibility path. `actualctl.mjs` is the only bridge command that can import
transactions, and production reaches it only through the ingestion worker.

See `docs/actual-production.md` for the operating procedure and PowerShell wrappers.
## Read-only tag reports

`actualctl.mjs tag-report` provides `any`, `all`, and excluded-tag filters plus grouping by category, payee, account, or tag. It reads the authoritative Actual budget and never creates a companion ledger. See `docs/tag-reporting.md` for examples and the documented native-report limitations.
