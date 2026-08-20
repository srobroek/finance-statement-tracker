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

n8n 2.36.2's `Execute` command declares `needsTaskRunner = true`, and
`BaseCommand.init()` therefore starts a task broker before `Execute.run()`.
The retained n8n service already owns its external-runner broker on
`0.0.0.0:5679`. A second CLI process using internal-runner mode on the inherited
port reaches `EADDRINUSE`; n8n calls `process.exit(1)` from the broker error
handler while its `listen()` promise remains unresolved. Because the guarded
WF23 process intercepts early exits, that condition previously appeared as
`WF23_TIMEOUT_COMMAND_INIT`. The runner now reserves `127.0.0.1:15679` only for
the transient internal runner, verifies that port is bindable before any
metadata read, workflow import, or provider call, and requires the direct shim
to reject every other runner mode/address/port boundary. The workflow's Code
nodes still execute in n8n's official isolated task runner; no Code-node
execution is moved into the main process.

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

If the pinned WF23 incident leaves execution `15` in the exact soft-deleted
orphan state, do not relax the normal runner's zero-row cleanup guard. Use the
separate, explicit-ACK `runner/run-remediate-stranded-wf23.sh` contract. It
accepts only the incident boundary (`22/0/0`, 22 placements, 66 tag edges), the
exact inactive and unpublished WF23 source contract in `90 Platform & Admin`,
one `cli` execution with the observed combination `running`, unfinished, no
stop/wait time, and a non-null soft-delete time, one execution-data row, one
matching history row, the exact owner share and three tags, and no other
workflow or execution references. It calls this state
`ORPHANED_SOFT_DELETED_EXECUTION`; it never describes it as terminal or
completed.

The workflow and its matching history row are compared to complete canonical
projections derived from the pinned source file. Only the two runtime
credential IDs are normalized back to `BIND_OUTLOOK` and `BIND_ONEDRIVE`, and
the binder's `configured` / `action_required` annotations are removed. The
comparison therefore covers the exact ordered seven-node set, every node
parameter and code body, all connections, full settings, full metadata,
`pinData`, node groups, workflow name/ID, `active: false`,
null description/static/source-lineage state, zero trigger count,
`isArchived: false`, and the history name/description/autosave state. Any extra
node, connection, setting, metadata field, or history drift fails before the
first delete. The history projection follows n8n 2.36.2 import semantics for a
source without `versionMetadata`: null history name/description,
`authors: import`, `autosaved: false`, the exact workflow ID, and the exact
generated version ID shared with `workflow_entity`. Before binder annotations
are normalized, both expected provider bindings must have exactly
`configured: true` and `action_required: false`, and neither may retain a
`credential_id`. Project tag checks count every workflow-tag edge, not merely the
three expected names, and require every project workflow to have exactly
`finance,inactive,setup-required`; an additional tag therefore fails both the
edge total and per-workflow set check.

Host remediation has two separately acknowledged modes that use this one SQL
transaction body. `REHEARSAL` executes the production locks, exact preflight,
foreign-key scans, transaction-local backups, deletes, and post-delete
assertions, but its only reachable final command is `ROLLBACK`. The shell then
independently re-proves the unchanged 22/0/0 boundary, exact orphan rows,
workflow/credential/Data Table digests, container health, and absence of a
transient runner before writing a mode-600 redacted rehearsal receipt. `COMMIT`
refuses to enter the transaction unless that receipt is at most 15 minutes old
and matches the exact finance/orchestrator commits, SQL/source hashes, and
current live pre-state digests. The SQL repeats every exact proof under locks;
the rehearsal receipt never substitutes for the transaction preflight.
Before either transaction the runner pauses the retained n8n container, proves
the paused state and absence of transient runners, and rechecks the complete
boundary, orphan signature, and workflow/credential digests. An EXIT trap
resumes n8n on ordinary failure paths. The psql finalizer defaults a missing
authorization variable to `off`, derives its decision from literal equality
with `on`, and reaches `COMMIT` only for that exact positive value. Real psql
16.14 fixtures cover absent, malformed, `off`, and `on` inputs; only `on`
persists the probe row.

If n8n pruning has already hard-deleted incident execution `15` and its
`execution_data` row, the orphan-state contract above is intentionally
inapplicable. Use the separate
`runner/run-remediate-execution-free-wf23.sh` contract. It requires the same
complete canonical workflow/history, project, folder, owner, tag, credential,
dependency, corpus, and Data Table proofs, but accepts only zero WF23
executions of every status and zero incident execution-data rows. Its SQL
expects `execution_entity` to contribute zero workflow references, copies no
execution data, and deletes only the exact history, three tag edges, owner
share, and workflow. A reappearing execution or incident data row fails before
the first delete.

This execution-free path retains the two-step rollback rehearsal and recent
receipt binding. Both modes cleanly stop the sole retained n8n writer,
revalidate the zero-execution state immediately before the same serializable
SQL body, and hold workflow/execution/history/share/tag tables through its
exact checks. The container stop/start, host SQL invocation, PostgreSQL lock
wait, and PostgreSQL statement all have independent bounds; stopping releases
in-flight database connections instead of freezing their locks.
Only the exact positive `commit_authorized=on` branch can commit. The commit
readback must restore `21/0/0`, 21 placements, 63 tag edges, and zero WF23
workflow/history/execution rows while preserving the full retained workflow
and history surface, credential corpus, and official Finance Data Table
digest. The older stranded
runner remains pinned to `ORPHANED_SOFT_DELETED_EXECUTION` and must not be used
after pruning changes that signature.

Because n8n's `ActiveExecutions` registry is process-local and WF23 ran in a
short-lived `docker exec ... node -` process, the checked-in process proof
requires that exact stdin Node process to be absent without printing any
command line. The wrapper also requires zero matching transient `n8n-run`
containers before it reads the database. It proves both credential owner
bindings without printing their IDs and takes retained-workflow,
credential-corpus, and official Finance Data Table digests before mutation.

The remediation deliberately uses one narrowly scoped PostgreSQL transaction
instead of two independent n8n service calls. n8n 2.36.2's official execution
and workflow deletion paths are separate operations, so they cannot provide a
single rollback boundary for the orphan execution, import history, and
workflow rows. The transaction makes temporary copies of only the exact rows,
checks every foreign-key reference before the first delete, deletes exact row
counts, and verifies absence before commit. Those rollback copies never leave
PostgreSQL and disappear at commit; raw execution/provider data is not copied
to the host or receipt. After commit, the wrapper re-proves the exact
`21/0/0`, 21-placement, 63-tag boundary, zero WF23 execution/history rows,
unchanged retained-workflow and credential digests, and an unchanged official
Data Table digest. It never initializes or executes WF23, calls a provider,
deletes a credential, or writes finance data.

The retained soft-deleted `running` row does not mean the `none` retention
settings failed. In n8n 2.36.2, a successful non-manual execution that must not
be retained calls `ExecutionPersistence.deleteInFlightExecution()`. When
pruning is enabled, that method backdates `deletedAt` so the next pruning pass
can hard-delete the row; it intentionally does not write the final execution
status first. The runner's unqualified zero-row query therefore mistook n8n's
expected deferred-hard-delete state for failed retention. A workflow's `none`
settings and process environment still must not be treated as a zero-row proof
by themselves. Separately, n8n 2.36.2 defines `ITaskData.executionStatus` as
optional. The redacted shim
previously required that optional field to be present and equal to `success`,
which could convert a successful terminal item without it into
`WF23_TERMINAL_RUN_INVALID`. Because only the outer
`WF23_REDACTED_OUTPUT_REJECTED` diagnostic was retained, that is a static-code
inference rather than proven live cause. The shim now accepts an absent
optional status, while still rejecting an explicit non-success status or
terminal error and requiring the overall `IRun.status` to be `success`.
