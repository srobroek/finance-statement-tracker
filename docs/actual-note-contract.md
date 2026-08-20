# Actual transaction note contract

Actual notes are a compact human-facing annotation layer. Import provenance,
Outlook IDs, attachment IDs, source URLs, and deduplication identities belong
in the ingestion manifest, evidence catalogue, or `imported_id`; they must not
be copied into notes.

## Canonical grammar

```text
#semantic-tag #namespace:value | Doc: Finance Evidence/YYYY/MM/vendor/file.ext | Review: controlled-reason | Memo: human text
```

The sections are optional but, when present, always appear in this order:

1. one tag block;
2. one or more `Doc:` paths;
3. short `Review:` reasons;
4. explicitly human-authored `Memo:` text.

Tags are lower-case, de-duplicated, sorted, and may contain letters, digits,
hyphens, underscores, and namespace colons. Automated ingestion does not write
`Memo:`. `#browser-import`, `#primary`, `#evidence`, and `#statement` are
technical implementation details and are forbidden.
Derived `#cashback-*` bucket tags and routine original-currency facts are also
forbidden. Cashback state belongs to the companion/configuration and FX facts
remain in source manifests and evidence metadata, not the display note.

Only paths below `Finance Evidence/` may be written as `Doc:` values. The
catalogue remains authoritative for hashes, message identities, document
types, and transaction relationships.

## Enforcement

`finance_tracker.actual_notes` is the canonical formatter, parser, legacy
normalizer, and cleanup-plan builder. The Python serializer and evidence linker
use it directly. `integrations/actual/note-contract.mjs` independently validates
every envelope at the Node bridge before Actual's dry run, so alternate callers
cannot bypass the contract.

Native Actual rules cannot atomically de-duplicate and re-order several tags in
notes. The compiler therefore deploys lossless payee/category actions to Actual
and explicitly defers `add_tag`/`add_tags` actions to the deterministic worker.
Those worker actions still follow the configured pre/default/post rule stages;
they are not copied into executable code.

Legacy cleanup is exact-state guarded: each change pins imported ID, account,
date, amount, and the complete previous note. A drifted row fails instead of
being overwritten. Cleanup runs only at the final migration gate after the
classification, tagging, schedule, budget, and evidence work is complete.

## Full-corpus dry run

`scripts/plan-actual-corpus-migration.py` regenerates every source manifest
without modifying its signed amounts, produces a corpus-wide topic/note
exception report, and optionally compares the proposed state with a read-only
Actual snapshot. The Actual plan is always marked `DRY_RUN_ONLY`, is keyed by
unique `imported_id`, and carries a SHA-256 guard over the complete current
account/date/amount/category/note state. Current `Memo:`, `Review:`, and `Doc:`
content is retained. The corpus report must show zero amount mutations and zero
note-contract violations. A manual category is reported as a conflict and
preserved.

Ordinary positive merchant credits default to refunds. An issuer reward needs
explicit reward evidence. In particular, a positive Amazon merchant row on the
Emirates Islamic statement is a refund; an actual EI cashback award is an
Amazon credit with `cash_equivalent=false`, not cash deposited to the card.
The words `Amazon credit` alone do not establish a reward.

Example read-only invocation:

```powershell
python scripts/plan-actual-corpus-migration.py `
  runtime/full-restage/final/manifests `
  runtime/full-restage/note-v2/manifests `
  runtime/audit/actual-corpus-semantics-audit-2026-08-19.json `
  --snapshot runtime/audit/live-full-chat-audit-snapshot.json `
  --plan runtime/plans/actual-corpus-migration-dry-run-2026-08-19.json `
  --migration-audit runtime/audit/actual-corpus-migration-audit-2026-08-19.json
```

This command has no apply mode and never opens an Actual connection.

Run the contract and full ingestion tests before any deployment:

```powershell
python -m unittest tests.test_actual_notes -v
python -m unittest tests.test_corpus_migration -v
python -m unittest discover -s tests -v
Push-Location integrations/actual
npm test
npm run integration
Pop-Location
```
