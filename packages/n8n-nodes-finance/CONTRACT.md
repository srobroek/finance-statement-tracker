# n8n finance node contract

Status: `SPEC_ONLY` until the exact image imports and executes every workflow in
a disposable n8n instance.

Package: `n8n-nodes-finance@0.1.0`

Build target: `n8n@2.36.2` (RC) with its bundled
`n8n-workflow@2.36.1`. This candidate replaces vulnerable stable 2.35.x only
for disposable validation; promotion remains `NO-GO` until the RC is accepted
or an equally patched stable release is available.

## Frozen node types

| Type | Version | Operations | Boundary |
|---|---:|---|---|
| `n8n-nodes-finance.financePdf` | 1 | `validate`, `unlock`, `profile` | Sends an in-memory PDF to the fixed Unix socket `/run/finance-pdf/pdf.sock`. `profile` performs fixed `statement-v1` text extraction inside the resource-limited sandbox and returns `extracted_text`. It exposes no URL, path, shell, command, or caller-selected parser parameter. |
| `n8n-nodes-finance.financeStatement` | 1 | `parse` | Uniquely detects and parses extracted text using build-time allowlisted issuer profiles. It exposes no caller-selected issuer/provider and never reads files, credentials, or URLs. |
| `n8n-nodes-finance.financeRules` | 1 | `normalize`, `projectActualImport`, `applyNonRepresentableRules` | Runs normalization, an explicit canonical-to-Actual projection, and rules explicitly owned by `N8N_ONLY`; source amount, direction, IDs, and reconciliation state are immutable. `TRANSACTION_NORMALIZATION` may set a provisional topic once, after which the topic is finalized and locked. The projection makes card debits negative and credits positive in integer minor units. |
| `n8n-nodes-finance.actualBudget` | 1 | `doctor`, `read`, `preflight`, `import`, `verify` | Uses `@actual-app/api` directly. Reads are fixed shapes; imports require a prepared outbox envelope, a live positive fencing lease, a non-manual/non-MCP context, and an enabled mutation credential. There is no AQL, command, path, URL, or arbitrary method surface. |

Credentials are `financeStatementPassword` and `actualBudgetApi`. The PDF
utility has no network or finance credential and the socket path is not
configurable by a workflow.

All Actual sessions download and sync before an operation, sync after a
mutation/readback, and call `shutdown()` in `finally`. `import` sets
`reimportDeleted: false`, disables category learning, rejects returned errors,
and is followed by the separate `verify` operation.

`verify` accepts the exact expected transaction rows, binds its observed shape
to the installed `@actual-app/api` `TransactionEntity` declaration, and compares
account, imported ID, date, integer amount, imported payee, category, notes, and
cleared state before returning canonical expected/observed hashes and account
balance. Unit mocks do not prove a particular deployed Actual server preserves
those fields; a disposable real-server import/readback remains a promotion
blocker.

Issuer profiles initially include only the already verified repository
adapters: `adcb_v1`, `emirates_islamic_v1`, and `wio_credit_v1`. Placeholder
RAKBANK and Standard Chartered statement profiles are intentionally absent.

The exported `N8N_RULE_COMPATIBILITY` matrix is authoritative for the
non-representable evaluator. It rejects unknown operators and rejects
Actual-owned tag actions (`add_tag`, `add_tags`, `remove_tag`) rather than
silently dropping them. The ownership compiler must fail before execution if a
rule labelled `N8N_ONLY` is outside that matrix.

Regex conditions are compiled and evaluated only by pinned RE2JS, a
linear-time RE2-compatible engine. Backreferences, lookarounds, and other
non-RE2 constructs fail validation; untrusted transaction text is never passed
to JavaScript's backtracking `RegExp` evaluator.
