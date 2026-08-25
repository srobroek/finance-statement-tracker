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

## Microsoft refresh and failure handling

- [ ] Run the read-only WF23 proof after an access token expires while a refresh
  token remains available.
- [ ] Record result counts and time bounds.
- [ ] Record the execution ID and safety flags.
- [ ] Record the verification time without token or provider-content values.
- [ ] Restart n8n only, rerun WF23, and compare the redacted receipt fields with
  the pre-restart run. Do not restart task runners, Postgres, or the host for
  this check.
- [ ] Revoke each Microsoft consent grant, rerun the negative-auth proof, and
  confirm that Outlook and OneDrive reads fail before cursor or evidence writes.
- [ ] Classify `invalid_grant` as an authentication failure.
- [ ] Classify a missing refresh token as an authentication failure.
- [ ] Classify a scope denial as an authentication failure.
- [ ] Classify a missing credential as an authentication failure.
- [ ] Stop acquisition after an authentication failure.
- [ ] Preserve the last cursor.
- [ ] Emit a redacted failure receipt.
- [ ] Request user-present consent before retrying.
- [ ] Keep bounded reads pending until the receipt proves refresh behavior.
- [ ] Keep bounded reads pending until the receipt proves restart persistence.
- [ ] Keep bounded reads pending until the receipt proves revocation handling.
- [ ] Keep bounded reads pending until the receipt proves negative-auth behavior.

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
- [ ] Assert the four finance custom node types register without `?` placeholders.
- [ ] Assert the ProDex node types register without `?` placeholders.
- [ ] Complete the direct ProDex device login with the user through the pinned
  platform [`login-community-subscriptions.sh`](https://github.com/srobroek/n8n/blob/2c3286ae3c63a80b86ade945f19d419bf562874b/scripts/login-community-subscriptions.sh)
  procedure. Do not enable API-key fallback.
- [ ] Keep ProDex account state in the runtime-owned host path
  `/home/ci/.codex-n8n-community/auth.json`, mounted at
  `/home/node/.n8n/codex/auth.json`. Keep the directory mode `0700`, the file
  mode `0600`, and the bind mount persistent across n8n restarts.
- [ ] Exclude `auth.json` from ordinary platform backups.
- [ ] Use the platform's separate encrypted recovery path or repeat device login.
- [ ] Keep `auth.json` contents out of Git, images, workflow data, logs, receipts,
  and backups.
- [ ] Run `codex login status` with `CODEX_HOME=/home/node/.n8n/codex` inside
  the n8n container and retain only a redacted receipt with
  `auth_file_present`, `auth_file_mode`, `login_status`,
  `auth_contents_read=false`, and the verification timestamp.
- [ ] Emit a redacted `AUTH_REQUIRED` or `AUTH_REVOKED` receipt when
  `auth.json` is missing or rejected. Stop subscription work until the user
  completes device login again.
- [ ] Prove ProDex read-only receipts.
- [ ] Prove ProDex new-thread receipts.
- [ ] Prove schema-bound Luna receipts.
- [ ] Prove gated Sol receipts.
- [ ] Keep these claims `SPEC_ONLY` until three schema-valid receipts exist.
- [ ] Confirm provider/model/prompt/command/path/sandbox/credential controls are
  absent from every caller and supplied only by workflow 21.

## Later 1Password reconciliation

The runtime owns the ProDex `auth.json` file. n8n owns Microsoft refresh state in
its encrypted credentials. A later 1Password reconciliation may add references
for these two state owners through the platform's existing `FinanceAutomation`
item and restore procedure. It must not create a second credential store or put
secret values in this repository. This reconciliation remains pending.

## Promotion readback

- [ ] Import all 19 workflows inactive into the exact n8n 2.36.2 disposable
  project.
- [ ] Create/reconcile the six folders from `workflow-folders.json`; verify
  membership through package response or direct read-only Postgres join because
  the public workflow GET omits write-only `parentFolderId`.
- [ ] Verify exact workflow tags and all `From list` subworkflow references.
- [ ] Confirm no workflow is published or active after credential binding.

## Production identity receipt

Record one mode-`0600` redacted receipt for each promotion or recovery proof. It
must contain these identities and statuses:

- finance source commit;
- platform source commit;
- immutable image digest;
- registry digest;
- Compose project;
- service names;
- n8n project ID;
- workflow IDs;
- Data Table digest;
- listener origin;
- Cloudflare connector identity;
- route status;
- Outlook and OneDrive credential types;
- credential owner relations;
- scope results;
- authentication results;
- ProDex auth result and `auth_file_mode`, without `auth.json` contents;
- receipt status;
- verification timestamp;
- `secret_values_recorded=false`;
- exact rollback or recovery receipt reference.

Keep bearer material, token values, mailbox content, document content, and
financial plaintext out of the receipt. Treat every missing field as pending.
