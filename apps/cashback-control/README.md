# Cashback Control companion

This is the continuously running operational surface for payment routing: a small web app with a SQLite backend. It does not copy the Actual ledger. Hourly Outlook jobs submit exact message objects; the app parses supported formats, applies static rules, deduplicates provisional reward events, persists them, and rebuilds the dashboard immediately.

Run from the repository root:

```powershell
python .\apps\cashback-control\server.py
```

Open `http://127.0.0.1:5010`. Set `CASHBACK_DASHBOARD_PATH`, `CASHBACK_HOST`, or `CASHBACK_PORT` to override defaults.

This deliberately remains a companion instead of an Actual plugin. Actual's internal plugin work is not currently a supported third-party extension boundary.

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

The live service deliberately does not run the full budgeting rulebook. `cashback-programs.json` selects the `LIVE_CASHBACK` rule set from the canonical static-rule source: only the normalization, vendor/category, and bucket rules needed for routing. Full statement ingestion into Actual runs the complete staged rule pipeline. Rule-set membership is searchable metadata, while rule IDs remain stable audit keys.

`POST /api/events` remains available for other normalized transaction sources.

Submit one event or a list to `POST /api/events`. Use the configured bearer token. Every event needs a stable `source_event_id`, timestamp, card, and positive AED amount. Refunds use `event_type: REFUND`.

```json
{
  "source_event_id": "outlook-message-id:transaction-1",
  "occurred_at": "2026-08-16T12:34:00+04:00",
  "card_code": "RAK_WORLD",
  "amount_aed": "245.50",
  "currency": "AED",
  "purchase_type": "GROCERY",
  "channel": "PHYSICAL_POS",
  "merchant": "Carrefour",
  "source": "outlook",
  "status": "PROVISIONAL"
}
```

The only required companion secret is `CASHBACK_INGEST_TOKEN` when the endpoint is reachable beyond localhost.

The scheduled Codex worker does not need that secret. It sends its raw Outlook envelope over SSH and invokes `submit_local.py` inside the container, where the token is already available:

```powershell
.\scripts\push-outlook-messages.ps1 -InputPath .\runtime\outlook-message-batch.json
```

The SSH target and container name are stored once in `config/deployment.json`, with environment-variable overrides available for another deployment. Helper scripts no longer contain host literals.

The SQLite sidecar contains only provisional cashback events and their operational status. It exists for live routing, idempotency, refunds, corrections, review state, and statement reconciliation. Actual remains the authoritative financial ledger. At statement close, the reconciliation job confirms, reverses, or ignores provisional events and imports the statement into Actual.
