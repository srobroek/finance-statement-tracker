# setup workflows

These exports stay outside `integrations/n8n/workflows/`. The regular import
contains 19 workflows. Operators import setup workflows one file at a time. This
runbook covers deployed-stack validation, the mandatory acknowledgment, and
the read-only WF23 proof with protected recovery inputs.

## onedrive setup

`22-onedrive-finance-evidence-root-setup.json` is inactive and manual-only. It
uses the existing `Finance OneDrive` OAuth credential.

The workflow performs these steps:

- Lists the drive root.
- Reuses or creates one exact `Finance Evidence` folder.
- Reads the drive root again.
- Rejects a nested `Finance Evidence/Finance Evidence` folder.

The terminal receipt omits:

- Record IDs.
- Drive metadata.
- URLs.
- Credential values.
- File contents.

Import the workflow into `Global/Shared` for the manual run. Keep it inactive
and unscheduled. After the manual run, remove the setup export.

## microsoft oauth proof

`23-microsoft-oauth-refresh-proof.json` is inactive, manual-only, and
read-only. The proof performs these reads:

- Reads at most one Outlook message with `isDraft=false`.
- Uses a frozen seven-day window.
- Lists the OneDrive root once.

The workflow downloads no provider content. The workflow contains no
provider-write node.

The terminal receipt retains:

- Result counts.
- Time bounds.
- An execution ID.
- Safety flags.
- Verification timestamp.

The receipt omits:

- Message fields.
- File fields.
- Credential values.
- Token values.

## OAuth lifecycle

Keep all workflows inactive during each proof. Run the proof for each row:

| Condition | Required readback | Failure action |
|---|---|---|
| The access token expired and a refresh token remains present | Run bounded Outlook and OneDrive reads. Record result counts and a redacted receipt. | If refresh fails, keep the cursor unchanged. |
| n8n restarted | Repeat the bounded reads after restarting n8n only. | If the credential is unavailable, stop before cursor or evidence writes. |
| Consent revoked | Record provider denial without message or file content. | Mark authentication revoked and request user-present consent. |
| Credential or refresh token missing | Record negative-auth denial before provider access. | Emit a redacted failure receipt and stop acquisition. |

Classify `invalid_grant` as an authentication failure. Classify scope denial as
an authentication failure. Classify missing credentials as an authentication
failure. Classify a missing refresh token as an authentication failure. Do not
retry authentication failures as transient provider errors. Keep each OAuth
claim pending until a current mode-`0600` receipt proves it.

## recovery receipt

Runner reads a recovered Postgres ID from a protected receipt. Set
`FINANCE_N8N_RECOVERY_RECEIPT` to the absolute
path of `prestate.json` from the deployed recovery directory. Include this
contract in the receipt:

```json
{
  "schema_version": 1,
  "purpose": "N8N_RECOVERY_PRESTATE_RECEIPT_V1",
  "postgres": {
    "container_id": "<64 lowercase hexadecimal characters>"
  }
}
```

These checks use the live recovered stack. A failed boundary stops the preflight
before workflow import:

- Recovery receipt missing.
- Container identity mismatch.
- Task-runner endpoint unavailable.
- Database readiness failure.
- Folder parent mismatch.
- After a successful preflight, run the full proof.

Runner checks:

- Compares the recorded container ID with `docker inspect`.
- Checks that the container runs.
- Runs `pg_isready`.
- Verifies that `psql` reports the configured database and role.
- Does not resolve Postgres through Compose.

## required inputs

Read values from the receipt or its mode-`0600` configuration:

- Reject missing values.
- Reject symlinks.
- Reject malformed commit IDs.
- Reject unsafe file modes.

Required inputs:

- `FINANCE_N8N_COMPOSE_PROJECT`: deployed project name.
- `FINANCE_N8N_STACK_DIR`: n8n stack checkout.
- `FINANCE_N8N_COMPOSE_FILE`: `compose.yaml` or its absolute path.
- `FINANCE_N8N_DEPLOYMENT_ENV_FILE`: mode-`0600` runtime environment file.
- `FINANCE_N8N_RECEIPT_DIR`: absolute mode-`0700` output directory.
- `FINANCE_N8N_RECOVERY_RECEIPT`: absolute mode-`0600` Postgres receipt.
- `FINANCE_REPOSITORY_DIR`: clean finance source checkout.
- `FINANCE_REPOSITORY_COMMIT`: exact 40-character finance commit.
- `ORCHESTRATOR_REPOSITORY_COMMIT`: exact 40-character stack commit.
- `N8N_FINANCE_PROJECT_ID`: explicit n8n project identifier.

Source provenance binds this proof to one recovery event. The finance commit
identifies the workflow and runner source. The orchestrator commit identifies
the deployed n8n and task-runners stack. The project ID and service names
identify the live control plane. The receipt identifies the recovered Postgres
container, and the environment file supplies the database role and name. A
successful run keeps these values together.

Service names:

- `FINANCE_N8N_N8N_SERVICE` defaults to `n8n`.
- `FINANCE_N8N_TASK_RUNNERS_SERVICE` defaults to `task-runners`.
- When a deployed service name differs, set the relevant variable.

## run the deployed preflight

Run the commands from the finance checkout on the deployed host. Do not import
or execute WF23 manually.

```sh
export FINANCE_N8N_COMPOSE_PROJECT='<project from recovery configuration>'
export FINANCE_N8N_STACK_DIR='<deployed stack checkout>'
export FINANCE_N8N_COMPOSE_FILE='compose.yaml'
export FINANCE_N8N_DEPLOYMENT_ENV_FILE='<mode-0600 runtime environment file>'
export FINANCE_N8N_RECEIPT_DIR='<absolute mode-0700 receipt directory>'
export FINANCE_N8N_RECOVERY_RECEIPT='<absolute mode-0600 prestate.json>'
export FINANCE_REPOSITORY_DIR='<clean finance checkout>'
export FINANCE_REPOSITORY_COMMIT='<40-character finance commit>'
export ORCHESTRATOR_REPOSITORY_COMMIT='<40-character stack commit>'
export N8N_FINANCE_PROJECT_ID='<deployed n8n project ID>'

FINANCE_MICROSOFT_OAUTH_PROOF_ACK=RUN_TRANSIENT_WF23_ONLY \
FINANCE_MICROSOFT_OAUTH_PROOF_PREFLIGHT=true \
  integrations/n8n/setup-workflows/runner/run-transient-microsoft-oauth-refresh-proof.sh
```

The preflight resolves these containers from the named Compose project:

- One n8n container.
- One task-runners container.

The preflight checks that these boundaries hold:

- n8n health.
- The deployed n8n broker at `5679` and task-runner launcher at `5680`.
- Recovered Postgres identity and readiness.
- The `Global` root to `Shared` child hierarchy.
- The 19-workflow boundary.
- Four canonical Data Tables.
- Redacted Microsoft credential metadata.

The four-table check is a migration-target check. The legacy input contract still
declares 15 `SPEC_ONLY` tables in [`../data-tables.json`](../data-tables.json).
The generated [`../data-table-migration-matrix.json`](../data-table-migration-matrix.json)
and [`tests/test_data_table_migration_matrix.py`](../../../tests/test_data_table_migration_matrix.py)
prove the source dispositions, four target names, and bootstrap exclusion. A
successful preflight does not promote the target to live runtime evidence.

## run the full proof

After the preflight succeeds, run the full proof. Keep the same exports and
change the two operator flags as follows:

```sh
FINANCE_MICROSOFT_OAUTH_PROOF_ACK=RUN_TRANSIENT_WF23_ONLY \
FINANCE_MICROSOFT_OAUTH_PROOF_PREFLIGHT=false \
  integrations/n8n/setup-workflows/runner/run-transient-microsoft-oauth-refresh-proof.sh
```

Runner actions:

- Imports one inactive workflow into `Global/Shared`.
- Executes the reviewed read-only proof through the deployed n8n/task-runners path.
- Restarts only n8n.
- Runs the proof again.
- Removes WF23.
- Disables execution persistence.
- During the initial preflight, the runner checks PostgreSQL for readiness and identity once.

The cleanup gate restores these values:

- 19 workflows.
- Zero active workflows.
- Zero published workflows.
- 19 folder placements.
- 57 tag edges.
- Zero WF23 execution rows.
- The workflow digest.
- The four-table digest.

Runner emits a mode-`0600` success or failure receipt. Failure fields
remain `null` until a zero-row or digest readback proves their postconditions.

## Promotion identity receipt

Keep the mode-`0600` receipt redacted. Include the finance and platform commits.
Include image and registry digests. Include the Compose project and service
names. Include the n8n project ID, workflow IDs, and Data Table digest. Include
the listener origin, Cloudflare connector identity, credential owner and scope
results, authentication result, receipt status, and verification timestamp.
Include `secret_values_recorded=false` and the rollback or recovery receipt
reference. Exclude tokens, bearer material, mailbox content, document content,
and financial plaintext. Missing identity fields keep the promotion pending.
