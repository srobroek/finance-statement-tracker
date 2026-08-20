# Explicit n8n setup workflows

These exports are deliberately outside `integrations/n8n/workflows/`. They are
not part of the regular 21-workflow import or activation set and must be
imported one file at a time only for a reviewed setup action.

`22-onedrive-finance-evidence-root-setup.json` is manual-only and inactive. It
uses the bound `Finance OneDrive` OAuth credential to list the drive root,
reuse the exact top-level `Finance Evidence` folder when present, or create
that single folder at the drive root when absent. It then reads the root back,
checks that there is exactly one exact match, inspects its children, and fails
if `Finance Evidence/Finance Evidence` exists.

The final execution item is a redacted receipt: it confirms the exact root and
whether the folder was created or reused, but omits the OneDrive item ID, drive
metadata, URLs, credential values, and file contents. The workflow must remain
inactive and unscheduled. Import it into `90 Platform & Admin`, bind only the
existing `Finance OneDrive` credential, run it once manually, retain the
redacted output, and remove the setup export from n8n if it is no longer
needed.

`23-microsoft-oauth-refresh-proof.json` is a separate manual-only, inactive,
read-only proof for the two Microsoft OAuth credentials. Its Outlook operation
uses a frozen seven-day window, the server-side `isDraft eq false` filter, and
a maximum of one result. The Graph projection requests only the message `id`,
which is discarded before the next node. Its OneDrive operation lists the drive
root once. It does not download content and contains no provider-write node.
The final item retains only result counts, the bounded time window, execution
ID, safety booleans, and verification timestamp; it discards message fields,
file fields, credential values, and token values.

For the restart proof, use the guarded
`runner/run-transient-microsoft-oauth-refresh-proof.sh` contract. Do not import
or execute WF23 manually. The runner resolves the existing project-owner
credentials, compares their identities before and after only in memory without
printing or recording their IDs, creates a bound copy under `/dev/shm`, and
performs this exact reviewed sequence:

1. Before any workflow import, OAuth metadata read, or provider call, run a
   no-workflow/no-provider/no-database-initialization transport probe. It loads
   the extensionless n8n 2.36.2 config entry point, resolves the official
   `Execute` instance, and proves that its instance-owned output hook works.
2. Capture the workflow and Finance Data Table baselines, then a metadata-only
   readback for both bound credentials containing only
   credential type, `updatedAt`, token expiry time, and presence booleans. The
   readback deliberately omits credential IDs. Never print or persist encrypted
   credential data, access tokens, refresh tokens, client secrets, or response
   bodies.
3. Import the bound workflow inactive, run it through a directly initialized
   n8n `Execute` instance, and retain only its redacted terminal receipt.
4. Repeat the metadata-only readback and require both expired token expiries to
   have advanced to future, unexpired values.
5. Restart only the n8n service and wait for its health check to pass.
6. Run the same inactive workflow again and repeat the metadata-only readback.
7. Remove WF23 and accept the proof only when both
   executions are `VERIFIED`, both provider reads succeeded, each Outlook count
   is at most one, and the credential types remain stable. Before the first
   execution, both access-token expiries must already be in the past. The first
   execution must move each expiry strictly forward to a future, unexpired
   value. After restart, the second execution must succeed with each expiry
   still future and no earlier than after the first execution. `updatedAt` is
   retained only as supplemental non-regression metadata and never counts as
   refresh proof by itself.

The runner requires explicit `FINANCE_MICROSOFT_OAUTH_PROOF_ACK`, exact finance
and orchestrator commits, and the exact retained project. It starts only from
the reviewed `21 workflows / 0 active / 0 published` state, imports exactly one
inactive workflow, places it in `90 Platform & Admin`, and returns to the exact
21-workflow baseline. Raw n8n `IRun` objects and provider responses exist only
inside the execution process long enough to validate the terminal node; only
the redacted terminal result leaves that process. Execution persistence is
disabled and independently checked in PostgreSQL after both calls.

The direct WF23 process has its own 120-second, re-armed watchdog for the fixed
stages `CONFIG_LOAD`, `MODULE_LOAD`, `COMMAND_INIT`, `COMMAND_RUN`,
`RAW_CAPTURE`, and `FINALIZE`. A timeout emits exactly one fixed-schema line
whose only diagnostic is the allowlisted `WF23_TIMEOUT_<STAGE>` code, invokes
only sanitized command finalization, and exits. Provider responses, exception
text, raw execution data, and secrets are never copied into that line. The host
runner rejects every other failure payload and retains an allowlisted timeout
code only in the redacted failure receipt before performing the same exact
cleanup and digest readback.

The runner restarts only the n8n container and verifies that every other
service container and start time remains unchanged. Exact baseline and Finance
Data Table digests must match after cleanup. The final mode-600 receipt contains
the two redacted workflow results and three metadata-only snapshots (before,
after the first call, and after the post-restart call). Failure cleanup removes
only an exact inactive WF23 instance and emits a redacted failure receipt; any
cleanup or digest mismatch fails closed for review.

A failure receipt uses three-state postconditions. It reports
`raw_irun_persisted: false` only after a zero-row execution readback, and
`finance_data_table_writes: false` only after a fresh official
`DataTableService` digest matches the baseline. Otherwise those fields are
`null`, not optimistic assertions. `FAILED_CLEAN_BOUNDARY_RESTORED` is allowed
only when workflow restoration, zero execution rows, and the official Data
Table digest recheck all succeeded.
