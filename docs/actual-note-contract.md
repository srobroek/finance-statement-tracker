# Actual transaction note contract

Actual notes are a compact human-facing annotation layer. Import provenance,
Outlook IDs, attachment IDs, source URLs, and deduplication identities belong
in the ingestion manifest, evidence catalogue, or `imported_id`; they must not
be copied into notes.

## Canonical grammar

```text
#semantic-tag #namespace:value | Doc: Finance Evidence/YYYY/MM/vendor/file.ext | FX: CUR amount | Review: controlled-reason | Memo: human text
```

The sections are optional but, when present, always appear in this order:

1. one tag block;
2. one or more `Doc:` paths;
3. original-currency `FX:` facts;
4. short `Review:` reasons;
5. explicitly human-authored `Memo:` text.

Tags are lower-case, de-duplicated, sorted, and may contain letters, digits,
hyphens, underscores, and namespace colons. Automated ingestion does not write
`Memo:`. `#browser-import`, `#primary`, `#evidence`, and `#statement` are
technical implementation details and are forbidden.

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

Run the contract and full ingestion tests before any deployment:

```powershell
python -m unittest tests.test_actual_notes -v
python -m unittest discover -s tests -v
Push-Location integrations/actual
npm test
npm run integration
Pop-Location
```
