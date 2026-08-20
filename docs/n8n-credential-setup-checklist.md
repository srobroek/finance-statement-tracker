# n8n finance credential and provider setup checklist

Checked-in workflow exports declare inactive state and placeholder credential IDs.

No Outlook or OneDrive credential exists in the disposable baseline. Placeholder
IDs in workflow JSON are declarations, not proof that authentication is ready.
Do not create OAuth credentials without the user present for the browser consent
step.

## Microsoft acquisition and evidence

- [ ] Create one least-privilege Microsoft Outlook OAuth2 credential for
  `BIND_OUTLOOK` and complete browser consent.
- [ ] Confirm the Outlook identity can read only the intended mailbox/folders.
- [ ] Create one Microsoft OneDrive OAuth2 credential for `BIND_ONEDRIVE` and
  complete browser consent.
- [ ] Confirm the OneDrive identity can upload, download, and read metadata only
  under the approved Finance Evidence root.
- [ ] Bind placeholders after import; do not edit checked-in JSON with real IDs.
- [ ] Execute one attachment PDF, one non-PDF attachment, and one attachmentless
  HTML-body message in the disposable project. Verify immutable upload ID, eTag,
  SHA-256 download readback, and redacted Data Table receipt.
- [ ] Keep the >4 MiB upload-session path and HTML/MIME-to-PDF renderer blocked
  until exact implementations pass disposable tests.

## Statement and Actual

- [ ] Bind `BIND_CARD_PASSWORD` to `financeStatementPassword`; do not place
  statement passwords in workflow JSON or Data Tables.
- [ ] Bind `BIND_ACTUAL_API` to `actualBudgetApi` only in the reviewed disposable
  project first.
- [ ] Leave `ALLOW_ACTUAL_WRITES` false until double replay, fenced-lease,
  crash-recovery, exact economic readback, and reconciliation gates pass.

## Finance MCP origin bearer

- [ ] Create `finance_n8n_mcp_bearer` once in
  `FinanceRuntime/Finance Statement Tracker Runtime` through the approved
  operator mutation gate. The field is concealed and must not be overwritten,
  deleted, recreated, or rotated.
- [ ] Keep the bearer available only as
  `FINANCE_N8N_MCP_BEARER` through the `op` runtime-injection boundary. Do not
  place it in `.env`, workflow JSON, command arguments, logs, receipts, or
  generated configuration.
- [ ] Run `deploy/finance-runtime/bind-finance-mcp-facade.py` with the pinned
  n8n CLI. Verify one encrypted `httpBearerAuth` credential named `Finance MCP
  Facade Bearer`, a `credential:owner` relation for the finance project, and
  inactive workflow 15. The credential file must be mode `0600` under
  `/run/finance-mcp-binder` and removed before exit.
- [ ] Verify workflow 15 exposes the exact path
  `/mcp/finance-operations-v1` and retains the
  `BIND_FINANCE_MCP_FACADE` placeholder in the checked-in export.
- [ ] Set `FINANCE_N8N_MCP_DISPOSABLE_ACK=ACTIVATE_W15_ONLY` only for a
  disposable proof. The proof may temporarily publish W15 and must deactivate,
  unpublish, remove the live webhook registration, and verify inactive state
  before exit.
- [ ] Launch Codex with
  `deploy/finance-runtime/launch-codex-finance-mcp.sh`. The launcher rejects a
  pre-existing parent bearer and scrubs the child-only environment after both
  success and failure.

## Subscription agents

- [ ] Install exact integrity-pinned packages from
  `integrations/n8n/community-node-lock.json` into the immutable custom image.
- [ ] Assert the four finance custom node types plus ProDex and Claude node types
  register without `?` placeholders.
- [ ] Complete ProDex device login with the user and verify ChatGPT subscription
  auth; do not enable API-key fallback.
- [ ] Complete Claude CLI subscription login with the user.
- [ ] Prove ProDex read-only/new-thread/schema-bound Luna and gated Sol receipts.
- [ ] Prove Claude emits a schema-valid proposal and does not persist a session,
  or use a reviewed fork that enforces no-session persistence.
- [ ] Confirm provider/model/prompt/command/path/sandbox/credential controls are
  absent from every caller and supplied only by workflow 21.

## Promotion readback

- [ ] Import all 21 workflows inactive into the exact n8n 2.36.2 disposable
  project.
- [ ] Create/reconcile the eight folders from `workflow-folders.json`; verify
  membership through package response or direct read-only Postgres join because
  the public workflow GET omits write-only `parentFolderId`.
- [ ] Verify exact workflow tags and all `From list` subworkflow references.
- [ ] Confirm no workflow is published or active after credential binding.
