# finance workflows

This directory contains inactive `SPEC_ONLY` n8n workflow exports and their
contracts. The exports have not yet passed exact-image import or disposable
runtime tests. Do not treat them as executable or production-ready.

## scope

- n8n schedules and coordinates finance workflows.
- Actual remains the posted ledger.
- The cashback service owns its cursor and routing data.
- OneDrive stores evidence.
- OneDrive stores import files.
- Postgres stores cursors and receipts.
- Postgres stores outbox metadata and fenced leases.

## rules

- Every export is inactive and labeled `SPEC_ONLY`.
- `pipeline-registry.json` records import state.
- `pipeline-registry.json` records fixture state.
- `pipeline-registry.json` records disposable state.
- `pipeline-registry.json` records production state.
- Each financial stage is a visible node or sub-workflow.
- The finance package is `n8n-nodes-finance@0.1.0`.
- The package handles PDF workflows.
- The package handles statement workflows.
- The package handles rules and projection workflows.
- The package handles Actual operations.
- Workflows do not use Execute Command or SSH.
- Workflows do not accept caller-selected shell arguments.
- Workflows do not accept caller-selected filesystem paths.
- Credentials never appear in expressions, workflow JSON, or model inputs.
- Outlook is not the accounting cursor.
- Scheduled workflows use a cursor with an overlap recovery window.
- Mail sweeps freeze `run_upper_bound` and exhaust pagination.
- When the window is empty, mail sweeps return one aggregate record.
- Each terminal step owns one cursor commit.
- AI returns proposals only.
- Deterministic staging does not need AI.
- Transaction alerts and statement acquisition use separate sub-workflows.
- Sensitive workflows do not save payloads.

## disposable proof

The separate orchestrator mounts `workflows` read-only. CI must complete these
checks before a workflow leaves `SPEC_ONLY`:

1. Bind Outlook and OneDrive credentials.
2. Seed Data Tables from versioned configuration.
3. Install the reviewed finance custom nodes.
4. Import every export into n8n image 2.36.2 with no unknown nodes.
5. Run the resilience and security fixture tests.
6. Read each terminal receipt from Postgres or Data Tables.
7. Keep schedules and mutation workflows inactive until promotion gates pass.

## operational state

`data-tables.json` v4 assigns each declared Data Table a retention rule and an
idempotency key. It also assigns a concurrency rule and lookup contract. The 15
declared tables are the legacy `SPEC_ONLY` input contract.

The generated [`data-table-migration-matrix.json`](data-table-migration-matrix.json)
targets four tables:

- `finance_ingestion_state`
- `finance_documents`
- `finance_actual_batches`
- `finance_ai_reviews`

The migration tests prove the dispositions and target set. Runtime readback does
not prove live tables.

The Outlook sweep has two phases:

- `ENUMERATE` freezes the window and exhausts pages.
- `ENUMERATE` stores an acquisition receipt and returns one aggregate heartbeat.
- `COMMIT` requires a downstream receipt SHA-256.
- `COMMIT` performs an exact cursor readback.

The cashback workflows commit their own SQLite cursor. They do not use the n8n
cursor commit operation.

Workflow 10 exposes the bounded MCP facade. It writes an `ACCEPTED` request hash
before execution. After execution, it writes a redacted `COMPLETED` or `FAILED`
result hash. It verifies that the terminal receipt exists.

AI proposal output follows this path:

1. The workflow validates the proposal JSON.
2. OneDrive stores the exact proposal artifact.
3. The workflow downloads the artifact and checks its hash.
4. `finance_agent_jobs` records the proposal as `PENDING` review.

## prodex subscription adapter

Workflow 09 creates the bounded subscription handoff. Workflow 21 runs the
single ProDex route.

The checked-in runtime closure contains:

- `n8n-nodes-prodex@0.5.1` in
  `packages/n8n-nodes-finance/community-ai/package.json`.
- The matching integrity entry in `integrations/n8n/community-node-lock.json`.
- ProDex registration in `assert-runtime-registration.cjs`.
- ProDex source hashes in `verify-immutable-extension.cjs`.
- The ProDex hardening contract in `harden-community-ai.cjs`.

The entrypoint script creates links for the finance package and ProDex. It
rejects mutable replacements and API-key environment variables.

The server policy selects `CODEX_SUBSCRIPTION`. Normal work uses
`gpt-5.6-luna` with `max` reasoning. Exception work uses `gpt-5.6-sol` with
`medium` reasoning. Both modes use `CHATGPT_SUBSCRIPTION`.

Callers supply these values:

- policy ID
- transaction IDs
- requested fields
- redacted context

Callers cannot supply these values:
- provider or model
- prompt or command
- path or URL
- credential or write flag

The proposal schema is
`integrations/n8n/contracts/ai-proposal-v1.schema.json`. The handoff schema is
`integrations/n8n/contracts/subscription-agent-handoff-v1.schema.json`.

The adapter remains `SPEC_ONLY` until CI proves each condition:

- exact-image registration
- subscription login
- no-tool behavior
- no-write behavior
- three consecutive schema-valid receipts

The native n8n OpenAI credential is not used. It accepts an API key.
The policy forbids API-key fallback.

## workflow layout

`workflow-folders.json` assigns each workflow to one of six folders. Regular
inactive exports use the `finance` and `inactive` tags. They also use
`setup-required`.

Plain `import:workflow` supports a target project. It cannot create or remap
folders. Exports omit `parentFolderId` for that reason.

The placement reconciliation creates or reuses folder identities. It moves only
the 19 inactive finance workflows. It performs a durable readback.

Each workflow contains native `nodeGroups` and finance sticky notes. Execute
Sub-workflow selectors use n8n's `From list` form with a stable workflow ID.

Manual setup exports live under `setup-workflows/`. The registry and
regular import exclude those files.

## data table bootstrap

Workflow 19 is inactive and manual-only. It creates or reuses each table in
`data-tables.json` with the native Data Table `Table → Create` operation.

The workflow reads `generated/ai-policy-contracts.seed.json`. It upserts rows
into `finance_ai_policy_contracts`. It reads every ACTIVE policy.
It compares the policy identity and profile. It compares hashes.
It compares allowed fields. It compares value domains and state.

The workflow has no custom finance node. It cannot write ledger transactions.
It has no Actual node. It has no cashback node. It has no Outlook node.
It has no OneDrive, HTTP, or Postgres node.

`generate_platform_bootstrap.py` generates the workflow and
`generated/platform-bootstrap-manifest.json` from versioned contracts. When the
table or seed contract drifts, tests fail.

The export has not passed the pinned 2.36.2 image. This manifest and export are
specifications, not runtime evidence.
