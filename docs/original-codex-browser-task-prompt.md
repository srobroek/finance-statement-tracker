# Original Codex browser task prompt

Copy the prompt below into the original Codex app when one user-authenticated
finance source needs a fresh capture.

```text
Run one on-demand finance browser capture in a headed browser.

Before starting, resolve these non-secret parameters from the registered
provider and data recipe. Stop if any value is missing or does not match the
registry:
- FAB_DEBIT_PROVIDER=fab
- FAB_DEBIT_DATA_ID=current-account-transactions
- ACCOUNT_LABEL=<one exact actual_account from config/browser-sources.json>
- DATE_RANGE_START=<ISO date, inclusive>
- DATE_RANGE_END=<ISO date, exclusive>
- SARWA_PORTFOLIO_REF=<one exact portfolio label or last four digits>
- SARWA_AS_OF_DATE=<ISO date>
- AMAZON_YEAR=<four-digit year>
- ADCB_CARD_REF=<one exact registered card reference>
- ADCB_STATEMENT_PERIOD=<one exact registered statement period when using a PDF>

Scope: choose exactly one source. Use the matching provider/data pair:
FAB_DEBIT renders `fab/current-account-transactions` with
`account_ref=ACCOUNT_LABEL`, `from_date=DATE_RANGE_START`, and
`to_date=DATE_RANGE_END`; SARWA renders `sarwa/holdings` with
`portfolio_ref=SARWA_PORTFOLIO_REF` and `as_of_date=SARWA_AS_OF_DATE`; AMAZON
renders `amazon/orders` with `year=AMAZON_YEAR`; ADCB renders
`adcb/credit-card-transactions` with `card_ref=ADCB_CARD_REF`,
`from_date=DATE_RANGE_START`, and `to_date=DATE_RANGE_END`, or renders
`adcb/credit-statement` with `statement_period=ADCB_STATEMENT_PERIOD`. Use one
safe user-approved account or portfolio label and last four digits when
available.

Security boundary:
1. Open only the registered provider URL and follow its deterministic recipe.
2. Pause at the login page and return control to the user.
3. The user completes username, password, MFA, OTP, passkey, or reCAPTCHA.
4. Never request, type, inspect, copy, read, store, log, or return any
   credential, OTP, cookie, access token, session state, recovery code, PIN,
   CVV, full account number, or full payment number.
5. Continue only after the user confirms that authentication is complete.
6. Do not persist a browser profile, cookie jar, local storage, session state,
   screenshot containing secrets, or downloaded login page.

Capture contract:
1. Load and validate config/browser-sources.json and the adapter registry.
2. Render the provider recipe and the requested data recipe. Do not add a URL,
   selector, parameter, or inferred value that the recipe does not declare.
3. Prefer the provider's official CSV, XLSX, PDF, or native export. Use visible
   rows only when the recipe provides no official export.
4. Produce one browser-capture-schema-v1 JSON artifact. Retain only safe labels,
   last four digits, visible values, date/as-of evidence, and limitations.
5. Set capture_contract to:
   {"capture_mode":"HEADED_ON_DEMAND","redaction":"REDACTED",
    "immutability":"SHA256_ARCHIVED",
    "handoff_workflow":"INTERACTIVE_ARTIFACT_HANDOFF",
    "actual_mutation":false,"cashback_mutation":false}
6. Compute the source/export content SHA-256 and set both
   `artifact.source_content_sha256` and `provenance.source_content_sha256` to
   that digest. Set
   `provenance.capture_id`, `provenance.captured_at`, and
   `provenance.hash_algorithm` to the capture identity, UTC timestamp, and
   `SHA-256`.
7. Serialize the capture JSON with sorted keys, UTF-8, and compact separators.
   Compute `expected_capture_sha256` from those exact bytes. Send the same
   bytes as the single binary `data` attachment to inactive n8n. The JSON
   envelope contains only `artifact_id`, `expected_source_sha256`, and
   `expected_capture_sha256`;
   do not pass local paths, URLs, capture payloads, credentials, or session
   metadata as envelope fields.
8. Call `INTERACTIVE_ARTIFACT_HANDOFF` with the three envelope fields. n8n
   parses and validates the binary with runtime AJV before upload. An exact
   match of both source and capture-binary hashes is a deterministic no-op,
   while either changed hash for the same artifact ID is rejected.
9. n8n uploads a new capture to the configured Finance Evidence root, computes
   a separate archived-binary hash, writes and reads back its durable receipt,
   then dispatches to the existing inactive headless route.

Source tasks:
- FAB_DEBIT: use provider fab and current-account-transactions with the exact
  `account_ref`, `from_date`, and `to_date` mappings above. Capture the
  requested debit/current-account balance or transaction export in AED. Keep
  the balance as an as-of snapshot; do not create a balance transaction.
- SARWA: use provider sarwa and holdings with exactly one `portfolio_ref` and
  `as_of_date`, never an account-wide holdings view. Capture only that
  portfolio's visible positions, cash, value,
  currency, and as-of timestamp. Keep an approved label or last four digits
  and exclude insurance coverage from wealth.
- AMAZON: use provider amazon and orders for exactly `year=AMAZON_YEAR`. Capture order ID, order date, shown
  total and currency, product title, stable product reference, and status.
  Treat this as supplemental evidence; never create a ledger transaction.
- ADCB: use provider adcb and the requested credit-card export or statement,
  resolving the exact card/date or statement-period parameters above.
  Follow the single-page-app recipe after login and retain only safe card
  identity, date range, rows, and parser limitations.
- FUTURE_MFA: use a registered provider only. If no provider and data recipe
  exist, stop with a blocker instead of inventing selectors or a login flow.

Headless handoff boundary:
- Inactive n8n owns bounded archive upload, hash/readback receipt, strict schema
  validation, parse completeness, normalization, enrichment, transaction
  matching, review gates, and retry. The validated capture dispatches to the
  existing inactive `SHARED_STATEMENT_PIPELINE` route.
- The single fenced Actual outbox writer owns reviewed ledger mutation.
- The Cashback companion owns cashback state and close.
- This headed task never activates n8n, writes Actual, writes Cashback, or
  becomes a second writer.

Completion report:
- Return only the provider, safe capture ID, SHA-256 identity, requested scope,
  date/as-of value, row or portfolio count, review count, and blockers.
- Never return raw rows, credentials, OTPs, cookies, session state, PINs, CVVs,
  recovery codes, full account numbers, or full payment numbers.
```

The prompt pauses before authentication, produces a hash-bound redacted
artifact, and sends only its identity plus the binary capture attachment to
inactive n8n.
