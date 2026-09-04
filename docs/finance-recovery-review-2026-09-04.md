# Finance recovery review — 2026-09-04

This review repairs the checked-in runtime and ingestion contracts. Production
activation and historical ledger imports still require the host acceptance
evidence tracked in [issue 87](https://github.com/srobroek/finance-statement-tracker/issues/87).

## Why ingestion could stop

- Earlier work was merged into an execution branch rather than `main`. Its
  commit count overstated the actual difference because `main` had a squash
  merge. Recovery therefore applies reviewed changes to the current main
  baseline instead of merging every historical branch change.
- The proposed four-table migration was incomplete. Most workflow references
  need field and identity adapters, not a table-name substitution. Bootstrap
  now creates the seven operational tables current workflows actually use
  alongside the four target tables, without deleting existing tables or data.
- Source templates are deliberately disabled until real credentials, folder
  IDs, account UUIDs, and subworkflow IDs are bound. A successful workflow
  import is not evidence that acquisition is active.
- AI submission readback compared a job ID that was not persisted in the
  canonical row. Readback now uses the persisted request identity.
- Deployment contracts disagreed about finance network membership, the PDF
  socket path, Actual container identity, and the Cashback listener.

These are code and configuration findings. They do not establish which error
the production host most recently encountered; that requires authenticated
execution logs and destination readback.

## Simplification and correctness

- Keep Actual as the sole posted ledger, Cashback SQLite as live operational
  state, and OneDrive as immutable document evidence.
- Provision only runtime-referenced compatibility tables. Do not create eight
  unused legacy stores or perform a destructive selector-only cutover.
- Use bounded AI input fields and deterministic validation. Provider prompts
  do not need mailbox or archive identifiers to propose classifications.
- Preserve manual rule locks and classifications. Reject malformed dates,
  non-finite money, ambiguous booleans, duplicate message IDs, and inconsistent
  receipts before advancing state.
- Use source-specific ingestion freshness and the configured operational
  timezone for Cashback. Indexed date ranges avoid applying SQL functions to
  every stored event in ordinary range queries.
- Keep empty, loading, stale, and failed UI states distinct, including failed
  alert updates and overlapping refresh requests.
- Run the same comprehensive validation entry point locally and in CI:
  `./scripts/run-validation.sh`. Container workflow replay is a separate CI job.

## Historical evidence

The read-only Outlook inventory found 31 unique ADCB monthly statement PDFs,
covering February 2024 through August 2026 with no missing statement months.
The prior OneDrive extraction catalogue contains 604 rows dated January 4
through May 8, 2026: 599 ADCB, one FAB, and four Wio. Three repeated ADCB groups
contain six rows with the same source page, date, amount, and description.
Resolve these against original documents; identical purchase facts alone are
not sufficient grounds to remove a transaction.

RAKBANK and Standard Chartered statement adapters remain explicitly interim
until real statements are available. This must not block supported statement
sources or the separate RAK live-notification path.

## Verification boundary

The disposable n8n container runs without a production network or credentials.
It provides executable workflow evidence, not production authentication proof.
Outlook and OneDrive connector access likewise does not prove that n8n's own
Microsoft credentials refresh correctly. Production completion requires
replay, restart, exact Actual readback, and Cashback cursor/receipt checks.

No historical transactions were imported during the read-only inventory.
