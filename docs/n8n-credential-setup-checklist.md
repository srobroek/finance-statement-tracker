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

## microsoft refresh and failure handling

- [ ] Run read-only WF23 with an expired access token and a refresh token.
- [ ] Record result counts and time bounds.
- [ ] Record the execution ID and safety flags.
- [ ] Record verification time without token or provider-content values.
- [ ] Restart only n8n.
- [ ] Rerun WF23.
- [ ] Compare redacted receipt fields with the pre-restart run.
- [ ] Leave task runners unchanged.
- [ ] Leave Postgres unchanged.
- [ ] Leave the host unchanged.
- [ ] Revoke each Microsoft consent grant.
- [ ] Rerun the negative-auth proof.
- [ ] Outlook read failure blocks cursor and evidence writes.
- [ ] OneDrive read failure blocks cursor and evidence writes.
- [ ] Classify `invalid_grant` as an authentication failure.
- [ ] Classify a missing refresh token as an authentication failure.
- [ ] Classify a scope denial as an authentication failure.
- [ ] Classify a missing credential as an authentication failure.
- [ ] Failures stop acquisition.
- [ ] Preserve the last cursor.
- [ ] Emit a redacted failure receipt.
- [ ] User-present consent precedes each retry.
- [ ] Keep the refresh proof pending until a receipt proves it.
- [ ] Keep the restart proof pending until a receipt proves it.
- [ ] Keep the revocation proof pending until a receipt proves it.
- [ ] Keep the negative-auth proof pending until a receipt proves it.

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

## subscription agents

- [ ] Install exact integrity-pinned packages from
  `integrations/n8n/community-node-lock.json` into the immutable custom image.
- [ ] Assert four finance nodes register without `?` placeholders.
- [ ] Assert ProDex nodes register without `?` placeholders.
- [ ] Complete the direct ProDex device login with the user through the pinned
  platform [`login-community-subscriptions.sh`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/scripts/login-community-subscriptions.sh)
  procedure.
- [ ] Keep API-key fallback disabled.
- [ ] Store ProDex auth at `/home/ci/.codex-n8n-community/auth.json`.
- [ ] Mount the auth file at `/home/node/.n8n/codex/auth.json`.
- [ ] Set the directory mode to `0700`.
- [ ] Set the file mode to `0600`.
- [ ] Keep the bind mount across n8n restarts.
- [ ] Exclude `auth.json` from ordinary platform backups.
- [ ] Use the platform's separate encrypted recovery path or repeat device login.
- [ ] Keep `auth.json` contents outside Git.
- [ ] Keep `auth.json` contents outside images.
- [ ] Keep `auth.json` contents outside workflow data.
- [ ] Keep `auth.json` contents outside logs.
- [ ] Keep `auth.json` contents outside receipts.
- [ ] Keep `auth.json` contents outside backups.
- [ ] Run `codex login status` with `CODEX_HOME=/home/node/.n8n/codex` inside the n8n container.
- [ ] Retain a redacted receipt with `auth_file_present`.
- [ ] Retain `auth_file_mode` in the receipt.
- [ ] Retain `login_status` in the receipt.
- [ ] Set `auth_contents_read=false` in the receipt.
- [ ] Retain the verification timestamp.
- [ ] Login rejection emits a redacted `AUTH_REQUIRED` or `AUTH_REVOKED` receipt.
- [ ] Stop subscription work until the user repeats device login.
- [ ] Prove ProDex read-only receipts.
- [ ] Prove ProDex new-thread receipts.
- [ ] Prove a schema-bound Luna receipt.
- [ ] Prove gated Sol receipts.
- [ ] Keep these claims `SPEC_ONLY` until three schema-valid receipts exist.
- [ ] Confirm provider/model/prompt/command/path/sandbox/credential controls are
  absent from every caller and supplied only by workflow 21.

## later credential reconciliation

The runtime owns the ProDex `auth.json` file. n8n owns Microsoft refresh state in
its encrypted credentials. A later 1Password reconciliation may add references
for these two state owners through the platform's existing `FinanceAutomation`
record and restore procedure. It must not create a second credential store or put
secret values in this repository. This reconciliation remains pending.

## Promotion readback

- [ ] Import all 19 workflows inactive into the exact n8n 2.37.10 disposable
  project.
- [ ] Create/reconcile the six folders from `workflow-folders.json`; verify
  membership through package response or direct read-only Postgres join because
  the public workflow GET omits write-only `parentFolderId`.
- [ ] Verify exact workflow tags and all `From list` subworkflow references.
- [ ] Confirm no workflow is published or active after credential binding.

## production identity receipt

Record one mode-`0600` redacted receipt for each promotion or recovery proof.
Include these fields:

- Finance source commit.
- Platform source commit.
- Immutable image digest.
- Registry digest.
- Compose project.
- Service names.
- n8n project ID.
- Workflow IDs.
- Data Table digest.
- Listener origin.
- Cloudflare connector identity.
- Route status.
- Outlook credential type.
- OneDrive credential type.
- Credential owner relations.
- Scope results.
- Authentication results.
- ProDex auth result.
- `auth_file_mode` without `auth.json` contents.
- Receipt status.
- Verification timestamp.
- `secret_values_recorded=false`.
- Exact rollback or recovery receipt reference.

The receipt excludes sensitive values. Missing identity fields keep promotion pending.

- Bearer material.
- Token values.
- Mailbox content.
- Document content.
- Financial plaintext.
