# Finance Tracker Agent Guide

## Objective

Maintain a portable, Notion-first finance tracker. Notion owns business configuration and durable state; Python supplies deterministic execution that Notion cannot perform natively.

## Sources of truth

- Transactions, categories, vendors, rules, AI policies, evidence metadata, card programmes, period summaries, recommendations, alerts, and cursors: Notion.
- Receipts, bills, warranties, statements, and extracted documents: OneDrive, linked from Notion.
- Python: behaviour only. Do not hard-code user categories, vendors, or classification rules.
- Card programme seed data is a POC assumption and must be verified/versioned before production use.

## Execution order

1. Parse and normalise source records.
2. Apply static rules by stage and ascending priority.
3. Apply history/vendor matching.
4. Run scoped AI policies only for unresolved fields.
5. Calculate cashback and routing deterministically.
6. Search and link supporting email/documents when requested by evidence policy.
7. Persist results and a decision trace in Notion.

Individual transactions are the live, provisional cashback source. Recalculate pace, bucket headroom, warnings, and routing recommendations without waiting for a statement, but never mark `Cashback Finalized` from live notifications alone.

Statement and cashback close are scheduled independently per card. The current POC schedule is `TENTATIVE`: all card cycles end at month-end and their reconciliation jobs run on the following first day. A card closes only after statement evidence has been ingested and reconciliation has succeeded. The final reconciled ledger becomes the authoritative cashback source, then the next card period opens. Aggregate finance close is event-driven when the last required card period closes; the legacy daily gate remains paused. Payment due dates must come from the statement when available, while the configured 30-day offset is forecast-only.

Manual overrides always win. Never let an AI stage modify locked fields, transaction amounts, reward arithmetic, source IDs, reconciliation state, or deduplication keys.

## Rule semantics

- A rule matches when any condition group matches.
- A condition group matches when all conditions in that group match.
- Blank condition groups are invalid.
- `stop_on_match` stops later rules in the same stage only; later stages still run.
- Rule priority is ascending: 10 runs before 20.
- The Notion condition/action databases are the authoring UI. `finance_tracker.rules` is the canonical evaluator.

## Documents

Archive under `Finance Evidence/YYYY/MM/vendor-slug/` with filenames:

`YYYY-MM-DD__document-type__vendor__currency-amount__reference__hash8.ext`

Keep the hierarchy shallow. Store property/unit, category, warranty expiry, message ID, and transaction relation as Notion metadata.

`config/notion_manifest.json` contains workspace object IDs only; it contains no API secret. Update it when a database is replaced or the schema version changes.

## Verification

Run before handing off changes:

```powershell
python -m unittest discover -s tests -v
```

Add tests for every new rule operator, cashback edge case, and refund behaviour.
