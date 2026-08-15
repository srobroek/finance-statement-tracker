# Notion Finance Worker POC

This project is the deterministic execution layer for a Notion-first personal finance tracker. Notion is the source of truth for transactions, rules, card programmes, evidence metadata, tracker state, and calculated dashboard rows. The worker performs the operations that Notion formulas cannot safely express: ordered multi-condition rules, idempotent ingestion, cashback tier simulation, and month-close report generation.

The POC intentionally has no SQLite dependency. It also has no mandatory third-party Python packages.

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
- Unit tests for rules, refunds, tier uplift, routing, and reports.

## Bank adapter API

`finance_tracker.statements.BankStatementAdapter` is the extension boundary for banks. An adapter only detects and parses its own statement layout; it must emit `NormalizedStatement` and `NormalizedStatementTransaction`. Reconciliation, rules, cashback calculations, and the future Notion writer consume only those normalized objects.

The POC includes `emirates_islamic_v1` and `adcb_v1`. New banks register one adapter with `StatementAdapterRegistry`; downstream code does not change. PDF passwords are runtime-only inputs and must never be stored in Notion, Git, configuration, or logs.

`finance_tracker.ingestion.stage_statement` converts the canonical statement into a Notion-ready staging batch using the card identifiers loaded from Notion configuration. A statement can be `balance_tied` while `ledger_reconciled` remains false; only the later matching workflow may change the latter.

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
python -m finance_tracker.cli month-close --input data\sample_transactions.json --month 2026-08 --statement-status data\2026-08-statement-status.json --output data\reports\2026-08.md
```

## Runtime model

The production adapter will read and write the Notion databases through the public API and retrieve Outlook messages through Microsoft Graph. Individual transactions are ingested hourly and drive provisional cashback pace, bucket headroom, warnings, and routing recommendations. Each card has an independent monthly statement job that reconciles the live ledger, finalizes that card's cashback cycle, opens the next period, and extracts the actual payment due date. Aggregate finance close is event-driven after the final required card period is reconciled; missing statements keep only the affected card cycle open and raise an alert. The calculation modules are independent of either API and can therefore be moved without rewriting the business rules.

Four Codex automations are active: one hourly live transaction/evidence ingest and three card-specific monthly statement reconciliation jobs. All current card cycles are tentatively month-end, with reconciliation on the following first day. The former daily aggregate gate is paused. Every job is idempotent and leaves cursors or close state unchanged when a required connector is unavailable.

Statement adapters emit normalized, reviewable rows and an exact balance reconciliation check. Passwords are passed only at runtime and are never stored in Notion, source files, or configuration. A successful parse is not a successful close: a card period is finalized only after the staged statement rows have been matched to the live transaction ledger.

See `AGENTS.md` for architecture and extension rules.
