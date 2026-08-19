# Tag reporting

Actual 26.8.1 can filter custom reports with tag-aware conditions stored in the report. The current native boundary is narrower than the initial assumption:

- `hasTags` implements all selected tags.
- `hasAnyTag` implements any selected tag.
- custom reports cannot split or group the output by transaction tag.
- there is no first-class `does not have tag` condition.
- top-level tags on split parents have known edge cases, and the custom-report table view has a reported condition-handling bug.

The current custom-report documentation lists category, group, payee, account, and month as split dimensions, but not tags. Actual issue [#3130](https://github.com/actualbudget/actual/issues/3130) requested tag grouping and is closed without linked development. Two still-open correctness reports are directly relevant: [#5640](https://github.com/actualbudget/actual/issues/5640) documents top-level split tags being omitted from report totals, and [#7644](https://github.com/actualbudget/actual/issues/7644) documents report conditions being ignored in table view and `hasTags` being discarded there. A new upstream patch is not prepared from this project because it would have to resolve both renderer-level condition handling and product semantics for multi-tag grouping, not merely add a selector. One transaction may intentionally appear in more than one tag group, so grouped totals can exceed the unique transaction total.

## Read-only fallback

The direct Actual integration provides a tested reporting path without creating another ledger:

```powershell
$env:ACTUAL_SERVER_URL = 'http://127.0.0.1:15006'
$env:ACTUAL_SYNC_ID = '<sync-id>'
$env:ACTUAL_PASSWORD = '<runtime-secret>'

node .\integrations\actual\actualctl.mjs tag-report `
  --start 2026-08-01 `
  --end 2026-08-31 `
  --all-tags shared,rental `
  --without-tags business `
  --group-by category `
  --result .\runtime\reports\shared-rental.json
```

Options:

- `--any-tags`: match at least one tag.
- `--all-tags`: require every tag.
- `--without-tags`: reject any matching tag.
- `--group-by`: `category`, `payee`, `account`, or `tag`.

The command downloads and syncs the Actual budget through the official read API, calculates the report in memory, and writes only the requested result file. It does not persist transactions. Split parents are not double-counted; their tags are inherited by child rows for filtering. Tag-grouped output exposes `duplicated_spend_minor` so multi-tag duplication is explicit rather than hidden.
