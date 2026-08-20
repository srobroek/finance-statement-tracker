# n8n Data Table minimization plan

Date: 2026-08-20
Status: design and audit candidate only; no runtime migration or final approval
Backlog requirement: `N8N-011`
Machine inventory: `n8n-data-table-column-disposition-2026-08-20.json`
Machine schema: `n8n-data-table-column-disposition.schema.json`
Deterministic generator: `../../scripts/generate-n8n-data-table-column-disposition.py`

## Decision boundary

The deployed 15-table contract proves current deployment, not necessity. Data
Tables may hold only durable finance-domain state. They must not become a
second ledger, generic observability database, configuration registry, provider
circuit store, protected-route access log, or tracing backend.

Observability has fixed owners:

- n8n owns generic execution, retry and failure records;
- Cloudflare owns protected-route access logs;
- Langfuse is optional for separately approved, sanitized agent traces only and
  is never authoritative finance state.

There is no generic `finance_operation_receipts` replacement. The reviewed
design direction is 15 to exactly four n8n-visible domain tables:

- `finance_ingestion_state` — 9 columns;
- `finance_documents` — 20 columns, including distinct `archive_state` and
  `processing_state`;
- `finance_actual_batches` — 18 columns;
- `finance_ai_reviews` — 9 columns and review-only state.

Actual remains the posted ledger, cashback SQLite remains live cashback and
period-close authority, OneDrive remains immutable evidence storage, and Git
remains deployable configuration authority. No `finance_period_closes` table is
allowed. The existing fixed-purpose Postgres Actual-writer lease remains the
only separate Postgres state and holds atomic fencing only.

## Exact current workflow access

WF19 creates the schema for all current tables. For
`finance_source_contracts`, WF19 only creates the table and does not seed rows.
The machine inventory derives every column independently from the workflow JSON:
WF19 schema definitions, filter key reads, explicit
`parameters.columns.value.<column>` writes, and downstream expressions or row
spreads from `get` nodes. Each binding records the Data Table node, binding node,
parameter path and binding SHA-256. No operation is copied from a sibling column;
the resulting 203 rows contain 279 explicit value writes and omit the 159 false
writes found in the previous table-level expansion. Every row includes its own
disposition rationale.

| Current table | Exact access beyond WF19 schema creation | 15-to-4 direction |
| --- | --- | --- |
| `finance_source_contracts` | WF02, WF04, WF05 and WF09 read; no row producer | Generated source/runtime resolver |
| `finance_source_cursors` | WF12 read/update | `finance_ingestion_state` |
| `finance_acquisition_receipts` | WF12 read/write | Committed boundary fields into `finance_ingestion_state`; lifecycle to n8n |
| `finance_archive_receipts` | WF01 read/write; no WF11 access | Merge into `finance_documents` |
| `finance_document_operations` | WF01 write, WF11 read, WF13 read/write; no WF14 access | Merge into `finance_documents` with separate archive/processing state |
| `finance_pipeline_runs` | WF03, WF04 and WF05 read/write | Remove to n8n execution metadata |
| `finance_actual_outbox` | WF03 prepare, WF17 recovery read, WF20 read/write | Merge into `finance_actual_batches` |
| `finance_actual_verifications` | WF20 read/write | Immutable OneDrive verification artifact; pointer and hash on batch |
| `finance_reconciliations` | WF03 read/write | Cashback companion close authority; no close Data Table |
| `finance_config_versions` | WF19 seed/read | Generated source/runtime resolver plus deployment receipt |
| `finance_provider_circuits` | WF01, WF09, WF12 and WF16 read/write | Remove to n8n retry/error/concurrency policy |
| `finance_execution_failures` | WF16 read/write | Remove to n8n execution/error records |
| `finance_mcp_requests` | WF10 read/write | Remove to n8n status and Cloudflare access logs |
| `finance_agent_jobs` | WF09 read/write | Review-only fields into `finance_ai_reviews`; execution to n8n |
| `finance_ai_policy_contracts` | WF19 seed/read and WF09 read | Generated AI-policy/output-contract resolver |

Previously stated WF11 archive access and WF14 document-operation access do not
exist. Any other unlisted reference is a blocking inventory error.

## Candidate four-table schemas

`finance_ingestion_state` (9): `source_code`, `cursor_value`, `cursor_version`,
`committed_run_id`, `run_upper_bound`, `scanned_count`, `matched_count`,
`downstream_receipt_sha256`, `updated_at`.

`finance_documents` (20): `document_id`, `source_code`, `source_message_id`,
`source_attachment_id`, `source_sha256`, `onedrive_item_id`, `onedrive_etag`,
`document_profile`, `requested_schema_version`, `archive_state`,
`processing_state`, `attempt_count`, `parser_version`, `output_sha256`,
`error_class`, `actual_file_id`, `account_id`, `period_key`, `verified_at`,
`updated_at`.

`finance_actual_batches` (18): `batch_id`, `run_id`, `imported_id`,
`actual_file_id`, `account_id`, `period_start`, `period_end`, `payload_sha256`,
`artifact_item_id`, `artifact_etag`, `artifact_schema_version`, `config_sha256`,
`parser_version`, `state`, `actual_transaction_id`,
`verification_artifact_item_id`, `verification_artifact_sha256`, `updated_at`.

`finance_ai_reviews` (9): `review_id`, `idempotency_key`, `operation_code`,
`proposal_artifact_item_id`, `proposal_sha256`, `review_state`,
`review_decision`, `reviewed_at`, `updated_at`.

The machine target-schema matrix declares every one of these 56 columns with its
type, constraints, authoritative owner, exact source-column lineage and
transformation, current source producer/consumer bindings, and the planned target
producer/consumer binding. In particular, `finance_actual_batches.account_id`,
`period_start`, and `period_end` descend from the WF20 verification manifest;
`verification_artifact_item_id` comes from the immutable OneDrive upload/readback;
and `verification_artifact_sha256` is the read-back hash of the canonical full
expected/observed verification receipt. These schemas are a direction for
independent review, not an approved migration.

## Generated caller-immutable resolvers

Source, config and policy contracts move to exactly two generated subworkflows
made from server-owned Edit Fields nodes:

1. `Finance/Shared/Resolve Finance Source and Runtime Config`, generated from
   statement, email, document, deployment and account-mapping configuration;
2. `Global/Shared/Resolve AI Policy and Output Contract`, generated from AI
   policy, agent-provider and output-schema configuration and returning the
   server-owned provider, model, reasoning, auth mode, package and schema.

They are generated from reviewed Git inputs, content-addressed, image/commit
bound, read-only to callers, and hash-checked before activation. Callers cannot
choose or override provider, model, mailbox, folder, URL, account, card, source,
policy, output schema, or allowed fields. A missing key, caller override, hash
mismatch or ungenerated edit fails closed. Deployment and domain artifacts may
record resolver hashes but must not recreate editable config tables.

Current parameter ownership is scattered across compose/environment runtime
values, `config/*.json` and generated Data Table seeds, plus workflow JSON, Code
and Set-node literals. The target inventory assigns every value exactly once:

- secrets and auth remain n8n credentials or 1Password-rendered environment;
- runtime URLs, images, mounts, ports, timezone and concurrency remain in
  compose/environment;
- the two resolvers above own reusable Global and Finance non-secret config;
- each callable workflow declares a strict typed input schema and then exactly
  one `Workflow Parameters` Edit Fields node for truly local non-secret
  constants/defaults; downstream expressions read
  `$('Workflow Parameters').first().json.<field>`.

Callers may pass invocation facts only: IDs, hashes, window, cursor, version and
operation. Source-owning workflows fix `source_code` locally. Provider, model,
account, mail folder, OneDrive path, credential, URL and commit flags are never
caller-controlled. Unknown fields, override attempts, missing/duplicate keys,
resolver hash drift and duplicated authoritative literals fail closed. A static
authority scan must prove every literal/config binding has one owner, and no
generic parameter table may be introduced. All Execute Sub-workflow selectors
use From list. This design assumes no Enterprise Variables feature.

## Provider circuits and observability

Provider circuits are removed. Bounded n8n retries, error workflows and
per-provider concurrency controls must pass duplicate, concurrency, induced
failure and restart fixtures. No new Postgres circuit store is allowed. The
existing writer lease is unchanged and remains limited to Actual fencing.

Self-attesting readback booleans are removed in favor of independent receipts.
Generic execution IDs, retry counts and detailed errors remain in n8n. Caller
access identity remains only in Cloudflare. Optional Langfuse traces must be
sanitized and cannot contain document text, mail bodies, tokens, provider
responses, transaction payloads or authoritative review state.

## WF20 correctness blockers

WF20 must be redesigned before the four-table target can be approved. It may
not advance `finance_actual_batches` to verified or committed until it:

1. performs exact Actual readback under the existing writer fence;
2. writes the full expected/observed comparison to an immutable OneDrive
   verification artifact;
3. reads back the artifact item identity and SHA-256;
4. binds both to the same batch before the terminal state transition.

Kill-before-upload, kill-after-upload-before-batch-update, duplicate delivery,
stale fence, mismatched artifact hash, missing artifact and readback mismatch
fixtures must prove recovery without duplicate Actual writes, false verification
or early cursor advancement.

## Migration and rollback acceptance

Before mutation, a disposable receipt binds the exact Git commit, image digest,
workflow corpus hash, machine-inventory hash, all 15 schemas and IDs, row counts,
canonical row digests, resolver hashes, existing writer-lease schema and every
workflow-node access. It must prove:

- exact typed row/column maps into the four schemas, generated resolvers, n8n,
  Cloudflare, cashback companion, OneDrive artifacts or removal;
- no unlisted producer/consumer and no dropped required state;
- cursor, valid-empty, archive, document recovery, Actual writer/readback,
  cashback close, bounded MCP and AI review behavior parity;
- provider-circuit removal using n8n only;
- the complete WF20 failure matrix above;
- zero posted transactions, cashback events, secrets, access logs, provider
  payloads, decrypted text or document content in Data Tables;
- a second migration changes no row, schema or timestamp.

Rollback restores all 15 original schemas, IDs, typed rows, hashes, state
transitions, workflow access and supported timestamps from an immutable backup.
It restores the prior generated resolver pair and leaves the existing writer
lease unchanged. It calls no provider, Actual, cashback, OneDrive or Cloudflare
service and activates/publishes no workflow. Any collision, missing mapping,
unlisted access, count/hash mismatch, privacy violation or drift fails closed.

Final approval requires a separate reviewer to accept every one of the 203
machine dispositions, both resolver contracts, all four target schemas, the
WF20 state machine and exact migration/rollback receipts.
