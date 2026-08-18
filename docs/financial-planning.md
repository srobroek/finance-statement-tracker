# Financial planning compiler

`financial-planning` turns a verified Actual snapshot into reviewable schedule
and monthly-envelope proposals. It never writes to Actual.

```powershell
python -m finance_tracker.cli financial-planning `
  --snapshot runtime/actual-note-cleanup-snapshot.json `
  --config config/financial-planning.json `
  --as-of 2026-08-18 `
  --output runtime/reports/financial-planning.json
```

Budget recommendations use completed months only. Transfers, income, refunds,
card payments, and review rows are excluded. The recommended amount is the
median active-month spend plus the configured buffer, rounded up to a useful
envelope increment. Sparse categories remain unbudgeted instead of producing
false precision.

Schedules are allowlisted in configuration. The compiler groups each recurring
family by account, requires multiple observed months, and proposes a monthly
date plus an evidenced amount range. Schedules never post transactions. Card
statement reminders remain statement-derived one-time schedules because their
amount changes every cycle.

The report is advisory until the final clean re-ingestion passes. Only then are
accepted values copied into `config/actual-bootstrap.json` or Actual's native
budget-automation UI.
