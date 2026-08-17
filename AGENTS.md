# Finance Tracker Agent Guide

## Objective

Maintain a portable Actual-first finance tracker with a dedicated live cashback companion. Actual owns the authoritative ledger and budgeting model; the companion owns live notification-derived routing state and deterministic calculations.

## Sources of truth

- Posted transactions, accounts, payees, categories, budgets, schedules, and ordinary reports: Actual Budget in the target POC.
- Live cashback events, internal reconciliation state, period state, recommendations, alerts, and ingestion cursors: the cashback companion SQLite store.
- Card programmes, live rule-set membership, ingestion settings, AI policies, and deployable schema: versioned repository configuration until a dedicated admin UI replaces it.
- Receipts, bills, warranties, statements, and extracted documents: OneDrive, indexed by `Finance Evidence/catalogue.json`.
- Python and TypeScript: deterministic behaviour and adapters only. Do not hard-code user categories, vendors, or classification rules in executable code.
- Card programme seed data is a POC assumption and must be verified/versioned before production use.

## Execution order

1. Parse and normalise source records.
2. Apply static rules by stage and ascending priority.
3. Apply history/vendor matching.
4. Run scoped AI policies only for unresolved fields.
5. Calculate cashback and routing deterministically.
6. Search and link supporting email/documents when requested by evidence policy.
7. Persist authoritative rows to Actual, live operational state to the companion, and evidence links to the OneDrive catalogue.

Browser acquisition follows the versioned provider and data recipes under `browser_adapters/`. The authenticated browser may download an official export or capture explicit visible data, but it must never write directly to Actual. Convert the artifact to `browser-capture-schema-v1`, stage it, review any provisional rows, and only then use the standard Actual bridge. The user completes MFA/OTP. Never persist browser cookies, session state, passwords, full card numbers, PINs, or CVVs.

Valid notification transactions count in live cashback buckets immediately and require no user approval. Recalculate pace, bucket headroom, warnings, and routing recommendations without waiting for a statement, but never mark `Cashback Finalized` from live notifications alone. Source and reconciliation markers are internal bookkeeping only.

Statement and cashback close are scheduled independently per card. RAKBANK World and Standard Chartered Platinum X close on day 5 and reconcile on day 6; Emirates Islamic Amazon closes at month-end and reconciles on day 1. Wio statement ingestion runs on day 3 because its statement normally arrives on day 1 or 2, and Wio is outside the live cashback programme. A card closes only after statement evidence has been ingested and reconciliation has succeeded. The final reconciled ledger becomes the authoritative cashback source, then the next card period opens. Aggregate finance close is event-driven when the last required card period closes; the legacy daily gate remains paused. Payment due dates must come from the statement when available, while the configured 30-day offset is forecast-only.

Manual overrides always win. Never let an AI stage modify locked fields, transaction amounts, reward arithmetic, source IDs, reconciliation state, or deduplication keys.

## Rule semantics

- A rule matches when any condition group matches.
- A condition group matches when all conditions in that group match.
- Blank condition groups are invalid.
- `stop_on_match` stops later rules in the same stage only; later stages still run.
- Rule priority is ascending: 10 runs before 20.
- Canonical rule JSON is the portable authoring/compile contract and `finance_tracker.rules` is the evaluator. Actual receives only rules it can represent without semantic loss. `rule_sets` identify scoped subsets such as `LIVE_CASHBACK`.

## Documents

Archive under `Finance Evidence/YYYY/MM/vendor-slug/` with filenames:

`YYYY-MM-DD__document-type__vendor__currency-amount__reference__hash8.ext`

Keep the hierarchy shallow. Store property/unit, category, warranty expiry, message ID, transaction identity, and evidence links in the catalogue and relevant ledger/companion records.

## Verification

Run before handing off changes:

```powershell
python -m unittest discover -s tests -v
```

Add tests for every new rule operator, cashback edge case, and refund behaviour.
