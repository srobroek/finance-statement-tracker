# Finance Statement Worker POC

This project is the deterministic automation layer for an Actual-first personal finance tracker. It performs statement parsing, ordered multi-condition rules, idempotent ingestion, evidence matching, cashback tier simulation, and month-close validation.

The deterministic parser and rule engine use a small pinned Python dependency set. The continuously running cashback companion uses SQLite for durable operational state and `pywebpush` for iOS Declarative Web Push delivery.

The target is **Actual Budget as the primary ledger**, with a small continuous companion application for cashback control and OneDrive for evidence. See `docs/platform-evaluation.md`, `docs/actual-production.md`, and `docs/cashback-companion-decision.md`.

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
- A compact read-only cashback companion in `apps/cashback-control`, refreshed by `scripts/refresh-cashback-control.ps1`.
- A tabbed mobile-first cashback interface verified at the 428 x 926 iPhone 13 Pro Max viewport: Routing and its decision tree fill one screen, while Cards and History have dedicated screens without horizontal overflow.
- Installable iOS PWA support with declarative bucket-full, final-week target, and routing-change notifications; delivery state is deduplicated per device in the companion database.
- Conservative Outlook notification adapters that emit traceable provisional events only when card, amount, currency, merchant, and a usable timestamp are evidenced.
- Recipe-driven browser acquisition and deterministic official-export parsers migrated from the previous source app for ADCB, Emirates Islamic, FAB, Wio, generic CSV, and Sarwa capture.

Rules use the versioned AutoCat-style JSON contract in `config/static-rule-schema-v1.json`. The worker validates and evaluates it deterministically, while compatible rules are compiled into Actual. `rule_sets` provide searchable scopes such as `LIVE_CASHBACK` without duplicating rules.

## Bank adapter API

`finance_tracker.statements.BankStatementAdapter` is the extension boundary for banks. An adapter only detects and parses its own statement layout; it must emit `NormalizedStatement` and `NormalizedStatementTransaction`. Reconciliation, rules, cashback calculations, and the Actual bridge consume only those normalized objects.

The POC includes `emirates_islamic_v1`, `adcb_v1`, and `wio_credit_v1`. New banks register one adapter with `StatementAdapterRegistry`; downstream code does not change. RAKBANK and Standard Chartered remain explicitly non-importing placeholders until real fixtures pass parser and arithmetic tests. Statement passwords are supplied through runtime secrets or an approved credential store. They must never be committed to Git, emitted to logs, or copied into decision traces.

`finance_tracker.ingestion.stage_statement` converts the canonical statement into a reviewable staging batch using versioned account/card configuration. A statement can be `balance_tied` while `ledger_reconciled` remains false; only the later matching workflow may change the latter.

## Recreate locally

```powershell
git clone <private-repository-url>
cd Finance-Statement-Tracker
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m unittest discover -s tests -v
```

PDF extraction is isolated in the Actual ingestion container. Install the `statements` optional dependency only in an environment that parses statements; the deterministic normalization and calculation modules remain dependency-free.

## Run

```powershell
python -m unittest discover -s tests -v
python -m finance_tracker.cli demo
python -m finance_tracker.cli actual-export --input data\poc-transactions.json --output data\actual-import.json
python -m finance_tracker.cli month-close --input data\sample_transactions.json --month 2026-08 --statement-status data\2026-08-statement-status.json --output data\reports\2026-08.md
python -m finance_tracker.cli browser-adapters-status --sources config\browser-sources.json --adapters-root browser_adapters
```

Browser acquisition is an alternate source, not a second ledger. Provider/data recipes describe the exact authenticated UI path; official CSV/XLSX/PDF artifacts are normalized into the same staging, rules, review, and Actual import pipeline as email statements. See `docs/browser-ingestion.md`.

The continuously running `actual-ingestion` container accepts statement PDFs, normalized browser captures, and official browser exports through one authenticated job API. `scripts/push-actual-ingestion-job.ps1` reads its SSH target and container name from `config/deployment.json`, uploads one content-addressed artifact, and submits an idempotent STAGE, PREFLIGHT, or explicitly gated COMMIT job. The container is published privately to GHCR and deployed by `.github/workflows/actual-ingestion-image.yml`.

## Runtime model

The target adapter writes ordinary finance records to Actual Budget through its official Node API. Outlook messages are retrieved by the scheduled Codex task. The companion SQLite store owns the durable mailbox cursor and provisional cashback state; OneDrive owns evidence originals and its JSON catalogue. Individual transactions drive provisional cashback pace, bucket headroom, warnings, and routing recommendations. Each card has an independent statement job that reconciles the live ledger, finalizes that card's cashback cycle, opens the next configured period, and extracts the actual payment due date.

Four Codex automations are active: one end-of-day live transaction/evidence ingest at 23:50 Asia/Dubai and three card-specific monthly statement reconciliation jobs. All current card cycles are tentatively month-end, with reconciliation on the following first day. The former daily aggregate gate is paused. Every job is idempotent and leaves cursors or close state unchanged when a required connector is unavailable. The daily Sol job scans the complete durable-cursor gap plus overlap and submits exact Outlook message objects through `push-outlook-messages.ps1`; the continuous companion performs deterministic parsing, static rules, deduplication, persistence, and dashboard refresh in one acknowledged call. Sol handles only unresolved classification and related-email evidence after that deterministic pass. Failed payloads remain under `runtime` for retry.

Statement adapters emit normalized, reviewable rows and an exact balance reconciliation check. Passwords are loaded from runtime secrets or supplied interactively; they are never stored in source files or logs. A successful parse is not a successful close: a card period is finalized only after the staged statement rows have been matched to the live transaction ledger.

See `AGENTS.md` for architecture and extension rules.
