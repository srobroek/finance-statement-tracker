# setup workflows

These exports live outside `integrations/n8n/workflows/`. The regular import contains
19 workflows. Reviewers import setup workflows one at a time.

## onedrive setup

`22-onedrive-finance-evidence-root-setup.json` is manual-only and inactive. It
uses the `Finance OneDrive` OAuth credential.

The workflow:

- lists the drive root.
- reuses the exact `Finance Evidence` folder when present.
- creates that folder at the drive root when absent.
- reads the root again.
- checks for one exact match.
- rejects `Finance Evidence/Finance Evidence`.

The final node emits a redacted receipt. It omits:

- record IDs.
- drive metadata.
- URLs.
- credential values.
- file contents.

Keep the workflow inactive and unscheduled. Before a manual run, import it into
`Global/Shared` and bind the existing `Finance OneDrive` credential. After the
review, remove the setup export.

## microsoft oauth proof

`23-microsoft-oauth-refresh-proof.json` is manual-only, inactive, and read-only.
Import it only for the reviewed proof.

The Outlook node:

- scans a frozen seven-day window.
- applies the server-side `isDraft eq false` filter.
- requests at most one message.
- projects only the message `id`.
- discards that identifier before the next node.

The OneDrive node lists the drive root once. The workflow has no provider-write
node and downloads no content. The final node keeps:

- result counts.
- time bounds.
- an execution ID.
- safety flags.
- a verification timestamp.

## run the deployed proof

Run `runner/run-transient-microsoft-oauth-refresh-proof.sh` on the deployed
host. Do not import or execute WF23 manually.

Set these variables from the deployed recovery receipt. The receipt supplies
the values below:

| Variable | Value |
| --- | --- |
| `FINANCE_N8N_COMPOSE_PROJECT` | Deployed Compose project name. |
| `FINANCE_N8N_STACK_DIR` | Deployed stack checkout. |
| `FINANCE_N8N_COMPOSE_FILE` | `compose.yaml` or an absolute path. |
| `FINANCE_N8N_DEPLOYMENT_ENV_FILE` | Mode-`0600` runtime environment file. |
| `FINANCE_N8N_RECEIPT_DIR` | Absolute mode-`0700` receipt directory. |
| `FINANCE_REPOSITORY_DIR` | Clean finance source checkout. |
| `FINANCE_REPOSITORY_COMMIT` | Exact 40-character finance commit. |
| `ORCHESTRATOR_REPOSITORY_COMMIT` | Exact 40-character stack commit. |
| `N8N_FINANCE_PROJECT_ID` | Explicit n8n project identifier. |

Resolve one n8n and one Postgres container from the explicit Compose project.
Check both containers for running and healthy state.

Before metadata reads, workflow import, or provider calls, run a transport
probe. The probe loads the extensionless n8n 2.36.2 configuration, resolves the
official `Execute` instance, and verifies its output hook.

Start with the reviewed `21 workflows / 0 active / 0 published` boundary. Import
one inactive workflow into `Global/Shared`. Restore the same 21-workflow
boundary during cleanup.

Read these four canonical Data Tables:

- `finance_ingestion_state`
- `finance_documents`
- `finance_actual_batches`
- `finance_ai_reviews`

Read the tables with the official `DataTableService`. Write no rows or schemas.
Credential readbacks omit:

- IDs and encrypted data.
- access and refresh tokens.
- client secrets.
- provider response bodies.

The proof sequence is:

- Use `docker exec` for the WF23 import.
- Run `Execute` directly.
- Remove WF23 from that container.
- Disable execution persistence.
- Before each call, confirm no PostgreSQL execution rows.

Restart only the n8n container with `docker restart`. Check all other service
containers and their start times. Use internal runner mode on `127.0.0.1:15679`.
Before metadata reads, workflow import, or provider calls, verify that port.

Before the first execution, check that both token expiries are past. Produce a
redacted `VERIFIED` receipt for each execution. Move both expiries into the
future. Limit Outlook to one server-filtered message. On the second
execution, preserve the first future expiry values.

Compare these digests with their baselines:

- workflows.
- execution history.
- credential bindings.
- Data Tables.

Emit a mode-`0600` success or failure receipt. A failure receipt uses
three-state postconditions. `raw_irun_persisted: false` requires a zero-row
readback. `finance_data_table_writes: false` requires an official digest match.
