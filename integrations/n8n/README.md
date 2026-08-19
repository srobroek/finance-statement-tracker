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

1. bind the Outlook and OneDrive credentials;
2. seed the source/rule/cursor Data Tables from versioned configuration;
3. install the reviewed finance custom nodes;
4. import every export into the exact n8n 2.36.1 image with no unknown nodes;
5. execute the resilience and security fixture matrix, including restarts;
6. read back every terminal receipt directly from Postgres/Data Tables;
7. leave all schedules and mutation workflows inactive until promotion gates.

## Codex proposal handoff

Workflow 09 uses only the bounded `CODEX_AGENT_HANDOFF` contract. A future narrow
runner must use cached ChatGPT subscription login with
`forced_login_method=chatgpt`; API-key fallback is forbidden. Normal policies
map server-side to `gpt-5.6-luna`/`max`; exception policies map to
`gpt-5.6-sol`/`xhigh`. Callers cannot select a model, prompt, command, path, URL,
credential, or write flag. Requests are redacted/idempotent and output is
proposal-only under checked-in schemas. The workflow stays blocked until the
runner exact image and three consecutive receipts are verified.

Callers supply only a policy ID, unresolved transaction IDs, requested fields,
and redacted context. `finance_ai_policy_contracts` owns the single ACTIVE
policy version, profile, exact hashes, target fields, and resolved value
domains. `compile_ai_policy_contracts.py` derives its seed from checked-in
policies, Actual categories, properties, cashback programmes, and the output
schema; caller-supplied profiles, hashes, and domains are rejected.

The native n8n OpenAI credential is not used: the released node supports an API
key, while the proposed ChatGPT account OAuth credential is not in the pinned
n8n release. Execute Command is also excluded because it is an arbitrary-shell
boundary and is disabled by default in current n8n. A fixed HTTP Request to the
narrow runner is therefore the only specified subscription-auth handoff. Each
unresolved item carries bounded configured value domains, and both runner and
workflow must reject invented category, property, channel, tag, or reward-bucket
values. The request hash includes those domains.

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
pinned 2.36.1 image, so `EXACT_IMAGE_IMPORT_REQUIRED` and
`DISPOSABLE_BOOTSTRAP_RUNTIME_PROOF_REQUIRED` remain activation blockers. The
generated manifest and export are specifications, not runtime evidence.
