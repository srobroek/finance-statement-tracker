# Finance n8n workflows

This directory contains sanitized **SPEC_ONLY** n8n workflow exports and their
contracts. They have not yet passed exact-image import or disposable execution
tests and must not be described as executable or production-ready. n8n is the
planned scheduler/orchestrator; Actual remains the authoritative ledger, the
cashback app owns live routing state and its live-source cursor, OneDrive owns
immutable evidence and canonical import artifacts, and Postgres owns n8n plus
operational cursors, receipts, outbox metadata, and fenced leases.

## Design rules

- Every workflow is inactive and labelled `SPEC_ONLY`.
- `pipeline-registry.json` records that import, fixture, disposable, and
  production validation are all false.
- Every financial stage is a visible node or sub-workflow.
- Prefer official nodes. The frozen custom contract is
  `n8n-nodes-finance@0.1.0`: fixed PDF, statement, rules/projection, and Actual
  operations only.
- Never use Execute Command, SSH, arbitrary filesystem paths, caller-selected
  shell arguments, or embedded credentials.
- The Outlook trigger is not the accounting cursor. Every live/monthly workflow
  includes a scheduled cursor-minus-overlap recovery path.
- Mail sweeps freeze one `run_upper_bound`, exhaust pagination, apply the exact
  half-open local window, and return one aggregate item even when empty.
- No cursor advances until archive/downstream state is durable and read back;
  one terminal step owns the cursor commit.
- AI returns proposals only and is never required for deterministic staging.
- Transaction-alert sweeps and statement/document acquisition are separate
  sub-workflows. Live mail never traverses attachment or PDF processing.
- All companion calls use an n8n HTTP Header Auth credential. Tokens are never
  embedded in expressions, workflow JSON, or model-visible tool inputs.
- Actual writes use a OneDrive-backed canonical delta, Postgres outbox states
  `PREPARED → ACTUAL_OBSERVED → VERIFIED → COMMITTED`, a fixed Postgres
  compare-and-swap lease function, and the reviewed Actual custom node. There
  is no ingestion bridge, SSH hop, or generic command runner.
- Sensitive document/mail workflows save neither successful nor failed payloads
  and attach the redacted durable error workflow.

## Required disposable proof before import claims

The separate orchestrator is intended to mount `workflows` read-only. Before any
workflow status can move beyond `SPEC_ONLY`, CI/disposable validation must:

## Durable operational state contract

`data-tables.json` v4 assigns every declared Data Table an explicit retention,
logical idempotency key, concurrency rule, and index/lookup contract. The 21
workflows reference all 15 tables through connected executable nodes, including
the single fenced Actual writer and the subscription-agent adapter. Actual
remains the posted ledger, the cashback service remains authoritative for its
live cursor/routing state, and OneDrive remains the immutable binary/artifact
store; Data Tables contain only operational receipts, pointers, hashes, state,
and bounded proposals.

The Outlook sweep is two phase. `ENUMERATE` freezes and fully exhausts the
window, persists an acquisition receipt, and returns exactly one aggregate
heartbeat. `COMMIT` requires a downstream receipt SHA-256 and performs a
`source_code + cursor_version` compare/update followed by exact readback. The
cashback workflows continue to commit their own SQLite cursor and do not use
the n8n cursor commit operation.

The bounded MCP facade dispatches only through workflow 10, which writes an
`ACCEPTED` request hash before execution and a redacted `COMPLETED` or `FAILED`
result hash afterward, then verifies the terminal receipt. AI proposals are
proposal-only: the exact validated JSON is archived to OneDrive, downloaded,
hash-verified, and recorded as `PENDING` review in `finance_agent_jobs`.

1. bind the Outlook and OneDrive credentials;
2. seed the source/rule/cursor Data Tables from versioned configuration;
3. install the reviewed finance custom nodes;
4. import every export into the exact n8n 2.36.2 image with no unknown nodes;
5. execute the resilience and security fixture matrix, including restarts;
6. read back every terminal receipt directly from Postgres/Data Tables;
7. leave all schedules and mutation workflows inactive until promotion gates.

## Subscription-agent proposal handoff

Workflow 09 uses only the bounded ProDex handoff contract and calls workflow 21.
The server-owned policy selects `CODEX_SUBSCRIPTION`; the caller cannot select
the provider. Normal Codex policies map to `gpt-5.6-luna`/`xhigh`; exception
policies map to `gpt-5.6-sol`/`xhigh`. Callers cannot select a model, prompt,
command, path, URL, credential, or write flag. Requests are redacted and
idempotent, and output is proposal-only under checked-in schemas.
`community-node-lock.json` pins `n8n-nodes-prodex@0.5.1` by integrity. It remains
blocked until exact-image registration, subscription login, no-tool/no-write
behavior, and three consecutive schema-valid receipts are verified.

Callers supply only a policy ID, unresolved transaction IDs, requested fields,
and redacted context. `finance_ai_policy_contracts` owns the single ACTIVE
policy version, profile, exact hashes, target fields, and resolved value
domains. `compile_ai_policy_contracts.py` derives its seed from checked-in
policies, Actual categories, properties, cashback programmes, and the output
schema; caller-supplied profiles, hashes, and domains are rejected.

The native n8n OpenAI credential is not used because it is API-key based. Execute
Command is excluded, and API-key fallback is forbidden. The two version-locked subscription community nodes exist
only inside workflow 21; changing provider implementation does not change the
proposal schema or workflow 09. Each
unresolved item carries bounded configured value domains, and both runner and
workflow must reject invented category, property, channel, tag, or reward-bucket
values. The request hash includes those domains.

## Workflow organization

`workflow-folders.json` assigns every workflow to one of eight numbered finance
folders and applies the exact tags `finance`, `setup-required`, and `inactive`.
Plain `import:workflow` supports a target project but cannot create/remap folders,
so JSON exports deliberately omit `parentFolderId`. After inactive import, the
reviewed placement reconciliation creates/reuses the folder identities and moves
only the 21 inactive finance workflows, then performs direct durable readback.
Each workflow also contains native `nodeGroups` plus finance-specific sticky
notes. Execute Sub-workflow selectors use n8n's `From list` representation with
the stable workflow ID and cached readable name.

Manual provider setup exports live under `setup-workflows/` and are deliberately
excluded from the regular 21-workflow registry and import. The OneDrive root
setup export is an explicit single-file action for `90 Platform & Admin`: it
creates the top-level `Finance Evidence` folder only when absent, reads it back,
rejects nested same-name duplication, emits a redacted receipt, and must remain
inactive and unscheduled.

Instance-level n8n MCP is disabled. Workflow 15 specifies a dedicated MCP Server
Trigger façade with exactly three fixed operation codes. It accepts no arbitrary
mailbox, sender, subject, URL, path, provider, credential, Actual ID, command, or
commit flag and remains `SPEC_ONLY` until real-client negative tests pass.

## Platform Data Table bootstrap

Workflow 19 is an inactive, manual-only platform bootstrap. It creates or reuses
every table declared in `data-tables.json` using the native Data Table `Table →
Create` operation, with `createIfNotExists=true`, then upserts the generated
`ai-policy-contracts.seed.json` rows into `finance_ai_policy_contracts`. It reads
back every ACTIVE policy and compares the policy/version identity, profile,
hashes, allowed fields, domains, and state exactly before returning one in-memory
verification receipt.

The workflow contains no Actual, cashback, Outlook, OneDrive, HTTP, Postgres, or
custom finance node, and cannot write ledger transactions. Its only permitted
mutations are n8n Data Table schema creation/reuse and the checked-in AI policy
configuration seed. `generate_platform_bootstrap.py` generates both the workflow
and `generated/platform-bootstrap-manifest.json` from the two versioned source
contracts so table/seed drift fails tests.

The native parameter shape is grounded in n8n's official Data Table create-node
source and documentation. It has not been imported into or executed against the
pinned 2.36.2 image, so `EXACT_IMAGE_IMPORT_REQUIRED` and
`DISPOSABLE_BOOTSTRAP_RUNTIME_PROOF_REQUIRED` remain activation blockers. The
generated manifest and export are specifications, not runtime evidence.
