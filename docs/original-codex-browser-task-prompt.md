# Original Codex browser task prompt

Copy the prompt below into the original Codex app when one user-authenticated
finance source needs a fresh capture.

```text
Run one on-demand finance browser capture in a headed browser.

Scope:
- Source: choose exactly one of FAB_DEBIT, SARWA, AMAZON, ADCB, or a provider
  already registered with the same MFA boundary.
- Account or portfolio: use one safe user-approved label and last four digits
  when available.
- Date or portfolio scope: use the requested bounded range or year.

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
6. Compute the SHA-256 identity and archive the original through inactive n8n.
7. Call INTERACTIVE_ARTIFACT_HANDOFF with only artifact_id and expected_sha256.
   Do not pass local paths, URLs, capture payloads, credentials, or session
   metadata in the handoff request.

Source tasks:
- FAB_DEBIT: use provider fab and current-account-transactions. Capture the
  requested debit/current-account balance or transaction export in AED. Keep
  the balance as an as-of snapshot; do not create a balance transaction.
- SARWA: use provider sarwa and holdings. Capture every requested portfolio's
  visible positions, cash, value, currency, and as-of timestamp. Keep an
  approved label or last four digits and exclude insurance coverage from wealth.
- AMAZON: use provider amazon and orders. Capture order ID, order date, shown
  total and currency, product title, stable product reference, and status.
  Treat this as supplemental evidence; never create a ledger transaction.
- ADCB: use provider adcb and the requested credit-card export or statement.
  Follow the single-page-app recipe after login and retain only safe card
  identity, date range, rows, and parser limitations.
- FUTURE_MFA: use a registered provider only. If no provider and data recipe
  exist, stop with a blocker instead of inventing selectors or a login flow.

Headless handoff boundary:
- Inactive n8n owns archive receipt, schema validation, parse completeness,
  normalization, enrichment, transaction matching, review gates, and retry.
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
artifact, and sends only its identity to inactive n8n.
