# Actual budget automation and month-end cleanup

`config/actual-budget-automation.json` is the portable source for Actual's native
UI-backed budget automations and month-end cleanup roles. The bridge refuses to
apply it unless the live Actual server exactly matches the pinned version.

The initial policy uses recent-history averages for utilities and groceries.
Electricity, district cooling, and mobile/internet participate in a local
`Utilities` pool: unused funds first cover another utility's overspend, then
move to `Utilities Buffer`. `Monthly Buffer` is a named source for configured
overspend categories. After local pools finish, eligible positive leftovers go
to the global `Savings Goals` sink. This retains envelope history while making
unspent cash available for saving or investing.

The configuration writes the UI automation objects directly through Actual's
own version-pinned handlers. It does not maintain duplicate directives in
category notes. Applying the automation definitions does not execute a monthly
budget or cleanup. Month execution remains a separate, auditable action after
the clean ledger and available cash are verified.

```powershell
node integrations/actual/actualctl.mjs budget-automation `
  --config config/actual-budget-automation.json
```

Add `--apply` only behind `ALLOW_ACTUAL_WRITES=true` after bootstrap has created
all referenced categories.

