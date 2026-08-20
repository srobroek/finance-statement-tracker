# Browser ingestion

Browser acquisition collects user-authenticated finance data in an on-demand
headed session. The browser task emits one immutable redacted export and never
writes Actual or Cashback.

```mermaid
flowchart LR
    R["Versioned provider recipe"] --> B["Headed browser task"]
    B --> U["User completes login and MFA"]
    U --> C["Immutable redacted capture"]
    C --> N["Inactive n8n archive and receipt"]
    N --> A["Validate, enrich, match, retry"]
    A --> W["Single fenced Actual writer"]
    A --> K["Cashback close path"]
```

## Source coverage

| Provider | Capture scope | Result |
|---|---|---|
| FAB | Debit/current-account balance and transaction export | AED transaction or account capture |
| Sarwa | Authenticated holdings and portfolio values | USD wealth snapshot with an as-of timestamp |
| Amazon UAE | Order history evidence | Supplemental evidence for transaction matching |
| ADCB | Historical credit-card export or statement | Redacted historical capture |

Each registered provider declares `HEADED_ON_DEMAND` execution,
`USER_COMPLETED` authentication, disabled session persistence, and the
`browser-capture-schema-v1` output contract.

## Operator flow

1. Choose one provider, one account label, and one data scope.
2. Validate the provider registry and render the provider and data recipes.
3. Open the provider URL in a headed browser and pause at the login page.
4. The user completes login, MFA, OTP, passkey, or reCAPTCHA in the browser.
5. Follow the deterministic recipe and prefer the official CSV, XLSX, PDF, or
   provider-native export.
6. If only visible rows or a balance is available, record the date/as-of value
   and the limitation in the capture. Do not infer omitted values.
7. Set `artifact.content_sha256` and `provenance.content_sha256` to the
   original export's SHA-256. Include the capture ID, UTC timestamp, and
   `SHA-256` algorithm in `provenance`.
8. Send the capture JSON as binary `data` with an envelope containing only
   `artifact_id` and the source `expected_sha256` from the capture artifact to the inactive
   `INTERACTIVE_ARTIFACT_HANDOFF` workflow.
9. n8n uploads the bounded binary to the configured Finance Evidence root,
   writes and reads back `finance_document_operations`, validates with AJV,
   then dispatches to the inactive `SHARED_STATEMENT_PIPELINE` route.
10. Review n8n validation, enrichment, transaction matching, retry state,
    archive receipt, and writer preflight before any production promotion.

The handoff envelope contains only `artifact_id` and the source
`expected_sha256`; the capture JSON is a single binary attachment. n8n records
both the source hash and the separately computed archived-binary hash in the
durable receipt. It does not log the capture payload.

## Ownership boundary

| Surface | Owner | Allowed result |
|---|---|---|
| Headed browser | User-assisted Codex task | Visible data or official export |
| Capture archive and validation | Inactive n8n | Hash-bound artifact and redacted receipt |
| Enrichment and transaction matching | n8n | Proposed or staged normalized data |
| Actual ledger mutation | Single fenced Actual writer | Reviewed outbox delta only |
| Cashback mutation and close | Cashback companion and its n8n flow | Source-scoped cashback state |

The headed task never activates n8n, calls Actual, calls Cashback, or creates a
second ledger writer. Amazon order captures remain supplemental evidence and do
not create ledger transactions.

## Security boundary

The user completes authentication in the headed window. Automation never
requests, reads, types, copies, stores, logs, or returns credentials, OTPs,
cookies, session state, PINs, CVVs, recovery codes, full account numbers, or
full payment numbers. Captures retain only user-approved labels or last four
digits. Recorded URLs omit query strings and fragments.

Use the [copy-paste browser task prompt](original-codex-browser-task-prompt.md)
when starting an acquisition from the original Codex app.
