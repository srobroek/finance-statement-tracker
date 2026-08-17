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

The Docker host has 1Password CLI 2.34.1 and a Finance-only service-account
bootstrap at `/opt/stacks/finance-runtime/.env.bootstrap`.
`deploy/finance-runtime/finance.env.tpl` contains
only `op://` references; `deploy/finance-runtime/render-env.sh` renders them atomically into
`/opt/stacks/finance-runtime/.env` with mode `0600` immediately before either
independent Compose project is deployed. The bootstrap token is never rendered,
mounted into a container, or copied into a job payload.

The runtime item owns the Actual application credentials, ingestion tokens,
bank statement passwords, and VAPID keypair. The cashback and ingestion
containers receive only the variables declared in their Compose environment.
`runtime_secret()` still supports `<NAME>_FILE` for local or alternate secret
providers, but production uses the injected environment to avoid a second
credential bridge.

The item lives in the dedicated `FinanceRuntime` vault. The host service account
is granted view/copy access to that vault only; it does not receive access to the
owner's Private vault or any Bellwether secrets.

Changing a 1Password field does not mutate a running container. Re-run the
corresponding deployment workflow (or the renderer followed by that project's
`docker compose up -d`) to inject a fresh process environment.

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

The Docker companion does not poll Actual for live routing. The hourly Codex ingestion job submits minimal provisional cashback events from email or another supported source to the companion, which recalculates immediately. The browser refreshes its view every minute. Actual receives authoritative statement transactions on each configured statement cycle; the close job then reconciles and finalizes the companion's provisional events.

The output contains card pace, current tier, bucket spend and headroom, routing mode, current payment recommendations, and review count. Late in a cycle, a card that is materially under pace is valued at its current tier instead of pretending an unreachable target tier will be achieved.

## Scheduling

- Live transaction notifications: ingest when available; these drive provisional cashback.
- Statement jobs: one schedule per card, based on that card's configured statement date.
- Reconciliation: run only after the relevant statement arrives.
- Cashback close: finalize only after that card statement is ingested and reconciled.
- Aggregate month close: event-driven after all required card periods close.

The current tentative schedule remains month-end with processing on the first day of the following month. Active Codex tasks are:

| Time (Asia/Dubai) | Task | Behaviour |
|---|---|---|
| Hourly at `:05` | Transaction and evidence ingestion | Full durable-cursor gap plus overlap; provisional cashback only. |
| 20:00 on day 1 | RAKBANK World statement | Leaves the period open while its statement source/adapter remain placeholders. |
| 20:20 on day 1 | Standard Chartered Platinum X statement | Leaves the period open while its statement source/adapter remain placeholders. |
| 20:40 on day 1 | Emirates Islamic Amazon statement | Stage, AI handoff when requested, Actual preflight/commit, cashback reconciliation, and guarded finalization. |
| 21:00 on day 1 | ADCB statement | Stage, AI handoff when requested, Actual preflight/commit, and due-date reminder; no companion close unless ADCB is added to a versioned cashback profile. |
| 21:20 on day 1 | Wio statement | Preserve multi-account suffix mapping, then stage and commit only when every row maps cleanly; no companion close unless Wio is added to a versioned cashback profile. |

The legacy daily aggregate month-close task remains paused. The attachment-retrieval step is performed by the card/source-specific Codex task; the deterministic command begins only after the exact PDF attachment and Outlook evidence IDs have been captured.

## Live Outlook notifications

The hourly Sol automation, scheduled at minute 5 in Asia/Dubai, writes exact fetched transaction-notification message objects into one envelope and submits it to the continuous companion:

```powershell
.\scripts\push-outlook-messages.ps1 `
  -InputPath .\runtime\outlook-message-batch.json
```

The container then performs parsing, card mapping, the live static-rule subset, normalized-identity deduplication, SQLite persistence, cashback calculation, and dashboard refresh. Sol inspects only unresolved results and performs the evidence-aware AI policy stage through auditable correction calls. Codex is not installed in the container and no container-side AI is assumed. Only after those corrections and evidence writes succeed does the task commit the candidate cursor through the companion's ingest-run endpoint.

The hourly path uses only the `LIVE_CASHBACK` rule set configured under `cashback-programs.json/live_ingestion`. Rule-set membership is metadata on the canonical rules, not a second rule implementation. Budget-only classification, property, subscription, and evidence rules remain in the full statement pipeline and do not burden live routing. Cadence does not limit coverage: the worker always resumes from the last durable cursor minus overlap and therefore catches up across missed runs.

Transaction notifications are retrieved in full from the durable cursor minus the configured overlap. Statement-PDF retrieval is different: each card-specific job selects only the latest statement message for that card/account and expected cycle, except for an explicit retry or backfill.

Production notification adapters currently support amount-bearing ADCB card-authorization emails and verified RAKBANK World transaction emails. An ADCB OTP proves an attempted authorization, not settlement, so it is stored with confidence below 0.8, `review_required=true`, and `PROVISIONAL` status. The RAKBANK adapter requires the registered sender, exact transaction subject, amount, currency, merchant, card suffix, and transaction day/month; same-subject activation messages fail closed. RAKBANK notification emails do not expose Apple Pay usage, so unresolved RAK_WORLD channels use the explicit profile default after merchant-specific channel rules. All notification events remain provisional until statement reconciliation. Standard Chartered and Emirates Islamic transaction adapters remain unavailable until representative notification formats exist.

## Backup

The CI host runs `finance-backup.timer` daily at approximately 03:15 Asia/Dubai. It archives the Actual bind mount, cashback event store, compose source, and a secret-free manifest under `/opt/backups/finance-actual-poc/`, then verifies the archive checksums. See `docs/backup-and-restore.md` for restore and validation commands. Keep an off-host copy of this backup root and create an Actual `.zip` export before upgrades.

## Monitoring and recovery

`finance-health-monitor.timer` runs every five minutes on the Docker host. It probes the Actual proxy, Cashback Control, and the ingestion worker twice before acting. It skips while the backup lock is held, never pulls an image, and only restarts or recreates the exact service owned by its independent Compose project. A failed recovery or a backup older than 48 hours makes the oneshot unit fail and leaves structured evidence in the system journal:

```bash
systemctl status finance-health-monitor.timer finance-health-monitor.service
journalctl -u finance-health-monitor.service --since today
```

Cashback Control independently rebuilds the dashboard every minute. This makes weekly pace, close-window warnings, and feed age advance even when no purchase arrives. When the hourly mailbox cursor is older than the configured stale threshold, the installed PWA receives one deduplicated `Cashback feed is stale` notification for that ingestion episode; a later successful cursor commit arms the next episode.

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

## Cashback PWA and Declarative Web Push

Publish the independent cashback service as a second application route on the
existing Cloudflare Tunnel:

- Public hostname: `cashback.vxsan.com`
- Type: `HTTP`
- Service URL: `http://172.20.10.20:5010`
- Path: blank
- Cache: bypass for the entire hostname

Cloudflare terminates public HTTPS and forwards ordinary PWA and JSON API
requests to the companion. It does not relay push delivery. On subscription,
iOS returns an Apple-managed HTTPS push endpoint; the companion sends the
encrypted declarative payload directly to that endpoint using its VAPID key.
Cloudflare is involved again only when the user opens the notification's
`https://cashback.vxsan.com/?screen=...` navigation URL.

Cloudflare Access may protect the hostname. Authenticate in Safari before adding
the app to the Home Screen. An expired Access session can require login when a
notification is opened, but it does not prevent the notification itself from
arriving. Do not put Access service tokens, cookies, or tunnel credentials in
the PWA.

The client targets current iOS Declarative Web Push directly through
`window.pushManager`; there is deliberately no service-worker notification
fallback. On the phone:

1. Open the HTTPS hostname in Safari and complete Cloudflare Access.
2. Choose **Share > Add to Home Screen**.
3. Launch **Cashback Control** from the new icon.
4. Tap **Enable alerts** and allow notifications.

The server sends an immediate test notification. Later dashboard rebuilds emit
deduplicated notifications when a cashback bucket becomes full, a card is
inside its final seven days without its configured target, or a routing result
changes. Subscriptions and delivery receipts remain in the companion SQLite
store and survive container replacement.

## Primary documentation

- [Actual API](https://actualbudget.org/docs/api/)
- [Actual API reference](https://actualbudget.org/docs/api/reference/)
- [Actual rules](https://actualbudget.org/docs/budgeting/rules/)
- [Actual Docker deployment](https://actualbudget.org/docs/install/docker/)
- [Actual reverse proxies](https://actualbudget.org/docs/config/reverse-proxies/)
- [Actual backup and restore](https://actualbudget.org/docs/backup-restore/backup/)
