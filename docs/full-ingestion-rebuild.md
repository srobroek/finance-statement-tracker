# Full ingestion rebuild

The final production cleanup is a controlled replacement, not an in-place
attempt to repair historical drift. It is intentionally blocked until every
source has been freshly staged with the current pipeline revision and the
disposable rebuild passes.

## Required sequence

1. Freeze scheduled Actual statement commits. Live cashback notification
   ingestion may continue because it uses the companion store.
2. Archive and hash every source statement in the evidence catalogue.
3. Restage every statement and browser capture with current normalization,
   static rules, history matching, AI responses, evidence links, and the note
   contract. No old manifest may be silently reused.
4. Rebuild a temporary local Actual budget from the exact manifest set. Browser
   captures are imported before statements so the guarded cross-source
   duplicate suppression behaves the same on every rebuild.
5. Run `full-ingestion-audit` against the disposable snapshot. It must report
   `PASS`, including zero missing IDs, unexpected IDs, field mismatches,
   noncanonical notes, untracked rows, and evidence failures.
6. Back up production and verify the archive before deleting anything.
7. Delete only transactions covered by the versioned account and imported-ID
   scope. Preserve unrelated/manual data unless it has an explicit replacement
   record.
8. Import the exact disposable manifest set, then reapply bootstrap, schedules,
   budgets, budget automations, and dashboards.
9. Run the same audit against production, replay every manifest, and require
   both a `PASS` audit and zero duplicate imported IDs before unfreezing jobs.

## Disposable rebuild

Create the fresh staging set first. This command submits `STAGE` jobs only and
cannot write to Actual:

```powershell
& .\scripts\restage-full-history.ps1 -PlanOnly
& .\scripts\restage-full-history.ps1
```

Its timestamped `runtime/full-restage/<run>/summary.json` is the AI/evidence
handoff inventory and records the exact durable manifest paths used by the
rebuild.

After every handoff is completed and every result is review-free:

```powershell
Push-Location integrations/actual
node full-rebuild.mjs `
  --root ../.. `
  --validation ../../config/full-ingestion-validation.json `
  --bootstrap ../../config/actual-bootstrap.json `
  --start 2024-01-01 `
  --end 2026-12-31 `
  --snapshot ../../runtime/audit/disposable-full-rebuild-snapshot.json `
  --result ../../runtime/audit/disposable-full-rebuild-result.json
Pop-Location

python -m finance_tracker.cli full-ingestion-audit `
  --config config/full-ingestion-validation.json `
  --snapshot runtime/audit/disposable-full-rebuild-snapshot.json `
  --output runtime/audit/disposable-full-rebuild-audit.json
```

The rebuild uses an isolated temporary Actual data directory and removes only
that validated temporary directory after the snapshot is written. It never
connects to or mutates production.

Actual Category Learning remains disabled throughout. Existing deterministic
rules still run during import; our history and scoped AI stages handle only
fields unresolved by those rules.
