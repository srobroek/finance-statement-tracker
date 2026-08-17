# Actual operating guide

Actual is the authoritative ledger, budget, native rule engine, schedules UI, reconciliation surface, and reporting application. The Python worker decrypts and normalizes bank statements; the Node bridge is the only component that writes those rows into Actual.

## Runtime

- Server: `172.20.10.20`
- Container port: `5006`
- Image: `actualbudget/actual-server:26.8.1`
- Persistent data: `/opt/stacks/finance-actual-poc/data`
- Ingestion worker: `finance-actual-ingestion` on host-local port `5020`
- Ingestion state: `/opt/stacks/finance-actual-poc/ingestion-data`
- Actual/proxy Compose source: `deploy/actual-poc/compose.yaml`
- Cashback Compose source: `deploy/cashback/compose.yaml`
- Ingestion Compose source: `deploy/ingestion/compose.yaml`
- Local tunnel for administration: `http://127.0.0.1:15006`

The image is deliberately pinned. Review Actual release notes and export a budget backup before changing the tag.

The three Compose projects have separate lifecycle ownership. Actual and its
proxy share one project; cashback and ingestion each have their own project.
Never deploy them from a combined Compose invocation.

## Cloudflare Tunnel

Create a public hostname in the existing tunnel with these values:

- Type: `HTTP`
- Origin URL when `cloudflared` runs on the Docker host: `http://127.0.0.1:5006`
- Origin URL when the connector runs elsewhere on the LAN: `http://172.20.10.20:5006`
- Port `5006` is served by `finance-actual-proxy`; Actual itself is reachable only on the private Compose network.
- Actual, the Python base, and the unprivileged Nginx proxy are pinned by image digest. The proxy runs as UID/GID `101`, drops all Linux capabilities, and has a read-only root filesystem with a small `/tmp` tmpfs.
- Path: blank
- Cache: bypass for the entire Actual hostname

Actual must be reached through HTTPS. It emits the COOP/COEP headers required by its SQLite WebAssembly client. Do not add duplicate response headers at Cloudflare; duplicate COOP/COEP values can make the browser reject `SharedArrayBuffer`.

Cloudflare Access is recommended for the browser hostname. Allow only the owner's identity. Scheduled workers should use the private origin URL and Actual password, not the public Access-protected URL.

## Secrets

The bridge reads these process environment variables:

- `ACTUAL_SERVER_URL`
- `ACTUAL_PASSWORD`
- `ACTUAL_SYNC_ID`
- `ACTUAL_ENCRYPTION_PASSWORD` when end-to-end encryption is enabled
- `ACTUAL_DATA_DIR` for the disposable local API cache

Statement decryption uses `STATEMENT_PASSWORD` by default. Passwords are never command-line arguments, run-manifest fields, or Git configuration.

The Docker host has 1Password CLI 2.34.1 and Bellwether uses a service account
to render committed `op://` templates into a mode-600 runtime env file before
`docker compose up -d`. The service account currently has access only to the
`Bellwether` vault; it cannot read the existing Actual login in the owner's
`Private` vault. Production therefore keeps the rendered Actual values in the
host-only stack `.env` for now. To make future refreshes host-native, create a
dedicated Finance vault, grant that service account access, and add a separate
Finance `op inject` template. Do not reuse Bellwether's rendered `.env`, expose
its bootstrap token to a container, or overwrite the Finance `.env` because it
also holds unrelated companion secrets.

## Bootstrap

`config/actual-bootstrap.json` is the declarative setup source for accounts, category groups, categories, tags, payees, native schedules, and monthly budget amounts. Native Actual rules are compiled from the marked subset of `config/static-rules.seed.json`; the same business rule is not manually maintained in a second list. Rules that require OR-of-AND groups, evidence actions, protected fields, AI, or cashback logic remain in the deterministic worker.

Plan changes:

```powershell
.\scripts\actual-setup.ps1 bootstrap `
  -SyncId '<budget-sync-id>'
```

Apply the reviewed plan:

```powershell
.\scripts\actual-setup.ps1 bootstrap `
  -SyncId '<budget-sync-id>' `
  -Apply
```

Verify health and object counts:

```powershell
.\scripts\actual-setup.ps1 doctor -SyncId '<budget-sync-id>'
```

Bootstrap is idempotent. A clean second plan must return `changes: []`.

`schedules` and `budget_months` are intentionally empty until the owner supplies real recurrence dates and budget amounts. This is a data constraint, not missing plumbing. Schedule amounts and budget amounts are integer minor units (fils); the bridge creates or updates them by stable name/month and reports every planned change before applying it. Optional `owner` values on account records become owner tags on imported transactions because Actual does not expose a first-class account-owner field.

Statement payment reminders are separate from recurring bill configuration. After a successful commit, a statement with an evidenced future payment due date and positive closing balance creates a one-time Actual schedule on the card account. The schedule never auto-posts a transaction. Missing due dates, forecast-only dates, zero/credit balances, ambiguous account mappings, and already-past dates are skipped explicitly.

## Native reports

The production budget's `Main` reports dashboard includes two saved custom reports in addition to Actual's built-in overview widgets:

- `Spending by Category · Last 12 Months`: total-mode donut, grouped by category, payment transactions only.
- `Monthly Spending Trend · Last 12 Months`: time-mode line chart, monthly interval, payment transactions only.

These reports are native Actual objects and update as statement transactions are imported. They were verified in the authenticated Actual 26.8.1 UI. Actual does not currently expose a supported API for creating saved report-dashboard layouts, so this small UI-authored layer is documented here rather than falsely represented as bootstrap-managed configuration. The deterministic companion report remains the supported fallback for tag-filtered reporting; see `docs/tag-reporting.md`.

## Statement ingestion

Upload and stage through the container worker:

```powershell
.\scripts\push-actual-ingestion-job.ps1 `
  -InputPath 'C:\path\statement.pdf' `
  -Type STATEMENT_PDF `
  -CardCode EI_AMAZON `
  -SourceMessageId '<exact-outlook-message-id>' `
  -SourceAttachmentId '<exact-outlook-attachment-id>' `
  -ActualMode STAGE
```

The command performs these gates:

1. Upload the content-addressed artifact to the host-local worker inbox as a
   private UID/GID 10002 file, while preserving the original attachment name
   and exact Outlook message/attachment IDs in the manifest.
2. Enforce the active/placeholder statement-source registry before parsing.
3. Decrypt in memory.
4. Detect the registered bank adapter.
5. Parse to the bank-neutral statement model.
6. Prove statement arithmetic ties.
7. Map card suffixes to configured Actual accounts.
8. Apply deterministic rules and write an evidence-linked manifest.
9. Return constrained `ai_requests` for unresolved derived fields. A Sol task
   may submit proposal JSON using `-AIResponsesPath`; the policy engine rejects
   protected fields, populated values, disallowed values/tags, and low
   confidence proposals before rebuilding the envelopes.
10. Contact Actual only for PREFLIGHT or COMMIT.
11. Commit only when both caller and container write gates are explicitly enabled.

`imported_id` and worker job IDs are stable and `reimportDeleted` is false, so repeats do not duplicate transactions. Production enables writes through `ALLOW_ACTUAL_WRITES=true` in the host-only stack `.env`; the worker still requires explicit `COMMIT`, a review-free manifest, completed AI handoff, valid Actual credentials, and a target sync ID. CI fixtures remain staging-only and are never committed to the production budget.

AI is an explicit second submission, not an implicit container action. Save a
JSON array containing one object per answered request (`transaction_id`,
`policy_id`, `provider`, `model`, and `proposals`), then rerun the same source:

```powershell
.\scripts\push-actual-ingestion-job.ps1 `
  -InputPath 'C:\path\statement.pdf' `
  -Type STATEMENT_PDF `
  -CardCode EI_AMAZON `
  -SourceMessageId '<exact-outlook-message-id>' `
  -SourceAttachmentId '<exact-outlook-attachment-id>' `
  -AIResponsesPath '.\runtime\statement-ai-responses.json' `
  -AIHandoffComplete `
  -ActualMode STAGE
```

Unknown transaction/policy pairs fail the job. Omitted responses leave their
fields unresolved; accepted responses are recorded in `ai_trace` and flow into
the Actual import envelopes.

## Browser ingestion

Browser acquisition is supported for banks that expose account history or statements only through an authenticated portal. It uses the same normalized transaction contract and Actual bridge as statement ingestion; it does not maintain a separate ledger.

Validate every migrated recipe and configured account mapping:

```powershell
python -m finance_tracker.cli browser-adapters-status `
  --sources .\config\browser-sources.json `
  --adapters-root .\browser_adapters
```

After following the rendered provider/data recipe and downloading an official export, parse and stage it with a dry-run:

```powershell
.\scripts\ingest-browser-export.ps1 `
  -Provider adcb `
  -DataId credit-card-transactions `
  -File 'C:\path\adcb-export.csv' `
  -ActualAccount 'ADCB Credit Card · 8833 / 6838' `
  -SyncId '<budget-sync-id>'
```

Add `-Commit` only after reviewing the generated manifest. Visible-row captures also require `-ApproveReviewedRows`. Official PDFs are routed into the existing statement parser and arithmetic-reconciliation gates. Account overview balances remain reviewable snapshots and never create synthetic transactions. Full operating details are in `docs/browser-ingestion.md`.

## Cashback snapshot

Actual remains the only transaction ledger. The cashback engine consumes a read-only period snapshot:

```powershell
node .\integrations\actual\actualctl.mjs snapshot `
  --start 2026-08-01 `
  --end 2026-08-31 `
  --result .\runtime\actual-snapshot.json

python -m finance_tracker.cli cashback-dashboard `
  --snapshot .\runtime\actual-snapshot.json `
  --config .\config\actual-bootstrap.json `
  --output .\runtime\cashback-dashboard.json
```

The Docker companion does not poll Actual for live routing. The end-of-day Codex ingestion job submits minimal provisional cashback events from email or another supported source to the companion, which recalculates immediately. The browser refreshes its view every minute. Actual receives authoritative statement transactions on each configured statement cycle; the close job then reconciles and finalizes the companion's provisional events.

The output contains card pace, current tier, bucket spend and headroom, routing mode, current payment recommendations, and review count. Late in a cycle, a card that is materially under pace is valued at its current tier instead of pretending an unreachable target tier will be achieved.

## Scheduling

- Live transaction notifications: ingest when available; these drive provisional cashback.
- Statement jobs: one schedule per card, based on that card's configured statement date.
- Reconciliation: run only after the relevant statement arrives.
- Cashback close: finalize only after that card statement is ingested and reconciled.
- Aggregate month close: event-driven after all required card periods close.

The current tentative schedule remains month-end with processing on the first day of the following month. The attachment-retrieval step can be a Codex Outlook automation initially; the deterministic command begins once the PDF has been downloaded to the worker.

## Live Outlook notifications

The 23:50 Asia/Dubai Sol automation writes exact fetched transaction-notification message objects into one envelope and submits it to the continuous companion:

```powershell
.\scripts\push-outlook-messages.ps1 `
  -InputPath .\runtime\outlook-message-batch.json
```

The container then performs parsing, card mapping, the live static-rule subset, normalized-identity deduplication, SQLite persistence, cashback calculation, and dashboard refresh. Sol inspects only unresolved results and performs the evidence-aware AI policy stage through auditable correction calls. Codex is not installed in the container and no container-side AI is assumed. Only after those corrections and evidence writes succeed does the task commit the candidate cursor through the companion's ingest-run endpoint.

The end-of-day path uses only the `LIVE_CASHBACK` rule set configured under `cashback-programs.json/live_ingestion`. Rule-set membership is metadata on the canonical rules, not a second rule implementation. Budget-only classification, property, subscription, and evidence rules remain in the full statement pipeline and do not burden live routing. The schedule change does not change coverage: the worker always resumes from the last durable cursor minus overlap and therefore catches up across missed days.

Transaction notifications are retrieved in full from the durable cursor minus the configured overlap. Statement-PDF retrieval is different: each card-specific job selects only the latest statement message for that card/account and expected cycle, except for an explicit retry or backfill.

The first production adapter supports amount-bearing ADCB card-authorization emails. An OTP/authorization email proves an attempted authorization, not settlement, so it is stored with confidence below 0.8, `review_required=true`, and `PROVISIONAL` status. Messages that omit an amount or foreign messages that omit an AED equivalent are not ingested. RAKBANK, Standard Chartered, and Emirates Islamic adapters remain unavailable until representative notification formats exist.

## Backup

The CI host runs `finance-backup.timer` daily at approximately 03:15 Asia/Dubai. It archives the Actual bind mount, cashback event store, compose source, and a secret-free manifest under `/opt/backups/finance-actual-poc/`, then verifies the archive checksums. See `docs/backup-and-restore.md` for restore and validation commands. Keep an off-host copy of this backup root and create an Actual `.zip` export before upgrades.

## Cloudflare Tunnel and SharedArrayBuffer

Actual must be opened through the tunnel's HTTPS hostname. Direct LAN access to
`http://172.20.10.20:5006` is useful for health checks and API clients, but it is
not a secure browser context and therefore cannot enable `SharedArrayBuffer`.

In Cloudflare Dashboard, open **Networking > Tunnels**, select the existing
tunnel, then choose **Routes > Add route > Published application** with:

- Subdomain: the desired hostname, for example `actual`
- Domain: the Cloudflare-managed domain
- Path: blank
- Service URL: `http://172.20.10.20:5006`

If the tunnel daemon runs in Docker on the same host, the host URL above avoids
requiring it to join this Compose network. Leave TLS origin settings disabled
because Cloudflare provides browser-facing HTTPS and the tunnel-to-origin hop is
private HTTP. Open only `https://actual.<domain>` in a browser.

The Nginx proxy hides Actual's upstream isolation headers before emitting exactly
one canonical set:

```text
Cross-Origin-Embedder-Policy: require-corp
Cross-Origin-Opener-Policy: same-origin
Origin-Agent-Cluster: ?1
```

After the hostname is active, verify it from a client:

```powershell
$response = Invoke-WebRequest -Method Head https://actual.<domain>
$response.Headers['Cross-Origin-Embedder-Policy']
$response.Headers['Cross-Origin-Opener-Policy']
```

The expected values are `require-corp` and `same-origin`, each appearing once.
Create a Cloudflare Cache Rule that bypasses cache for this hostname. Cloudflare
WebSockets should remain enabled for Actual synchronization.

## Primary documentation

- [Actual API](https://actualbudget.org/docs/api/)
- [Actual API reference](https://actualbudget.org/docs/api/reference/)
- [Actual rules](https://actualbudget.org/docs/budgeting/rules/)
- [Actual Docker deployment](https://actualbudget.org/docs/install/docker/)
- [Actual reverse proxies](https://actualbudget.org/docs/config/reverse-proxies/)
- [Actual backup and restore](https://actualbudget.org/docs/backup-restore/backup/)
