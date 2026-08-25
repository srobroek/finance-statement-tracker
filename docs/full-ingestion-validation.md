# Full ingestion validation

The final migration gate is a clean rebuild, not an in-place assumption that the
current ledger is complete. The versioned inventory in
`config/full-ingestion-validation.json` names every authoritative statement and
browser-capture manifest included in the POC ledger.

Run the read-only audit against a fresh full-period Actual snapshot:

```powershell
python -m finance_tracker.cli full-ingestion-audit `
  --config config/full-ingestion-validation.json `
  --snapshot runtime/actual-note-cleanup-snapshot.json `
  --output runtime/reports/full-ingestion-audit.json
```

The audit fails closed for:

- duplicate or missing imported IDs;
- unexpected ledger records inside the configured account/source scope;
- account, date, amount, imported-payee, or cleared-state drift;
- statement manifests that do not balance or still require review;
- non-canonical Actual notes;
- missing or hash-mismatched statement evidence; and
- missing or hash-mismatched source artifacts referenced by manifests.

A statement row omitted because an earlier browser row has the same unique
date, amount, and normalized imported payee is reported as an explicit
cross-source suppression. This mirrors the guarded direct Actual writer and never
hides an ambiguous many-to-one match.

## Final rebuild sequence

1. Finish rule, evidence, schedule, budget, and report configuration.
2. Take and verify an Actual backup and a full pre-clean snapshot.
3. Re-stage every raw statement and browser export with the current code,
   rules, history, AI policy, and note contract into a new immutable run set.
4. Require every statement to balance and resolve every review blocker.
5. Run this audit against the current ledger to expose source gaps before any
   deletion.
6. Rebuild the scoped accounts in a disposable Actual file first; import the
   immutable run set, snapshot it, and require a clean audit.
7. Only after the disposable rebuild passes, clean the production transactions,
   import the exact same run set, and require a second clean audit.
8. Verify balances, schedules, budgets, reports, evidence links, category
   coverage, and idempotent replay before declaring the migration complete.

The audit is deliberately read-only. Cleanup and import remain separately
guarded production operations so a failed check cannot partially mutate the
ledger.
