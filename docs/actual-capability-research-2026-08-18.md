# Actual Budget capability research and build recommendations

Date: 2026-08-18  
Tested version: Actual client and server 26.8.1  
Status: research and recommendations only; no production mutation was made during this audit

## Executive decision

Actual should remain the authoritative finance ledger, budgeting engine, reconciliation surface, rules engine, schedules surface, and primary reporting application.

The repository should provide only the functions Actual cannot perform safely or portably:

1. acquire and parse bank, card, browser, email, and document sources;
2. normalize raw merchant descriptions before import while preserving `imported_payee`;
3. run AI only for unresolved derived fields and recommendations;
4. enforce evidence, deduplication, reconciliation, and import gates;
5. calculate live cashback routing in the separate companion;
6. compile versioned configuration into Actual-native rules, tags, reports, filters, and dashboards.

Actual 26.8.1 is substantially more capable than the current project documentation assumed. In particular:

- custom reports can filter on `Notes -> has all tags` and `Notes -> has any tag`;
- dashboards support multiple pages and native JSON import/export;
- Formula cards can calculate sums, counts, comparisons, budget dimensions, and conditional colors from named filtered queries;
- the rule editor exposes category-group conditions, splits, formula actions, append/prepend notes, and deletion;
- saved transaction filters can serve as review queues;
- category learning and payee-renaming learning create ordinary editable Actual rules;
- several useful operations are contextual rather than permanently visible, including **Link schedule**.

No upstream change is required merely to filter reports by tags. Upstream work may still be worthwhile for grouping a report by tag and for the known split/table-view tag-reporting edge cases.

## Research method

The audit used three evidence levels:

1. the authenticated production UI at `https://actual.vxsan.com`, without saving experimental test objects;
2. Actual's official documentation and release notes;
3. the installed, version-pinned 26.8.1 source under `integrations/actual/node_modules/@actual-app/core`.

Capabilities are classified as:

- **Stable/public**: documented UI or public `@actual-app/api` method.
- **Contextual UI**: supported in the UI but shown only after a selection or inside a menu.
- **Experimental**: user-facing but explicitly subject to change.
- **Internal/pinned**: implemented by Actual's synced engine but not part of the public API. Use only behind a version guard, backup, dry-run, and UI verification.
- **Avoid**: direct database writes or behavior whose semantics cannot be preserved safely.

## Immediate findings

### Credit-card schedules should be statement-derived and one-time

A standing fixed-amount credit-card schedule is the wrong model for a variable statement balance. Actual's approximate amount matcher permits only a plus/minus 7.5% range, which is not suitable for normal month-to-month card variation.

Actual's own credit-card guidance says that, when a schedule is used for auto-pay, the next schedule amount must be edited to the statement's New Balance. The current production schedule is already closer to the correct model:

- name: `Emirates Islamic Amazon Credit Card · 0082 · statement payment · 2026-08-25`;
- amount: AED 285.70;
- next date: 2026-08-25;
- recurrence: none;
- it is not configured to auto-post.

Recommended behavior:

1. Do not create recurring fixed-amount card-payment schedules.
2. When a statement is reconciled, create or update one one-time due item with the evidenced due date and exact New Balance.
3. Never auto-post the payment transaction.
4. Link the real transfer when it arrives, or let Actual match it within the schedule window.
5. Close or replace the one-time item after the payment is linked.
6. If a statement has no due date or the balance is zero/credit, create no schedule.

This keeps the schedule useful as a due-date reminder without allowing it to invent a liability amount.

Official references:

- [Schedules](https://actualbudget.org/docs/schedules/)
- [Paying a credit card in full](https://actualbudget.org/docs/budgeting/credit-cards/paying-in-full/)

### Link schedule is contextual, not a normal rule action

The documented path is:

1. open an account register;
2. select a transaction using its left-hand selection control;
3. open the selected-transactions menu in the upper right;
4. choose `Link schedule`;
5. select an existing schedule or create a new one.

The keyboard shortcut is `S` after selecting the transaction.

Documentation: [Creating schedules from transactions](https://actualbudget.org/docs/schedules/#creating-schedules-from-transactions)

Why it is easy to miss:

- it is absent until at least one transaction is selected;
- it is not an action offered when creating an ordinary user rule;
- a schedule owns an underlying rule whose internal action is `link-schedule`;
- the Rules list displays that generated rule, but users normally create/edit the relationship from the schedule or selected transaction.

The installed source confirms that `link-schedule` writes the schedule ID onto the matching transaction and that every schedule is backed by such a rule. We should not author this internal action directly. Create/update schedules through the public Schedule API and let Actual own the generated rule.

### Tag-based custom reporting already works

In a custom report, the funnel icon opens transaction filters. The live 26.8.1 path is:

`Filter -> Notes -> has all tags` or `Filter -> Notes -> has any tag`.

This supports native filtered reports for:

- `#shared`;
- `#owner-*`;
- `#lt713` and `#indigo1414`;
- `#needs-review`, `#category-review`, `#tag-review`, and `#not-normalized`;
- `#receipt`, `#warranty`, and other evidence tags.

Limits:

- reports cannot split/group by tag;
- there is no native `does not have tag` selector;
- split-parent tag inheritance and table-mode report conditions have known edge cases documented in `docs/tag-reporting.md`.

Therefore:

- use native reports for tag-filtered totals and trends;
- use multiple filtered widgets or Formula-card named queries for comparisons between tags;
- retain the existing read-only `actualctl tag-report` fallback for tag grouping, negative tag filters, and correctness-sensitive split reporting.

## Automatic learning

### Category learning

The live UI confirms global Category Learning is enabled under:

`More -> Payees -> Category learning settings`.

It is not AI and does not interpret merchant semantics. It creates or updates an ordinary default-stage rule:

`payee is <payee> -> set category to <probable category>`.

Actual 26.8.1 source behavior is more specific than the documentation:

- it looks at the latest five non-parent transactions for the payee;
- a category must occur at least three times to win;
- it considers data in a 180-day look-back window from the changed transaction and up to 180 days in the future;
- closed accounts are excluded;
- split parents are excluded;
- payees with `learn_categories = false` are excluded;
- it updates only when one of the latest five transactions was part of the current categorized change;
- if a simple default `payee is X -> category Y` rule exists, Actual updates it; otherwise it creates one.

Importantly, the public API's transaction-add path defaults `learnCategories` to false. Statement imports will run existing rules, but importing already categorized rows does not automatically teach Category Learning unless the caller explicitly opts in or a later UI edit is submitted through a learning-enabled path.

Recommended policy:

- keep global learning enabled;
- enable it for stable single-purpose payees such as DEWA, Empower, a pharmacy, or a known grocery merchant;
- disable it per payee for polymorphic merchants and processors such as Amazon, Apple, payment gateways, marketplaces, and merchants whose category depends on the purchased item;
- treat learned rules as user-owned rules, but mark them in the exported rule inventory so the bootstrap does not create a competing duplicate;
- use post-stage policy rules for mandatory overrides because post rules run after learned default rules.

### Payee-renaming learning

Payee-renaming learning is a prompted cleanup flow, not an always-on classifier.

When a transaction's payee is changed and the old payee becomes unused, Actual can offer to apply the rename in future. If accepted, it creates or extends a pre-stage rule:

`imported payee one of [raw names] -> set payee to <canonical payee>`.

The rule is editable. Actual deliberately does not infer a broad `contains` or regex pattern; the user must widen the generated exact `one of` condition if appropriate.

Recommended policy:

- keep the repository Vendor Registry as the source of truth for normalization;
- run normalization before import and preserve the original text in `imported_payee`;
- generate any Actual pre-stage fallback rules from that same registry for manual/UI imports;
- do not maintain a separate handwritten list of normalization rules in Actual;
- use Actual's prompt as a discovery source for a new alias, then add the approved alias to the Vendor Registry.

Official reference: [Automatic rules](https://actualbudget.org/docs/budgeting/rules/#automatic-rules)

## Rules capability audit

### Stages and ordering

Actual has three stages:

1. `pre`: normalization and fields that later rules must see;
2. `default`: normal categorization and learned category rules;
3. `post`: mandatory overrides, review markers, and final tagging.

Within a stage, Actual ranks rules by specificity rather than exposing an arbitrary numeric priority. An exact `is` condition ranks above `contains`, and more specific rules run later so they win conflicts. The last action that sets a field wins.

Implication for this repository: canonical numeric priority is not fully portable to Actual. The compiler must reject or explicitly report any rule set whose result depends on insertion order between equally specific rules. Use stages, mutually exclusive conditions, and deliberate specificity instead of pretending that Actual preserves every DSL priority.

### Conditions visible in the live rule editor

- imported payee;
- account;
- category;
- category group;
- date;
- payee;
- notes;
- amount;
- amount inflow;
- amount outflow.

`category group` is supported by the installed UI even though it is omitted from the main Rules documentation field list. This is useful for post-processing broad classes of categories without enumerating every category.

Visible operators include exact/not exact, contains/not contains, regex matches, one-of/not-one-of, numeric comparisons, date comparisons, approximate dates/amounts, and tag-aware Notes conditions.

### Actions visible in the live rule editor

- set category;
- set payee;
- set payee by name;
- set notes;
- set cleared;
- set account;
- set date;
- set amount;
- prepend notes;
- append notes;
- split into multiple transactions;
- delete transaction.

`delete transaction` is supported but should not be generated by our bootstrap. Deduplication and unsupported-row exclusion belong before import; a rule that tombstones imported financial data is too destructive and may interact badly with reimport behavior.

### Supported in the engine but not offered as ordinary rule conditions/actions

Pinned 26.8.1 source also recognizes fields/actions including:

- `payee_name` as a condition field;
- `reconciled`;
- `saved` filter references;
- `transfer`;
- `parent`;
- `link-schedule`;
- fixed, percentage, formula, and remainder split amounts.

Some of these appear in transaction/report filters but not the ordinary rule-condition menu. They are internal/pinned, not a stable extension point. We should use them only if a public UI/API route exists for the desired outcome, or after an isolated compatibility test with a strict 26.8.1 version guard.

### The rule editor is also a batch editor

The editor previews all matching transactions and permits applying actions to selected matches without saving the rule. This is useful for controlled historical cleanup:

- normalize a historical alias;
- apply a new property tag;
- add a review marker;
- recategorize a verified payee cohort.

Because the preview is native and shows the affected transaction set, it is safer than a broad one-off script for many correction jobs. Split transactions remain a special case and should be validated separately.

## Rule formulas

Formula mode is enabled in production and appears as an `ƒ` button on eligible `set` actions. It is experimental and uses HyperFormula. Formulas must start with `=`.

Documentation: [Excel Formula Mode](https://actualbudget.org/docs/experimental/formulas/)

### Eligible and ineligible actions

Formula mode can be used on scalar fields such as notes, payee name, cleared, date, and amount. It is not available for ID-valued category, payee, or account actions. Formula mode and the older action-template mode are mutually exclusive.

Rule action templating is disabled in production and deprecated in Actual 26.6+. Do not add new dependencies on it.

### Transaction variables

Useful rule variables include:

- `today`, `date`;
- `amount`, `balance`;
- `notes`, `imported_payee`, `payee_name`, `account_name`, `category_name`;
- `cleared`, `reconciled`;
- `BALANCE_OF("account id or exact name")`.

Critical unit rule: `amount`, `balance`, and rule-mode `BALANCE_OF` enter the formula as integer minor units, but numeric formula results are interpreted as major currency units and converted back to minor units. Any calculation based on amount/balance must divide the input by 100 before returning a currency value.

### Recommended uses

Good rule-formula uses:

- deterministic note enrichment from existing fields;
- extracting a stable suffix or reference from `imported_payee` into Notes;
- deriving a display note with `TEXT`, `FORMATCURRENCY`, or date functions;
- fixed/percentage/formula split amounts when the arithmetic is independently tested;
- loan principal/interest splits if those modules are later added.

Avoid:

- changing statement amounts merely to normalize them;
- date rewrites that diverge from statement posting dates;
- balance-dependent categorization whose result changes when older transactions are imported later;
- using formulas as a substitute for evidence-based foreign-currency extraction;
- duplicating rules that are clearer as normal conditions/actions.

### Formula cards are more valuable than mutating rule formulas

Formula cards can use named filtered queries and functions including:

- `QUERY("name")` for a transaction sum;
- `QUERY_COUNT("name")` for a matching-row count;
- `BUDGET_QUERY` for budgeted, spent, start balance, end balance, and goals;
- `BALANCE_OF` for current account balances;
- conditional color formulas using `RESULT`.

This is the best native tool for compact dashboard KPIs such as:

- unresolved transaction count;
- shared spend this month;
- property net cash flow;
- budget used percentage;
- month-over-month deviation;
- subscriptions due/paid totals;
- reconciled versus unreconciled count;
- current cash and current card liabilities.

## Reports and dashboard audit

### Live production capabilities

Actual 26.8.1 supports:

- unlimited dashboard pages;
- drag/resizable 12-column widget layouts;
- dashboard rename/create/delete;
- dashboard JSON import and export;
- cash flow, net worth, crossover, Age of Money, spending analysis, text, summary, calendar, Formula, and custom-report widgets;
- experimental budget analysis, balance forecast, and Sankey widgets;
- custom report table, bar, line, area, and donut views;
- total/time modes and live/static date ranges;
- split by category, category group, payee, account, or month;
- report filters for account, amount, category, cleared, date, notes/tags, payee, reconciled, transfer, and saved filter;
- native report options for hidden categories, empty rows, off-budget accounts, uncategorized transactions, interval trimming, and trend lines.

The current Main dashboard already contains YTD summary cards, averages, net worth, cash flow, monthly comparisons, a transaction calendar, and recent net-worth change. Two saved reports are present:

- `Monthly Spending Trend · Last 12 Months`;
- `Spending by Category · Last 12 Months`.

The primary gap is not a lack of dashboard capability; it is dashboard information architecture and missing filtered modules.

### Dashboard configuration as code

The UI's dashboard menu exposes `Import` and `Export`. The installed engine has a `dashboard-import` handler that validates a versioned JSON object, replaces the target dashboard's widgets, and creates/updates custom reports embedded in custom-report widgets.

The public `@actual-app/api` does not document dashboard/report CRUD. The package does expose low-level `send`, and the pinned engine provides handlers for:

- dashboard create/delete/rename/update;
- add/update/remove/copy widget;
- dashboard import;
- custom report get/create/update/delete;
- saved filter create/update/delete.

Recommended approach:

1. Store exported dashboard JSON under `config/actual-dashboards/`.
2. Store any separately managed report and saved-filter definitions under versioned configuration.
3. Prefer the native UI Import action for the initial supported workflow.
4. If scripted bootstrap is added, call the synced internal handler through Actual's own client rather than writing SQLite directly.
5. Require exact client/server/API version 26.8.1, an Actual budget export backup, a dry-run structural diff, and a clean second plan.
6. Re-export from the UI and compare normalized JSON after apply.
7. Treat any version change as a compatibility test, not an automatic upgrade.

This supersedes the older assumption in `docs/actual-production.md` that layouts can only be UI-authored. There is still no supported public dashboard API, but there is a supported user-facing JSON import/export path.

### Recommended dashboard suite

#### 1. Finance Overview

Keep this as the default landing dashboard and make it concise:

- current cash and current card liabilities;
- current-month income, expense, and net cash flow;
- budget used percentage and overspent category count;
- unreconciled/uncategorized/review count;
- upcoming statement payments and bills;
- compact net-worth and monthly-spend trend;
- highest material spending deviations.

Do not show the entire transaction ledger here.

#### 2. Spending and Trends

- monthly category trend for 12/24 months;
- month-over-month and year-over-year Formula cards;
- current month versus 3/6/12-month averages;
- top payees and categories;
- discretionary versus fixed/recurring spend;
- refunds shown separately from gross purchases where useful.

#### 3. Shared and People

- report filtered by `#shared`;
- Formula cards for each `#owner-*` tag or owner-account cohort;
- shared spend by category and month;
- reimbursements and unresolved ownership;
- separate saved filters for each person's transactions.

Actual cannot group by tag natively, so side-by-side filtered widgets or Formula named queries should be used for person comparisons.

#### 4. Properties

Create managed tags:

- `#lt713`;
- `#indigo1414`.

For each property, show filtered income, utilities, maintenance, service charges, management cost, and net cash flow. Use one custom report per property plus Formula cards for net and major ratios. Do not create duplicate property-specific category trees.

#### 5. Review and Data Quality

Formula cards and saved filters for:

- uncategorized;
- `#needs-review`;
- `#category-review`;
- `#tag-review`;
- `#low-confidence`;
- `#not-normalized`;
- unreconciled;
- evidence requested but missing;
- suspected duplicates/splits requiring review.

The card should open the relevant saved transaction filter, not only show a count.

#### 6. Bills, Subscriptions, and Goals

- schedule calendar/list for true recurring bills;
- one-time statement-derived card due items;
- subscription spend filtered by `#subscription` and `#recurring`;
- annual and irregular costs backed by Save-by-Date or Cover-Schedule budget automations;
- savings goals and progress using Budget Analysis and Formula cards.

## Budget automation findings

Goal templates and the Budget Automations UI are enabled in production. The experimental UI can support:

- fixed amount at day/week/month/year cadence;
- save by date and repeating goals;
- refill/balance caps;
- cover a schedule;
- history-based averages;
- income percentages;
- long-term goals;
- weighted end-of-month cleanup pools.

This is a better home for monthly budgets, annual budgets, sinking funds, irregular bills, and savings goals than custom Python calculations.

Recommended use:

- use fixed/refill/history automations for ordinary monthly categories;
- use Save by Date for annual fees, insurance, holidays, and planned purchases;
- use Cover Schedule for fixed or narrowly variable scheduled expenses;
- do not use Cover Schedule as the source of a variable card statement balance;
- use named cleanup pools for variable utilities and property expense pools only after tests on a copy of the budget;
- do not automate end-of-month cleanup from the statement ingestion job until the budget logic has its own reviewed close workflow.

Official reference: [Budget Automation](https://actualbudget.org/docs/experimental/budget-automation/)

## Recommended rule architecture

### Pre-import normalization outside Actual

The deterministic n8n stages should:

- parse and preserve the exact imported description;
- normalize the vendor using the versioned Vendor Registry;
- attach source/evidence references;
- split a source transaction only when evidence proves the child amounts;
- leave unresolved category/tag fields for later stages.

It should not own ordinary static categorization logic.

### Actual pre stage

Generated fallback normalization rules for transactions manually entered/imported through the UI:

- imported-payee aliases/regex -> canonical payee;
- no categories or tags unless a true normalization depends on them.

The current production rules contain overlapping normalization rules and several pre-stage rules that also categorize/tag. Examples include multiple Amazon, Empower, DEWA, Apple, and Virgin Mobile patterns. These should be consolidated from the Vendor Registry and separated by stage.

### Actual default stage

- deterministic payee/category rules using `one of` wherever practical;
- conditions include `category is nothing` to preserve manual choices;
- Category Learning for stable payees;
- vendor-specific exceptions with more specific conditions;
- no AI and no evidence side effects.

### Actual post stage

- mandatory category overrides that must beat learning;
- append tags without replacing existing notes;
- positive transaction in an expense category group -> append `#refund`;
- known card reward/cashback inflows -> category `Cashback & Rewards` and append `#cashback`/`#reward`;
- shared, owner, property, subscription, utility, and review markers;
- never overwrite a manual locked field.

For ordinary refunds, retaining the original expense category is often preferable because it nets against the original category. The `#refund` tag supports separate gross/refund reporting without falsely treating the refund as income. Issuer cashback is different and belongs in `Cashback & Rewards`.

### AI stage

AI runs outside Actual only after deterministic rules cannot resolve a requested field.

It may propose:

- an existing category;
- tags;
- vendor aliases for human approval;
- property/owner association;
- evidence-search scope;
- a recommendation to create a new category or rule.

It must not create a category automatically. A proposed new category should yield structured metadata plus `#category-review`. The review flow either maps it to an existing category or explicitly approves a configuration change.

Because Actual executes native rules during import, the cleanest AI flow is a preview/fixed-point loop: normalize -> preview native results -> ask AI only for unresolved fields -> validate proposals -> final idempotent import. If previewing native rules is not possible through the public import method without side effects, the repository compiler must mirror only the generated subset and verify the mirror against Actual using fixtures.

## Evidence, receipts, and Amazon splits

Actual Notes support Markdown and links. Continue storing files in OneDrive and append portable evidence references to the transaction note, with tags such as `#receipt` and `#warranty`.

Recommended note content is compact and machine-readable:

`evidence:Finance Evidence/2026/08/vendor/file.pdf`

Do not paste large AI explanations into transaction notes.

For Amazon or another merchant where one card transaction represents several known orders:

1. match order evidence by merchant, time window, currency, amount, order ID, and source message;
2. create an Actual split parent only when child amounts sum exactly to the statement transaction;
3. keep the source imported ID on the parent;
4. place order IDs, categories, and evidence references on children;
5. leave ambiguous totals unsplit and tag `#split-review`;
6. validate with Actual's split repair checks and a post-import balance assertion.

The public API supports split imports using a parent with `is_parent` and `subtransactions`. Updating splits later requires supplying the complete child list, so evidence matching should finish before the first authoritative commit whenever possible.

## Other useful contextual or under-documented features

| Capability | Visibility | Recommended use |
|---|---|---|
| Link schedule / shortcut `S` | Contextual transaction selection | Link a real one-time payment to a statement-derived due item |
| Category-group rule condition | Visible in editor, omitted from main field docs | Broad post rules such as refund tagging |
| Dashboard Import/Export | Dashboard `Menu` | Version dashboard layouts in GitHub |
| Multiple dashboards | Dashboard name menu | Separate Overview, Trends, Shared, Properties, Review, Bills |
| Tag report filters | Unlabeled funnel -> Notes | Native filtered dashboards without a PR |
| Saved transaction filters | Account Filter -> save | Review queues and drill-down links |
| Rule preview and Apply actions | Rule editor | Controlled historical batch cleanup |
| Split-producing rules | Rule editor | Repeated deterministic splits with strong arithmetic tests |
| Delete-transaction rule action | Rule editor | Avoid in generated production rules |
| Dashboard Formula card | Add widget | Compact KPIs, counts, ratios, conditional warnings |
| End-of-month cleanup pools | Budget header/automation UI | Later, after budget tests; separate from statement close |
| Find schedules | Schedules page | Discover genuine recurring bills, not card balances |
| Change upcoming length | Schedules page | Tune reminder horizon |
| Merge shortcut `G` | Exactly two equal transactions selected | Controlled duplicate merging |
| Payee locations | Mobile transaction entry only | Optional mobile manual-entry convenience |
| Repair transactions | Advanced Settings | Diagnose/fix split and transfer integrity on a backup |
| Dashboard/report internal handlers | Low-level synced engine | Version-pinned bootstrap only, never raw SQLite |

## Current-production observations

- Client/server version: 26.8.1.
- Category Learning: enabled globally.
- Formula mode: enabled.
- Goal templates and Budget Automations UI: enabled.
- Currency support and mobile calculator: enabled.
- Sankey, Balance Forecast, and Budget Analysis: enabled.
- Deprecated rule-action templating: disabled.
- One-time Emirates Islamic statement-payment schedule: present.
- Dashboard import/export and multiple dashboard pages: available.
- Main dashboard: populated, but not yet organized into the recommended module suite.
- Custom report tag filtering: available.
- Rule inventory: contains overlapping normalization rules and mixed-stage responsibilities.
- Uncategorized indicator: 2,186 transactions at audit time; this must be separated into genuinely uncategorized rows versus split parents/off-budget/system rows before treating it as a cleanup count.

## Prioritized build backlog

### P0: protect and measure

1. Export an Actual backup.
2. Export the current dashboard JSON and rule inventory.
3. Build a read-only audit of rules, duplicates, learned rules, categories, payees, tags, saved filters, reports, and schedules.
4. Define fixtures that prove rule, split, refund, and tag-report behavior on 26.8.1.

### P1: rule and normalization cleanup

1. Consolidate the Vendor Registry.
2. Generate pre normalization fallbacks from it.
3. Move categorization to default and tagging/overrides to post.
4. Replace repeated conditions with native `one of` where semantics are exact.
5. Disable Category Learning for polymorphic payees.
6. Add explicit property, owner, refund, cashback, evidence, and review tags.
7. Require a clean second bootstrap plan.

### P2: native review flows

1. Create saved filters for every review marker.
2. Add Formula-card counts and conditional colors.
3. Link dashboard cards to review filters where the UI permits.
4. Add category/rule recommendation artifacts to the AI handoff.

### P3: dashboard suite as code

1. Create the six recommended dashboard pages.
2. Export each dashboard JSON to `config/actual-dashboards/`.
3. Add schema/version validation and normalized diff tooling.
4. Add guarded scripted import only after manual JSON import is verified.

### P4: budgets, bills, and schedules

1. Configure real monthly budget automations and annual sinking funds.
2. Discover/approve genuine recurring schedules.
3. Keep card payments statement-derived and one-time.
4. Test utility/property cleanup pools in a duplicate budget before production.

### P5: AI, evidence, and split completion

1. Implement the native-rule preview/fixed-point AI handoff.
2. Add category-creation recommendations without auto-creation.
3. Complete evidence links before authoritative commit.
4. Add exact-evidence Amazon splitting and split-repair verification.

## Recommendation on upstream work

Do not start an upstream PR for tag filtering; it already exists in 26.8.1.

Potential upstream contributions, after the local setup is complete:

1. group/split custom reports by tag with explicit multi-tag double-count semantics;
2. fix the documented table-view condition and split-parent tag edge cases if still reproducible on current `master`;
3. add a public dashboard/report/filter API or formally document dashboard JSON schema/import;
4. add documentation for category-group rule conditions and other currently visible-but-undocumented fields;
5. improve accessibility labels for the custom-report funnel button and other icon-only controls.

## Final recommendation

Build the next iteration inside Actual rather than beside it:

- deterministic normalization and AI remain external;
- deterministic category/tag/refund behavior becomes Actual-native;
- reports, review queues, budgets, schedules, and dashboards become Actual-native;
- dashboard JSON, generated rules, tag definitions, saved filters, and compatibility metadata are versioned in GitHub;
- the cashback companion remains separate because it has a provisional, live operational data model that Actual's statement-led ledger intentionally does not have.

This produces one authoritative financial system without forcing Actual to perform document parsing, AI inference, or live cashback routing.
