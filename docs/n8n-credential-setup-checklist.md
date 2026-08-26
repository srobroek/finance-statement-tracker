# credential setup

Checked-in exports use inactive state and placeholder credential IDs. The IDs
describe bindings. They do not prove that authentication works.

Keep OAuth creation with the user present for browser consent. The disposable
baseline has no Outlook or OneDrive credential.

## ownership boundaries

| credential | owner | scope |
|---|---|---|
| Outlook and OneDrive | Microsoft | mail and evidence |
| ledger API | disposable ledger project | ledger operations |
| Finance MCP bearer | internal facade | workflow calls |
| ProDex auth | host mount `/home/ci/.codex-n8n-community` | subscription agents |

Each receipt identifies its system. A login cannot prove ledger writes. Route
publication needs its own readback. Before promotion, bind each receipt to its
target.
Retain source and service IDs in a redacted receipt. A credential check never
opens the ledger gate.

## microsoft access

- [ ] create one least-privilege Outlook OAuth2 credential for `BIND_OUTLOOK`
- [ ] complete browser consent for Outlook
- [ ] check that the Outlook identity reads only the approved mailbox and folders
- [ ] create one OneDrive OAuth2 credential for `BIND_ONEDRIVE`
- [ ] complete browser consent for OneDrive
- [ ] check that OneDrive reads approved metadata
- [ ] check that OneDrive writes only in the Finance Evidence root
- [ ] bind placeholders in the imported project
- [ ] keep real IDs out of checked-in JSON

Run three disposable acquisition cases:

- one PDF attachment
- one non-PDF attachment
- one HTML-body message with no attachment

Read back these values:

- upload ID
- eTag
- file hash

Keep the receipt redacted. Keep large uploads pending. Keep the PDF renderer
pending. Disposable tests control path release.

## microsoft refresh

Run read-only WF23 with an expired access token and a refresh token. Record each
field below:

- result count
- time bounds
- execution ID
- safety flags
- verification time
Keep provider content and token values out of the receipt.

Restart only `n8n`, then rerun WF23. Compare redacted fields with the first run.
Leave these systems unchanged:

- task runners
- Postgres
- the host

Revoke each Microsoft consent grant. Run the negative-auth proof again.

Treat every entry below as an authentication failure:

- `invalid_grant`
- a missing refresh token
- a denied scope
- a missing credential
- an Outlook read failure
- a OneDrive read failure

Acquisition stops. Evidence writes stop.
Preserve the last cursor. Emit a redacted failure receipt. User-present consent
precedes each retry. Keep the four claims below pending:

- refresh
- restart
- revocation
- negative-auth

Promotion review uses one receipt for every submitted claim.

## statement and ledger

- [ ] bind `BIND_CARD_PASSWORD` to `financeStatementPassword`
- [ ] keep statement passwords out of workflow JSON and Data Tables
- [ ] bind `BIND_ACTUAL_API` to `actualBudgetApi` in the reviewed disposable project
- [ ] keep `ALLOW_ACTUAL_WRITES` false during replay and reconciliation checks

Keep the write gate closed until all checks pass:

- double replay
- fenced lease
- crash recovery
- exact economic readback
- reconciliation

## finance mcp bearer

Use the operator gate. It creates `finance_n8n_mcp_bearer` in this runtime:

```text
FinanceRuntime/Finance Statement Tracker Runtime
```
Keep the field concealed. Keep its identity stable. Do not delete,
recreate, or rotate it.

Expose the bearer only as `FINANCE_N8N_MCP_BEARER` through the `op` runtime
injection boundary. Keep it out of these locations:

- `.env`
- workflow JSON
- command arguments
- logs
- receipts
- generated configuration

Run `deploy/finance-runtime/bind-finance-mcp-facade.py` with the pinned `n8n`
CLI. Verify one encrypted `httpBearerAuth` credential named `Finance MCP Facade
Bearer`. Verify a `credential:owner` relation for the finance project. Verify
inactive workflow 15. Use mode `0600` for the temporary credential file under
`/run/finance-mcp-binder`. Before exit, remove that file.

Check that the path is `/mcp/finance-operations-v1`. Keep the
`BIND_FINANCE_MCP_FACADE` placeholder in the checked-in export.

Set `FINANCE_N8N_MCP_DISPOSABLE_ACK=ACTIVATE_W15_ONLY` only for a disposable
proof. The proof may publish W15 for its test. Deactivate and unpublish W15.
Remove the live webhook registration. Before exit, check inactive state.

Launch Codex with `deploy/finance-runtime/launch-codex-finance-mcp.sh`. The
launcher rejects a parent bearer and clears the child-only environment on both
success and failure.

## subscription agents

Install integrity-pinned packages from
`integrations/n8n/community-node-lock.json` into the immutable custom image.
Check four finance nodes and the ProDex nodes for registration without `?`
placeholders.

Complete the direct ProDex device login with the user by following the pinned
platform [`login-community-subscriptions.sh`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/scripts/login-community-subscriptions.sh)
procedure. Keep API-key fallback disabled.

The retained host owns ProDex auth at
`/home/ci/.codex-n8n-community/auth.json`. Mount the host directory at
`/home/node/.n8n/codex` in `n8n`. `n8n` reads
`/home/node/.n8n/codex/auth.json` as the container `node` user. The host owner and
container UID are different identities. Set directory mode `0700` and file mode
`0600`. Keep the bind mount across `n8n` restarts.

Keep `auth.json` out of ordinary platform backups. Use the platform's encrypted
recovery path or repeat device login. Keep its contents out of these locations:

- Git
- images
- workflow data
- logs
- receipts
- backups

Run `codex login status` inside the `n8n` container with
`CODEX_HOME=/home/node/.n8n/codex`. Retain a redacted receipt with:

- `auth_file_present`
- `auth_file_mode`
- `login_status`
- `auth_contents_read=false`
- verification timestamp

Login rejection emits a redacted `AUTH_REQUIRED` or `AUTH_REVOKED` receipt.
Stop subscription work until the user repeats device login.

Prove each receipt below:

- read-only ProDex
- new-thread
- schema-bound Luna
- gated Sol

Keep these claims `SPEC_ONLY` until three schema-valid receipts exist.

Workflow 21 supplies these controls. Keep them absent from callers:

- provider and model
- prompt and command
- path and sandbox
- credential and write flag

## later reconciliation

The retained host owns the ProDex `auth.json` file. The `n8n` container reads the
file through the mounted container path. `n8n` owns Microsoft refresh state in
encrypted credentials. A later 1Password reconciliation may add references
through the existing `FinanceAutomation` record and restore procedure.

Keep one state owner for each credential. Keep secret values out of this repo.
This reconciliation stays pending.

## promotion readback

- [ ] import all 19 workflows inactive into the exact disposable `n8n` 2.36.2 project
- [ ] create or reconcile six folders from `workflow-folders.json`
- [ ] read folder membership through the package response or a read-only Postgres join
- [ ] verify exact workflow tags
- [ ] verify every `From list` subworkflow reference
- [ ] leave every workflow unpublished and inactive after credential binding

The public workflow GET omits write-only `parentFolderId`. Use the package
response or the read-only database join for folder membership.

## production identity receipt

Record one mode-`0600` redacted receipt for each promotion or recovery proof.
Include the following fields:

The receipt ties each credential check to a named target and owner. It captures
the source commit and service identity used for the check. It records the
verification time and result status. Keep bearer and token values out of the
receipt. The promotion gate reads this receipt with route and workflow results.
Missing fields keep promotion pending.

- Finance source commit
- platform source commit
- immutable image digest
- registry digest
- compose project
- service names
- `n8n` project ID
- workflow IDs
- Data Table digest
- listener origin
- Cloudflare connector identity
- route status
- Outlook credential type
- OneDrive credential type
- credential owner relations
- scope results
- authentication results
- ProDex auth result
- `auth_file_mode` without `auth.json` contents
- receipt status
- verification timestamp
- `secret_values_recorded=false`
- exact rollback or recovery receipt reference

Exclude these values:

- bearer material
- token values
- mailbox and document content
- financial plaintext

Missing identity fields keep promotion pending until an operator supplies each
field.
