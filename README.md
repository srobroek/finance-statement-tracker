# Finance Statement Worker POC

This project is the deterministic automation layer for an Actual-first personal finance tracker. It performs statement parsing, ordered multi-condition rules, idempotent ingestion, evidence matching, cashback tier simulation, and month-close validation.

The deterministic parser and rule engine have no mandatory third-party Python packages. The continuously running cashback companion uses Python's standard-library SQLite driver for durable operational state.

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
- A mobile-first cashback interface verified at a 390 px viewport without horizontal overflow.
- Conservative Outlook notification adapters that emit traceable provisional events only when card, amount, currency, merchant, and a usable timestamp are evidenced.

Rules use the versioned AutoCat-style JSON contract in `config/static-rule-schema-v1.json`. The worker validates and evaluates it deterministically, while compatible rules are compiled into Actual. `rule_sets` provide searchable scopes such as `LIVE_CASHBACK` without duplicating rules.

## Bank adapter API

`finance_tracker.statements.BankStatementAdapter` is the extension boundary for banks. An adapter only detects and parses its own statement layout; it must emit `NormalizedStatement` and `NormalizedStatementTransaction`. Reconciliation, rules, cashback calculations, and the Actual bridge consume only those normalized objects.

The POC includes `emirates_islamic_v1` and `adcb_v1`. New banks register one adapter with `StatementAdapterRegistry`; downstream code does not change. Statement passwords are supplied through runtime secrets or an approved credential store. They must never be committed to Git, emitted to logs, or copied into decision traces.

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

PDF extraction is an optional runtime concern. Install `pdfplumber` in the worker environment that performs statement ingestion; the deterministic normalization and calculation modules remain dependency-free.

## Run

```powershell
python -m unittest discover -s tests -v
python -m finance_tracker.cli demo
python -m finance_tracker.cli actual-export --input data\poc-transactions.json --output data\actual-import.json
python -m finance_tracker.cli month-close --input data\sample_transactions.json --month 2026-08 --statement-status data\2026-08-statement-status.json --output data\reports\2026-08.md
```

## Runtime model

The target adapter writes ordinary finance records to Actual Budget through its official Node API. Outlook messages are retrieved by the scheduled Codex task. The companion SQLite store owns the durable mailbox cursor and provisional cashback state; OneDrive owns evidence originals and its JSON catalogue. Individual transactions drive provisional cashback pace, bucket headroom, warnings, and routing recommendations. Each card has an independent statement job that reconciles the live ledger, finalizes that card's cashback cycle, opens the next configured period, and extracts the actual payment due date.

Four Codex automations are active: one hourly live transaction/evidence ingest and three card-specific monthly statement reconciliation jobs. All current card cycles are tentatively month-end, with reconciliation on the following first day. The former daily aggregate gate is paused. Every job is idempotent and leaves cursors or close state unchanged when a required connector is unavailable. The hourly Sol job submits exact Outlook message objects through `push-outlook-messages.ps1`; the continuous companion performs deterministic parsing, static rules, deduplication, persistence, and dashboard refresh in one acknowledged call. Sol handles only unresolved classification and related-email evidence after that deterministic pass. Failed payloads remain under `runtime` for retry.

Statement adapters emit normalized, reviewable rows and an exact balance reconciliation check. Passwords are loaded from runtime secrets or supplied interactively; they are never stored in source files or logs. A successful parse is not a successful close: a card period is finalized only after the staged statement rows have been matched to the live transaction ledger.

See `AGENTS.md` for architecture and extension rules.
