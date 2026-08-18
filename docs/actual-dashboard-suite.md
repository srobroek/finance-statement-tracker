# Actual dashboard suite

The production dashboard layout is versioned under
`config/actual-dashboards/`. `suite.json` maps stable dashboard names to Actual's
native dashboard export format. The bridge resolves portable account, category,
payee, group, and tag references by name before using Actual's own dashboard
import handler.

The managed pages are:

- Main: compact landing page with the highest-value financial signals;
- Spending & Trends: monthly movement, category mix, Sankey, and calendar;
- Shared: reports filtered by `#shared`;
- Properties: rental income and costs filtered by `#rental`;
- Review: uncategorised, category-recommendation, and review queues;
- Bills & Subscriptions: due-date calendar, utilities, and subscriptions;
- Retirement: net worth and savings/investment contribution trends.

The suite deliberately contains data, charts, and actionable queues rather than
instructions or documentation cards. Custom report identifiers are stable, so
re-import updates the report rather than multiplying copies. Retired dashboard
names are explicit; the bridge never deletes an unmanaged dashboard implicitly.

```powershell
node integrations/actual/actualctl.mjs dashboard-apply `
  --config config/actual-dashboards/suite.json
```

Add `--apply` only behind `ALLOW_ACTUAL_WRITES=true`. Export and audit commands
remain read-only:

```powershell
node integrations/actual/actualctl.mjs dashboard-audit
node integrations/actual/actualctl.mjs dashboard-export --name Main
```

