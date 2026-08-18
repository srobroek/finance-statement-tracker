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

Production uses `integrations/actual/production-rebuild.mjs`. It exports an
exclusive Actual `.zip` backup and SHA-256 sidecar before deleting only rows in
the configured account plus imported-ID scope. Applying requires both `--apply`
and `ALLOW_ACTUAL_LEDGER_REPLACEMENT=true`; manual and unrelated rows are
preserved.

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

AI response files are cumulative JSON arrays named after each source id. Replay
them until the ordered handoff reaches a fixed point; mark the handoff complete
only on the final pass:

```powershell
& .\scripts\restage-full-history.ps1 `
  -AIResponsesRoot .\runtime\full-restage-ai\current
& .\scripts\restage-full-history.ps1 `
  -AIResponsesRoot .\runtime\full-restage-ai\current `
  -AIHandoffComplete
```

The second command fails closed if any source emits a response-free request.
Private backfill decisions can be converted into those exact arrays without
putting personal merchant data in executable code or repository configuration:

```powershell
python -m finance_tracker.full_restage_ai `
  --run-root .\runtime\full-restage\<run> `
  --decisions .\runtime\full-restage-ai\decisions.json `
  --output-root .\runtime\full-restage-ai\current `
  --report .\runtime\full-restage-ai\build-report.json
```

The builder merges each new transaction/policy response into the existing
per-source array, so later fixed-point rounds retain earlier decisions.

Export the exact staged manifests and build catalogue-backed evidence links
before the final pass:

```powershell
& .\scripts\export-full-restage-manifests.ps1 `
  -RunRoot .\runtime\full-restage\<run>
python -m finance_tracker.full_restage_evidence `
  --manifests-root .\runtime\full-restage\<run>\manifests `
  --catalogue '.\Finance Evidence\catalogue.json' `
  --output-root .\runtime\full-restage-evidence\current
```

Pass `-EvidenceLinksRoot .\runtime\full-restage-evidence\current` to the next
restage. Each link is accepted only when its exact imported transaction ID is
present in that source manifest.

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
