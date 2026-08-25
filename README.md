# Finance Statement Tracker

This project contains the deterministic finance contracts and n8n workflows for
an Actual-first personal finance tracker. n8n owns scheduling and orchestration;
Actual owns the ledger, the cashback app owns live routing state, and OneDrive
owns evidence.

The deterministic parser and rule engine use a small pinned Python dependency set. The continuously running cashback companion uses SQLite for durable operational state and `pywebpush` for iOS Declarative Web Push delivery.

The target is **Actual Budget as the primary ledger**, with a small continuous companion application for cashback control and OneDrive for evidence. See `docs/platform-evaluation.md`, `docs/actual-production.md`, `docs/cashback-companion-decision.md`, and `config/project-acceptance.json`.

## Included

- AutoCat-style ordered static rules with OR groups of AND conditions.
- Multiple actions per rule and stage-aware execution.
- A versioned, JSON-serialisable internal rule representation.
- Separate AI-policy contract; AI is never used for arithmetic or static matching.
- Cashback tier, cap, pace, over/under, and payment-routing calculations.
- Deterministic email/document matching and portable OneDrive evidence paths.
- Month-close Markdown with a static Mermaid category chart.
- A bank-adapter API that normalizes different statement layouts behind one contract.
- A CLI that processes JSON transactions and rules locally.
- Account, owner, category, subcategory, vendor, and tag attribution for Actual.
- Savings reservations and safe-to-spend calculations without splitting a bank account.
- Recurring-subscription detection that excludes variable utilities.
- Reusable reports by account, owner, category, vendor, tag, and transaction type.
- Unit tests for rules, refunds, tier uplift, routing, savings, subscriptions, and reports.
- A platform adapter boundary and an Actual Budget import serializer.
- An idempotent Actual account/category/tag/payee/rule bootstrap.
- Two-phase statement-to-Actual ingestion with a durable run manifest and mandatory preflight.
- Read-only Actual snapshots that drive cashback pace and routing without a duplicate ledger.
- A compact live cashback companion in `apps/cashback-control`, recalculated immediately after each accepted event.
- A tabbed mobile-first cashback interface verified at the 428 x 926 iPhone 13 Pro Max viewport: Routing and its decision tree fill one screen, while Cards and History have dedicated screens without horizontal overflow.
- Installable iOS PWA support with declarative bucket-full, final-week target, and routing-change notifications; delivery state is deduplicated per device in the companion database.
- Independent minute-level dashboard recalculation and stale-ingestion push warnings, even when no new transaction arrives.
- Conservative Outlook notification adapters that update live buckets only when card, amount, currency, merchant, and a usable timestamp are evidenced.
- Recipe-driven browser acquisition and deterministic official-export parsers migrated from the previous source app for ADCB, Emirates Islamic, FAB, Wio, generic CSV, and Sarwa capture.
- A public, profile-driven cashback engine with external cards, currencies, tiers, compound requirements, buckets, caps, alert thresholds, weekly pace policies, and decision-tree routing; four unrelated fictional profiles are boot-tested in CI.

Rules use the versioned AutoCat-style JSON contract in `config/static-rule-schema-v1.json`. The worker validates and evaluates it deterministically, while compatible rules are compiled into Actual. `rule_sets` provide searchable scopes such as `LIVE_CASHBACK` without duplicating rules.

## Bank adapter API

`finance_tracker.statements.BankStatementAdapter` is the extension boundary for banks. An adapter only detects and parses its own statement layout; it must emit `NormalizedStatement` and `NormalizedStatementTransaction`. Reconciliation, rules, cashback calculations, and the n8n Actual node consume only those normalized objects.

The POC includes `emirates_islamic_v1`, `adcb_v1`, and `wio_credit_v1`. New banks register one adapter with `StatementAdapterRegistry`; downstream code does not change. RAKBANK and Standard Chartered remain explicitly non-importing placeholders until real fixtures pass parser and arithmetic tests. Statement passwords are supplied through runtime secrets or an approved credential store. They must never be committed to Git, emitted to logs, or copied into decision traces.

`finance_tracker.ingestion.stage_statement` converts the canonical statement into a reviewable staging batch using versioned account/card configuration. A statement can be `balance_tied` while `ledger_reconciled` remains false; only the later matching workflow may change the latter.

## setup and operations

| Surface | Procedure owner |
|---|---|
| Workflow boundary | [`integrations/n8n/README.md`](integrations/n8n/README.md) |
| Credential setup | [`docs/n8n-credential-setup-checklist.md`](docs/n8n-credential-setup-checklist.md) |
| OneDrive setup | [`integrations/n8n/setup-workflows/README.md`](integrations/n8n/setup-workflows/README.md) |
| Ledger operations | [`docs/actual-production.md`](docs/actual-production.md) |
| Backup and restore | [`docs/backup-and-restore.md`](docs/backup-and-restore.md) |
| Browser boundary | [`docs/browser-ingestion.md`](docs/browser-ingestion.md) |

### procedure scope

- ProDex subscription adapter: workflow boundary.
- ProDex and Microsoft: credential setup.
- OneDrive root: OneDrive setup.
- Microsoft OAuth proof: OneDrive setup.
- Ledger and Cashback: ledger operations.
- Rollback: backup and restore.
- Browser acquisition: separate boundary.

### ownership

| Owner | State |
|---|---|
| `Actual` ledger | Ledger and budgets |
| n8n | Acquisition and orchestration |
| Cashback Control | Live routing and cashback state |
| OneDrive | Evidence and catalog |

The ledger owns:

- posted transactions
- accounts
- budgets
- schedules
- reconciliations
- reports

n8n owns:

- acquisition
- orchestration
- receipts
- cursor state

Cashback Control owns notification routing and cashback period state. OneDrive owns evidence originals and the evidence index.

The browser never writes directly to the ledger or Cashback Control. n8n validates each delta. n8n sends each validated delta through its fenced writer.

### data migration

The legacy input contract declares 15 `SPEC_ONLY` tables in [`integrations/n8n/data-tables.json`](integrations/n8n/data-tables.json).

The migration target contains four tables in [`integrations/n8n/data-table-migration-matrix.json`](integrations/n8n/data-table-migration-matrix.json):

- `finance_ingestion_state`
- `finance_documents`
- `finance_actual_batches`
- `finance_ai_reviews`

[`tests/test_data_table_migration_matrix.py`](tests/test_data_table_migration_matrix.py) proves the migration dispositions. It also proves the target set and bootstrap exclusion. A separate retained-runtime receipt proves the four target tables; this test does not prove live tables.

### runtime acceptance boundary

Checked-in n8n exports remain inactive and `SPEC_ONLY`.

A retained n8n readback proves these fields:

- 19 inactive workflows
- six credentials
- one project
- four ordered Data Tables
- HTTP 200 health
- zero restarts
- no duplicate retained names

The readback does not prove provider authentication, Actual/Cashback semantic
readback, Cloudflare routes, or rollback.

These results remain pending until a current redacted receipt records each result:

- ProDex device login
- `auth.json` persistence
- Microsoft refresh
- Microsoft restart
- Ledger/Cashback readback
- Cloudflare routes
- Production identities
- Rollback

- Acceptance statuses: [`config/project-acceptance.json`](config/project-acceptance.json)

### platform-owned procedures

The finance checkout does not own the n8n platform scripts. Use pinned commit [`a3fa5487b250dc46c14ee460a4dc2d34a22c3867`](https://github.com/srobroek/n8n/tree/a3fa5487b250dc46c14ee460a4dc2d34a22c3867).

- [`backup.sh`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/scripts/backup.sh)
- [`doctor.sh`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/scripts/doctor.sh)
- [`restore-disposable.sh`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/scripts/restore-disposable.sh)
- [`recover-retained-n8n-key.sh`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/scripts/recover-retained-n8n-key.sh)
- [`cloudflare-publication.md`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/docs/cloudflare-publication.md)
- [`verify-cloudflare-routes.sh`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/scripts/verify-cloudflare-routes.sh)

## Recreate locally

```powershell
git clone <private-repository-url>
cd Finance-Statement-Tracker
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m unittest discover -s tests -v
```

PDF extraction is an explicit n8n sub-workflow. Install the `statements`
optional dependency only for local parser development; production extraction
uses reviewed fixed-purpose n8n nodes.

## Run

```powershell
python -m unittest discover -s tests -v
python -m finance_tracker.cli demo
python -m finance_tracker.cli actual-export --input data\poc-transactions.json --output data\actual-import.json
python -m finance_tracker.cli month-close --input data\sample_transactions.json --month 2026-08 --statement-status data\2026-08-statement-status.json --output data\reports\2026-08.md
python -m finance_tracker.cli browser-adapters-status --sources config\browser-sources.json --adapters-root browser_adapters
```

Browser acquisition is an alternate source, not a second ledger. Provider/data recipes describe the exact authenticated UI path; official CSV/XLSX/PDF artifacts are normalized into the same staging, rules, review, and Actual import pipeline as email statements. See `docs/browser-ingestion.md`.

Statement PDFs, normalized browser captures, and official browser exports enter
the versioned workflows under `integrations/n8n`. There is no ingestion bridge,
SSH submission wrapper, or second transaction store. The fixed-purpose Actual
node writes through `@actual-app/api` only after validation and review gates.

## Runtime model

The target adapter writes ordinary finance records to Actual Budget through its official Node API. n8n owns bank-specific Outlook retrieval and schedule orchestration.

The companion SQLite store owns the cashback mailbox cursor and live cashback state. n8n owns other acquisition cursors. OneDrive owns evidence originals and its JSON catalogue. Individual notifications update cashback pace, bucket headroom, warnings, and routing recommendations immediately. Each card has an independent statement job that reconciles the live state, finalizes that card's cashback cycle, opens the next configured period, and extracts the actual payment due date.

`config/codex-automations.json` contains three local automation entries for
installation drift checks. It is not the canonical production scheduler and does
not establish production activity; n8n owns production schedules and orchestration.

The launcher prompts delegate to reusable runbooks under `agents/automations/`.
Audit a local Codex installation with:

```powershell
python -m finance_tracker.cli automation-audit `
  --manifest .\config\codex-automations.json `
  --project-root . `
  --automation-root "$env:USERPROFILE\.codex\automations"
```

The companion also recalculates its time-sensitive dashboard every minute. This advances weekly pace and final-week warnings without requiring a new transaction, and emits one deduplicated push warning per stale-ingestion episode. A separate five-minute host timer probes Actual and Cashback Control, skips cleanly while the quiesced backup owns its lock, restarts only the exact unhealthy container, and fails visibly if recovery or the 48-hour backup-age gate fails. n8n and its Postgres database use their own stack health checks.

Statement adapters emit normalized, reviewable rows and an exact balance reconciliation check. Passwords are loaded from runtime secrets or supplied interactively; they are never stored in source files or logs. A successful parse is not a successful close: a card period is finalized only after the staged statement rows have been matched to the live transaction ledger.

See `AGENTS.md` for architecture and extension rules.

## Reusable cashback deployment

Cashback Control is not tied to the bundled cards. Supply a validated JSON profile through `CASHBACK_PROGRAM_CONFIG_PATH`; the container and UI derive card names, short labels, currency, tiers, caps, transaction bucket assignment, alerts, and routing trees from that profile. See `docs/cashback-profile.md`, `config/cashback-profile-schema-v1.json`, and `examples/cashback-profiles/`.
