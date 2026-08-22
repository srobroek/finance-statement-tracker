# Finance Tracker Execution Plan

## 1. Repository and ownership

- Repository: `https://github.com/srobroek/finance-statement-tracker.git`
- WSL clone target: `~/src/finance-statement-tracker`
- Existing Windows checkout, for reference only: `/mnt/c/Users/USER/OneDrive - vxsan.com/Documents/Finance Statement Tracker`
- Deployment checkout: `/opt/stacks/finance-statement-tracker`

This repository owns the complete finance application, including every finance-specific n8n workflow, workflow generator, application configuration, source contract, rule set, Data Table schema and migration, fixture, custom finance node, Actual integration, cashback companion, document/evidence integration, AI contract, test, and finance-facing runbook.

It does not own the base n8n Compose stack, generic container/network/secrets bootstrap, n8n image locking, or generic platform backup and restore. Those belong to `srobroek/n8n`.

## 2. Product boundaries and sources of truth

- Actual Budget owns posted transactions, accounts, payees, categories, budgets, schedules, and ordinary ledger reports.
- The cashback companion SQLite store owns live cashback events, cashback periods, recommendations, alerts, and cashback ingestion cursors.
- Versioned repository configuration owns source contracts, card programmes, rules, AI policy, workflow defaults, and deployable schemas.
- OneDrive `Finance Evidence` owns immutable statements, receipts, bills, warranties, derived artifacts, and their catalogue.
- n8n owns schedules, cursor-safe acquisition, visible ETL, bounded AI handoff, orchestration receipts, and recovery state.
- A provider notification may update a live cashback bucket, but it must never finalize a statement or cashback period without statement evidence and reconciliation.

All production workflows remain inactive, unpublished, and write-disabled until the corresponding disposable acceptance evidence and explicit promotion authorization exist.

## 3. Target application structure

Use one application hierarchy and role-oriented children:

```text
Finance
  Account Reconciliation
  Cashback Sweep
  Shared

Global
  Shared
```

- `Finance / Account Reconciliation`: statement acquisition, evidence archival, parsing, normalization, reconciliation, Actual preparation, verification, recovery, and account-close controllers.
- `Finance / Cashback Sweep`: notification acquisition, cashback classification, live bucket updates, period reconciliation, recommendations, and sweep controllers.
- `Finance / Shared`: finance-only source-contract resolution, evidence helpers, Actual writer/recovery helpers, finance error policy, and finance AI policy resolution.
- `Global / Shared`: genuinely application-neutral utilities only. A workflow must not be placed here if it knows finance table names, finance folders, Actual, cashback rules, finance accounts, or finance source codes.

Each workflow must be placed exactly once. Setup-only workflows are transient, inactive, unpublished, and removed after their redacted acceptance receipt is captured.

## 4. Parameter architecture

### 4.1 Global parameters

Store deployable non-secret global and finance-wide parameters in versioned, schema-validated repository files. Generate immutable resolver subworkflows from those files:

- `Global / Shared / Resolve Global Runtime Contract` for application-neutral limits and protocol versions.
- `Finance / Shared / Resolve Finance Source Contract` for server-owned source, folder, account, evidence, schedule, and parser selections.
- `Finance / Shared / Resolve Finance AI Policy` for server-owned provider, model, prompt-contract, output-schema, and protected-field policy.

Callers pass only an allowlisted key such as `source_code` or `policy_id`. They must never supply a credential, provider, model, prompt, account, folder, path, or protected policy field.

Secrets remain in the platform secret store and n8n credentials. Do not put secrets, credential IDs, tokens, account secrets, or raw provider responses in Git.

### 4.2 Per-workflow parameters

Use one first-stage Edit Fields (Set) node named `Workflow Parameters` for local, non-secret constants. It must:

- emit a fixed typed object with no input passthrough;
- contain only values genuinely local to that workflow;
- exclude credentials and shared policy;
- feed every later reference rather than duplicating literals across nodes;
- be validated by tests against an explicit allowlist.

If two or more workflows need the same parameter or logic, move it to a generated resolver or a shared subworkflow. Do not duplicate it through multiple local Set nodes.

## 5. Workflow composition rules

- Prefer a subworkflow when logic is reused, independently testable, security-sensitive, or forms a durable transaction boundary.
- Prefer a Canvas Group for a single-workflow stage whose nodes are not reusable.
- Give every group a concise role name and description, and keep its nodes spatially contained.
- Remove stage-label sticky notes after equivalent group names/descriptions exist.
- Retain sticky notes only for design rationale, warnings, non-obvious invariants, or manual review instructions that a group description cannot express.
- Keep at most one short overview note when it materially improves navigation.
- Perform visual grouping and Tidy only after functional MVP and recovery acceptance are complete.

Before creating a group, search the corpus for equivalent node sequences. Extract real duplication into a list-mode subworkflow and prove that its callers preserve input/output contracts.

## 6. Critical execution plan

### P0 — Establish a safe, truthful baseline

- Reconcile the current branch, generated artifacts, backlog, and tests without discarding unrelated work.
- Push all reviewed source changes to this repository; do not rely on an uncommitted Windows checkout as authority.
- Record exact finance and platform commits in every disposable receipt.
- Keep provider, Actual, cashback, OneDrive, and activation writes disabled by default.
- Finish the exact WF23 transient-workflow cleanup using the reviewed execution-free path:
  1. execute the exact PostgreSQL rehearsal transaction and end with `ROLLBACK`;
  2. prove the live boundary is unchanged after rehearsal;
  3. independently review the redacted rehearsal receipt;
  4. run the separately acknowledged commit path;
  5. prove the retained corpus is restored to 19 inactive/unpublished workflows, 19 placements, and 57 inactive-export tag edges with no setup workflow/history residue.
- Do not continue provider experiments while the transient runtime boundary is dirty.

Acceptance:

- repository and generated files are internally consistent;
- full test suite passes;
- runtime baseline and service health are read back without secrets;
- no production workflow is active or published.

### P1 — Complete provider prerequisites

- Prove Outlook and OneDrive credential refresh on bounded read-only calls:
  1. capture redacted pre-call expiry metadata;
  2. perform one minimal Outlook filtered read and one OneDrive root children read;
  3. prove future expiry after the first calls;
  4. restart only n8n;
  5. repeat both reads;
  6. prove future, non-regressed expiry and successful second reads;
  7. prove workflow, finance-data, and cursor boundaries unchanged.
- Create or reuse the root-level OneDrive folder `Finance Evidence` through a transient setup workflow.
- Read back exactly one matching root folder and prove no nested duplicate.
- Persist only a redacted setup receipt, then remove the transient workflow and its history.

### P2 — Prove one statement-ingestion MVP end to end

Use one approved real statement or an approved immutable fixture. The run must:

1. freeze a bounded acquisition window;
2. exercise zero-item, one-item, and 101-plus pagination behavior in disposable tests;
3. filter by server-owned source contract, sender, date, and attachment policy;
4. deduplicate by stable source identity and content hash;
5. archive the original to OneDrive before parsing;
6. download/read back the archive and verify its hash;
7. parse and normalize without issuer-specific executable hard-coding;
8. apply repository rules, history matching, and only then scoped AI for unresolved fields;
9. reconcile counts, sums, currency, period, and source-document identity;
10. create a prepared Actual batch without writing the production ledger;
11. persist a durable downstream receipt;
12. advance the acquisition cursor only after that receipt is verified;
13. replay the identical input and prove no duplicate archive, document, batch, or cursor movement.

MVP evidence must identify the immutable source, archive receipt, normalized result, reconciliation result, prepared batch, cursor before/after, and replay no-op without storing document contents in Git.

### P3 — Prove one cashback-ingestion MVP end to end

Use one approved RAKBANK transaction-notification example and prove:

1. the exact cursor-minus-overlap window and frozen upper bound;
2. server-side filtering and complete pagination;
3. activation, OTP, notification-only, and malformed messages are excluded;
4. exactly one valid event is normalized and inserted in the cashback companion;
5. the live bucket, pace, headroom, warning, and recommendation are recalculated deterministically;
6. `Cashback Finalized` remains false until statement reconciliation;
7. the downstream receipt is durable before the sole cursor-commit step;
8. replay produces no duplicate event and no second cursor change;
9. companion and n8n readbacks agree on accepted, rejected, scanned, and cursor counts.

Do not treat a successful unit test or workflow import as ingestion proof. Require the redacted runtime receipt and authoritative companion readback.

### P4 — Make the Actual writer and recovery path correct

- Keep a single fenced Actual-writer subworkflow and require explicit `ALLOW_ACTUAL_WRITES=true`.
- Fix the current verification design before any live write:
  - independently calculate expected and observed transaction hashes, counts, and minor-unit sums;
  - remove comparison of current account balance with a historical statement balance unless an as-of-period, currency-aware, sign-defined API proof exists;
  - remove undefined account/balance fields and replay-only dereferences;
  - bind account, source, period, Actual file, config hash, and delta hash to server-owned contracts;
  - generate an immutable `actual-verification-v2` artifact;
  - upload, download, and hash-verify that artifact before marking a batch verified;
  - remain recoverable at `ACTUAL_OBSERVED` if OneDrive fails after Actual mutates.
- Prove kill/restart recovery at PREPARED, ACTUAL_OBSERVED, VERIFIED, and COMMITTED boundaries.
- Prove no duplicate Actual row on every replay path.
- Only after disposable double replay and post-replay audit may a reviewed production batch be submitted.

### P5 — Simplify application state from 15 Data Tables to 4

Target only these human-visible n8n Data Tables:

1. `finance_ingestion_state` — current successful acquisition/cursor boundary per non-cashback source.
2. `finance_documents` — archive proof and processing state as separate axes, with source/OneDrive/Actual linkage but no message body, extracted text, binary, or error stack.
3. `finance_actual_batches` — prepared/observed/verified/committed batch state plus immutable delta and verification-artifact pointers.
4. `finance_ai_reviews` — proposal artifact identity and human review state only, created after proposal upload and hash verification.

Remove or replace:

- `source_contracts`, `config_versions`, and `ai_policy_contracts` with generated repository-owned resolver workflows;
- `pipeline_runs` and `execution_failures` with n8n execution history and an observability service when needed;
- `mcp_requests` with n8n execution evidence and edge/access logging;
- `provider_circuits` unless a real shared circuit-breaker requirement is demonstrated;
- historical acquisition receipts by folding the last committed boundary into ingestion state;
- separate archive/document tables by merging them without conflating archive and processing states;
- separate Actual verification/reconciliation tables by retaining immutable artifact pointers on the batch and keeping cashback close authority in the companion;
- agent execution telemetry while retaining only durable proposal review state.

Migration gates:

1. read back all current table IDs, exact ordered schemas, counts, and digest;
2. inventory every current column and every node read/write/filter reference;
3. assign exactly one keep/transform/remove disposition with type, constraints, owner, retention, privacy class, producer, consumer, and rationale;
4. create the four target tables side by side;
5. transform the single current cursor exactly and prove the 7 policy plus 9 config rows are byte-equivalent to the generated immutable bundle;
6. update workflows and prove zero old-table references;
7. run the migration twice and prove the second run changes no schema, row, or timestamp;
8. rehearse a finance-scoped reverse migration;
9. delete old tables only after exact pre/post digests and rollback evidence pass.

Use Langfuse only for optional sanitized AI traces. It must not own idempotency, cursor, review, evidence, Actual recovery, or cashback-close state.

### P6 — Complete the remaining finance flows

- Add the remaining supported statements and cashback programmes through source contracts, not issuer-specific branching in shared executable code.
- Complete browser-capture adapters using the versioned `browser-capture-schema-v1`; browser capture never writes directly to Actual.
- Complete inline-email-to-PDF and non-PDF evidence behavior.
- Complete deterministic account/period close and cashback companion acknowledgement.
- Add Codex/Claude proposal paths only after deterministic processing passes; providers may propose but never mutate protected finance fields.
- Require proposal artifact upload/download/hash verification before human review.

### P7 — Disposable system acceptance

On an isolated platform stack:

- import all finance workflows inactive and unpublished;
- bind credentials without recording their IDs or values;
- place workflows under the target hierarchy;
- initialize the four target Data Tables;
- run one statement ingestion and one cashback ingestion end to end;
- run each a second time and prove exact no-op behavior;
- inject downstream, provider, archive, parser, Actual, and restart failures at every durable boundary;
- restore from a finance-scoped backup and prove identical source-of-truth digests;
- prove no write reached production Actual, cashback, OneDrive, or provider state unless the specific disposable fixture authorizes it.

### P8 — Reviewed production promotion

- Create and validate a fresh backup first.
- Verify exact finance/platform commits, image ID, schemas, credentials, folders, workflow count, and inactive/unpublished status.
- Import only the reviewed corpus and read it back independently.
- Keep schedules, webhooks, provider writes, Actual writes, and cashback writes disabled.
- Activate one workflow at a time only after its own live acceptance and explicit authorization.
- Record a redacted immutable promotion receipt and recovery boundary.

### P9 — Visual organization and maintainability

Only after functional MVP and recovery acceptance:

- implement the `Finance` and `Global` hierarchy;
- convert stage-label sticky notes into Canvas Groups with role names/descriptions;
- extract duplicated groups into list-mode subworkflows;
- run Tidy and inspect every canvas for readable left-to-right flow;
- retain design notes only where they add reasoning or a warning;
- verify groups never overlap nodes and subworkflow references resolve by human-readable list selection.

## 7. Required verification

Run at minimum:

```bash
python -m unittest discover -s tests -v
python -m unittest tests.test_n8n_workflows -v
python -m unittest tests.test_project_backlog -v
python -m unittest tests.test_cashback_events tests.test_cashback_server -v
npm test --prefix packages/n8n-nodes-finance
npm test --prefix services/codex-agent-runner
python scripts/generate-project-backlog.py --check
git diff --check
```

Also run every checked-in workflow/config/table generator in check mode and validate every JSON/schema artifact.

Runtime acceptance must use authoritative readback. Logs or process exit zero alone are not sufficient.

## 8. MVP definition of done

The MVP is complete only when all of the following are evidenced:

- one statement is acquired, archived, parsed, normalized, reconciled, and prepared for Actual;
- one valid cashback notification is ingested into the companion and updates the live calculation;
- both inputs replay with no duplicates and no incorrect cursor movement;
- cursor advancement occurs only after durable downstream verification;
- no statement or cashback period is finalized without statement reconciliation;
- Actual recovery is proven without duplicate ledger rows;
- all production workflows remain inactive/unpublished until explicitly approved;
- backups and a finance-scoped rollback are tested;
- the runtime receipt, source-of-truth readbacks, and repository commits agree.

Do not mark a task complete from source code, tests, imports, or screenshots alone when the acceptance criterion requires a live or disposable result.

