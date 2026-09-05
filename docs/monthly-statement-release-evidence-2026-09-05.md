# Monthly statement release evidence

This change repairs the monthly statement pipeline and its PDF boundary. It must
be deployed as a matching custom-node, Cashback API, PDF utility and workflow
release. A workflow-only import is insufficient. It preserves the current live
notification API, health endpoints and compact UI.

## Changed contracts

- The parser retains the immutable acquisition context. Packaged canonical
  rules apply the complete ledger rule set in stage/priority order.
- W03 checks `balance_tied`, reads categories/payees from the credential-bound
  Actual budget, projects their real IDs, and binds the expected closing balance
  to the issuer statement. W20 checks exact imported rows, amount and digest,
  then reads the account balance as of the statement end date before committing.
- Printed posting dates govern statement ledger dates when provided. Original
  transaction dates remain in notes and settlement traces. Wio prints one date
  column and can include earlier transaction dates in a later statement; those
  dates remain unchanged. Only the imported-ID lookup expands to cover them;
  the receipt period and closing-balance cutoff do not change. Opening balances
  must already be supported by genuine account history; no balancing rows are
  manufactured.
- Cashback settlement amounts are AED, with original FX facts retained in the
  trace. Notification matching uses the original transaction date. If posting
  moves the charge to another day, the immutable notification remains available
  for replay and a single active statement event controls period totals.
- Wio takes the non-Cashback branch. Payments-only statements can reconcile zero
  eligible purchase/refund events. Truly empty statements remain rejected.
- PDF validation accepts only explicit local page-view destinations as catalog
  OpenAction values. Action dictionaries, scripts, additional actions and
  embedded files remain rejected, including compressed objects. Locked PDFs
  report structural checks as pending until successful decryption.
- PDF profiling uses canonical pdfplumber geometry settings rather than PDF
  content-stream order, so issuer period labels remain paired with their dates.

## Local evidence and remaining deployment gates

The TypeScript suite covers parser-to-rules-to-Actual projection, trusted ID
mapping, manual-field replay, posting-date boundaries, Wio's earlier printed
dates, and historical closing-balance cutoff. SQLite tests cover settlement FX,
payments-only close and cross-month notification replay. The native Actual
SQLite integration verifies imports, splits, manual fields and historical ADCB
replay. PDF worker subprocess tests cover encrypted safe destinations, hidden
active content and geometry extraction. Unix socket tests require the Linux CI
runner because the local sandbox denies socket creation.

A real Wio monthly PDF was fetched using the exact configured sender/subject;
Python and TypeScript parsing agreed on 46 rows with zero reconciliation
difference. The bounded utility using the canonical extractor also passed that
fixture. Private originals and extraction receipts are deliberately excluded
from this source change. This is local fixture evidence, not a successful live
n8n-to-Actual monthly run. EI and ADCB require their existing password-bound
utility paths and rebuilt-image fixture validation.

Before activation, bind W03's new Actual credential declaration, regenerate and
import the matching workflow/binding contracts, preserve the existing historical
ADCB account and its IDs, and run one archived monthly statement followed by an
exact replay. Read back the committed outbox, exact Actual receipt, released
writer lease, reconciliation/close receipt and terminal cursor. Existing rows
previously imported using a different date must be reviewed explicitly; do not
rewrite historical dates silently. RAK/SC statement adapters remain inactive
placeholders until their own real monthly fixtures pass. ADCB remains historical
only, without a recurring acquisition schedule.
