# Cashback Control companion

This is the continuously running operational surface for payment routing: a small web app with a SQLite backend. It does not copy the Actual ledger. Each active bank has an independent morning Outlook task, cursor, and retry envelope. The app parses supported formats, applies static rules, deduplicates live reward events, persists them, updates buckets immediately, and rebuilds the dashboard.

Run from the repository root:

```powershell
python .\apps\cashback-control\server.py
```

Open `http://127.0.0.1:5010`. Set `CASHBACK_DASHBOARD_PATH`, `CASHBACK_HOST`, or `CASHBACK_PORT` to override defaults.

This is a companion instead of an Actual plugin. Actual's internal plugin work is not a supported third-party extension boundary.

## Live ingestion

The normal Outlook entry point is `POST /api/outlook/messages` with one envelope:

```json
{
  "source": "outlook",
  "completed_at": "2026-08-16T16:20:00+04:00",
  "cursor": "2026-08-16T16:20:00+04:00",
  "messages": []
}
```

The response includes the deterministic parse result and idempotent event upsert result, but does not commit the mailbox cursor. After any required Codex evidence/classification corrections succeed, the task commits the same cursor through `POST /api/ingest-runs`. This two-phase acknowledgement ensures a partial AI/evidence failure cannot skip messages on the next run. `POST /api/ingest-state` returns the last committed cursor from SQLite.

Only sources marked `ACTIVE` in `config/transaction-email-sources.json` participate. Each active source declares its mailbox folder, sender, subject, adapter, and evidence semantics. A verified but intentionally unused parser can remain `DISABLED`; placeholders must remain unmatchable. The current live scan is RAKBANK-only. OTP/authorization messages, statements, refunds, card payments, receipts, bills, warranties, purchase evidence, and reminders are excluded from this scan.

The live service deliberately does not run the full budgeting rulebook. `cashback-programs.json` selects the `LIVE_CASHBACK` rule set from the canonical static-rule source: only the normalization, vendor/category, and bucket rules needed for routing. Full statement ingestion into Actual runs the complete staged rule pipeline. Rule-set membership is searchable metadata, while rule IDs remain stable audit keys.

`POST /api/events` remains available for other normalized transaction sources.

Submit one event or a list to `POST /api/events`. Use the configured bearer token. Every event needs a stable `source_event_id`, timestamp, configured card code, positive base-currency amount, and currency. Refunds use `event_type: REFUND`.

```json
{
  "source_event_id": "outlook-message-id:transaction-1",
  "occurred_at": "2026-08-16T12:34:00+04:00",
  "card_code": "CARD_ALPHA",
  "amount": "245.50",
  "currency": "USD",
  "purchase_type": "DINING",
  "channel": "ONLINE",
  "merchant": "Example Restaurant",
  "source": "outlook"
}
```

`amount` is always denominated in the event's `currency`. If `currency` is omitted, the deployed profile's base currency is used. The API still accepts `amount_aed` as a compatibility alias for existing UAE deployments, but new integrations should use `amount`.

`CASHBACK_INGEST_TOKEN` authenticates machine-to-machine ingestion endpoints. The browser never receives or stores this token.

## Browser content security

The dashboard response sends a same-origin Content Security Policy and the shell repeats it in `web/index.html`. It permits same-origin scripts, styles, images, API requests, and form submissions; disallows plugins and base-URL overrides; and disallows framing. The policy allows inline style attributes because the dashboard sets numeric progress widths through DOM style properties. Dashboard values from API responses and configuration are inserted with text nodes or DOM properties, not HTML parsing.

The browser acknowledgement and push-subscription endpoints are `POST /api/alerts/ack` and `POST /api/push/subscriptions`. A public deployment accepts these routes only when all of these conditions hold:

- `Content-Type` is `application/json`.
- `Host` matches the authority in `CASHBACK_PUBLIC_URL`.
- `Origin` exactly equals `CASHBACK_PUBLIC_URL`.
- `Cf-Access-Jwt-Assertion` passes issuer, audience, expiry, asymmetric algorithm, and JWKS signature checks.

Host and Origin checks are request-boundary checks; the JWT remains the authentication factor. JWKS responses are size-limited and cached; an unknown key causes one bounded refresh for key rotation.

Public mode requires `CASHBACK_ACCESS_ISSUER`, `CASHBACK_ACCESS_AUDIENCE`, and `CASHBACK_ACCESS_JWKS_URL` at startup. The local JWT exemption applies only when `CASHBACK_HOST`, the request client, and `CASHBACK_PUBLIC_URL` are all loopback. The Compose deployment publishes port 5010 on `127.0.0.1` so the Cloudflare Tunnel remains the public entry point.

Public `GET /api/dashboard`, `/api/periods`, `/api/health`, and `/api/push/config` requests require the same Cloudflare Access assertion and public `Host` boundary. `GET /api/health` also accepts the ingest bearer token for the private container health probe. Push subscription credentials and subscription counts are not returned by any API response.

The scheduled Codex worker does not need the browser Access assertion. It sends its raw Outlook envelope over SSH and invokes `submit_local.py` inside the container, where the ingest token is already available:

```powershell
.\scripts\push-outlook-messages.ps1 -InputPath .\runtime\outlook-message-batch.json
```

The tracked `config/deployment.json` is an environment-neutral example. Put host-specific values in ignored `config/deployment.local.json`, or set `FINANCE_DEPLOYMENT_CONFIG`, `FINANCE_DOCKER_HOST`, and `FINANCE_CASHBACK_CONTAINER`. Helper scripts contain no host literals.

The SQLite sidecar contains live cashback events and their internal reconciliation state. Valid notifications count in buckets immediately; there is no browser approval step. Actual remains the authoritative financial ledger. At statement close, the reconciliation job matches, replaces, reverses, or excludes notification events and imports the statement into Actual.

General corrections remain restricted to `POST /api/corrections` with the ingest bearer token.
