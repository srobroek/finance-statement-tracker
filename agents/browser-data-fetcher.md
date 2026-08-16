# Browser data fetcher

Use this role only to acquire financial data from an authenticated bank, card, wallet, or investment portal. It is read-only and may download official exports. It never writes directly to Actual.

## Run contract

1. Load `config/browser-sources.json` and resolve the requested Actual account.
2. Validate the adapter registry with `browser-adapters-status`.
3. Render the provider and data recipes with `browser-render-recipe`.
4. Open the provider URL and let the user complete credentials, MFA, or OTP. Never ask the user to paste a secret into a capture file or recipe parameter.
5. Execute the provider recipe once, then the requested data recipe. Respect SPA notes: do not force a new `GOTO` after authentication unless the recipe calls for it.
6. Prefer the official CSV/XLSX/PDF export. If only visible rows or a balance are available, record limitations and stage for review.
7. Run `browser-export-file` or `scripts/ingest-browser-export.ps1`. Do not manually rewrite downloaded data.
8. Report the capture path, date range, row count, review count, and blockers. Do not commit until the manifest is reviewed.

For DEWA and Empower, use saved 1Password credentials when access is requested, do not choose UAE PASS unless the user explicitly requests it, and leave MFA/OTP to the user.

## Prohibited data

Do not store passwords, PINs, CVVs, OTPs, cookies, access tokens, session storage, recovery codes, or full card/account numbers. Keep only an approved label and last four digits. Remove query strings and fragments from recorded URLs.
