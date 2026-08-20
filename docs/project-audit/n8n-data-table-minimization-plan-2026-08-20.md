# n8n Data Table minimization plan

Date: 2026-08-20
Status: design and audit only; no schema or workflow migration is authorized
Backlog requirement: `N8N-011`

## Decision boundary

The deployed 15-table contract is evidence of the current state, not evidence
that every table and column belongs in persistent n8n Data Tables. Actual
remains the sole posted ledger, cashback SQLite remains the live cashback
store, OneDrive remains the immutable evidence store, and Git remains the
canonical deployable configuration owner.

The candidate target below is deliberately non-binding. Before any migration,
an implementation agent must enumerate every concrete producer and consumer
node, confirm every invariant and authoritative owner, and independently review
the migration and rollback receipts. A missing producer, consumer, invariant,
retention basis, or privacy basis means the table or column cannot be approved.

## Candidate table disposition matrix

| Current table | Candidate disposition | Candidate authoritative owner | Known producer and consumer boundary to verify | Invariant that must survive |
| --- | --- | --- | --- | --- |
| `finance_source_contracts` | Remove from Data Tables; compile Git-canonical source contracts into immutable runtime configuration and retain the applied fingerprint in receipts | Git configuration | WF19 currently seeds; acquisition, monthly, browser-handoff and AI-artifact flows read | Callers cannot choose mailbox, sender, destination, Actual identity, card identity, overlap or schedule |
| `finance_source_cursors` | Retain and simplify | n8n acquisition state, except cashback-owned cursors | WF12 reads, compare-and-swaps and commits; acquisition controllers consume | One authoritative row per n8n-owned source; frozen upper bound; one commit only after durable downstream proof |
| `finance_acquisition_receipts` | Candidate merge into `finance_operation_receipts` with a typed acquisition payload | n8n durable operations receipts | WF12 writes and reads; cursor commit consumes | Exact window, pagination, scanned and matched counts, valid-empty heartbeat, and downstream proof remain queryable |
| `finance_archive_receipts` | Retain and simplify | OneDrive evidence catalogue linkage | WF01 and reviewed artifact handoff write/read; evidence and document flows consume | Stable source identity plus content hash resolves to one verified OneDrive object |
| `finance_document_operations` | Retain and simplify | document-evidence state | WF13/WF14 write/read; parsing and reconciliation consume | No binary, decrypted plaintext or extracted text persists; state transitions and artifact hashes are durable |
| `finance_pipeline_runs` | Candidate merge into `finance_operation_receipts` | n8n durable operations receipts | Shared, issuer, browser and operations workflows write/read | One terminal durable receipt per workflow run; valid-empty remains distinguishable from failure |
| `finance_actual_outbox` | Retain; evaluate removal of lease and attempt diagnostics that have separate owners | Actual mutation intent owned by the single writer | WF03 prepares; WF18 fences; WF20 applies; WF17 recovers | Deterministic imported ID and monotonic PREPARED to ACTUAL_OBSERVED to VERIFIED to COMMITTED transition |
| `finance_actual_verifications` | Retain as independent append-only proof; evaluate column simplification only | Actual readback evidence | WF20 writes after Actual readback; reconciliation and recovery consume | Expected and observed economic state remain independently comparable to the outbox write attempt |
| `finance_reconciliations` | Retain and simplify | statement reconciliation and cashback-close state | WF03 and issuer controllers write/read; cashback close consumes | No finalization without statement evidence and verified Actual readback; second close is a no-op |
| `finance_config_versions` | Remove from Data Tables; Git owns versions and receipts retain applied hashes | Git configuration | WF19 currently seeds; configuration-bound workflows read | Exact deployed commit and content fingerprint remain provable without an editable duplicate configuration store |
| `finance_provider_circuits` | Move to a narrowly owned operational Postgres circuit store, or retain only if the audit proves Data Table semantics are required | provider operational resilience | Provider-facing workflows and WF16 mutate; controllers check | Atomic CLOSED, OPEN and HALF_OPEN transitions, retry deadline and failure count survive restart |
| `finance_execution_failures` | Candidate merge into `finance_operation_receipts` with a typed redacted-failure payload | n8n durable operations receipts | WF16 writes/readbacks; WF10 and recovery consume | Failure is redacted, attributable, actionable and replay-linked without secrets or source payloads |
| `finance_mcp_requests` | Candidate merge into `finance_operation_receipts` with a typed MCP payload | bounded interface audit | WF15 dispatches and writes; WF10/status consumes | Fixed operation code, caller hash, request/result hashes, Service Auth boundary and terminal state remain auditable |
| `finance_agent_jobs` | Retain and simplify | reviewable AI proposal state | WF09/WF21 write/read; review and archive flows consume | One idempotent schema-bound proposal, protected fields rejected, artifact hash verified, review state durable |
| `finance_ai_policy_contracts` | Remove from Data Tables; compile Git-canonical policies and domains into the immutable runtime bundle, with applied hashes in agent jobs | Git AI policy configuration | WF19 currently seeds; WF09/WF21 and the runner read | Provider, allowed fields, allowed values and output schema remain server-owned and caller-immutable |

The candidate target is seven retained domain tables plus one consolidated
operation-receipt table. Provider circuits remain durable operational state but
are a candidate for a fixed Postgres store rather than an n8n Data Table. This
count is not an acceptance target: the producer/consumer audit may justify a
different result.

## Every-column candidate review

The actions below are hypotheses to test. `Keep` means the column appears to
carry durable domain state. `Move` means another named owner should hold it.
`Merge` means it should be represented in the typed consolidated receipt.
`Review/remove` means it is redundant, derivable, self-attesting, overly
denormalized, or privacy-sensitive unless a producer/consumer proves otherwise.

### `finance_source_contracts`

| Columns | Candidate action and rationale |
| --- | --- |
| `source_code`, `config_version`, `folder_id`, `senders_json`, `subjects_json`, `onedrive_parent_id`, `manifest_onedrive_parent_id`, `overlap_seconds`, `cycle_day`, `deadline_days`, `actual_file_id`, `account_id`, `card_code`, `cashback_close_required`, `enabled` | Move to the Git-canonical source contract and immutable runtime bundle; these are trusted configuration, not mutable durable state |
| `content_sha256` | Move the applied hash to the operation/acquisition receipt; do not duplicate the canonical configuration row |
| `updated_at` | Remove with the table; Git commit time and receipt observation time are the authoritative timestamps |

### `finance_source_cursors`

| Columns | Candidate action and rationale |
| --- | --- |
| `source_code`, `cursor_value`, `committed_run_id`, `run_upper_bound`, `scanned_count`, `matched_count`, `cursor_version`, `updated_at` | Keep pending exact producer/consumer confirmation; these prove the committed boundary and enumerated window |
| `overlap_seconds` | Remove from the cursor row; it is source configuration and should be bound by the receipt/config fingerprint |
| `readback_verified` | Review/remove as self-attestation; acceptance should rely on the exact readback receipt, not a mutable boolean on the same row |

### `finance_acquisition_receipts`

| Columns | Candidate action and rationale |
| --- | --- |
| `run_id`, `source_code`, `window_start`, `run_upper_bound`, `pages_fetched`, `pagination_exhausted`, `scanned_count`, `matched_count`, `heartbeat`, `terminal_state`, `downstream_receipt_sha256`, `created_at`, `updated_at` | Merge into the typed acquisition portion of `finance_operation_receipts`; preserve exact empty and paginated-run semantics |
| `cursor_commit_eligible` | Review/remove if it is deterministically derived from terminal state plus downstream receipt and invariant checks |
| `readback_verified` | Review/remove as self-attestation; store the immutable receipt hash and independent readback result instead |

### `finance_archive_receipts`

| Columns | Candidate action and rationale |
| --- | --- |
| `run_id`, `source_code`, `source_message_id`, `source_attachment_id`, `source_sha256`, `onedrive_item_id`, `onedrive_etag`, `archive_state`, `verified_at` | Keep pending privacy and retention review; together they bind source identity, immutable content and provider readback |
| `archive_receipt_id` | Review/remove if the platform row identity plus the declared composite idempotency key is sufficient |
| `updated_at` | Review/remove for terminal immutable rows; retain only when a documented state transition producer requires it |

### `finance_document_operations`

| Columns | Candidate action and rationale |
| --- | --- |
| `document_id`, `source_sha256`, `document_profile`, `requested_schema_version`, `state`, `attempt_count`, `parser_version`, `output_sha256`, `error_class`, `updated_at` | Keep or simplify; these describe durable redacted document processing state |
| `onedrive_item_id`, `source_message_id`, `source_attachment_id`, `source_code` | Review/remove as denormalized archive-receipt fields; prefer one durable archive-receipt reference if lookup and recovery remain adequate |
| `config_version`, `actual_file_id`, `account_id`, `period_key` | Review individually; retain only fields required for deterministic parsing/reconciliation that cannot be resolved through immutable references |
| `last_execution_id` | Move to the operation receipt unless document-state recovery proves a durable direct link is required |
| `error_detail_redacted` | Move to the typed failure receipt; keep only a bounded error class on the document row |

### `finance_pipeline_runs`

| Columns | Candidate action and rationale |
| --- | --- |
| `run_id`, `workflow_code`, `source_code`, `trigger_kind`, `config_version`, `state`, `receipt_sha256`, `started_at`, `completed_at`, `updated_at` | Merge into `finance_operation_receipts`; deduplicate shared identifiers and timestamps across typed receipt kinds |
| `terminal_readback_verified` | Review/remove as self-attestation; the receipt validator and immutable receipt hash must prove readback |

### `finance_actual_outbox`

| Columns | Candidate action and rationale |
| --- | --- |
| `outbox_id`, `run_id`, `imported_id`, `actual_file_id`, `payload_sha256`, `artifact_item_id`, `artifact_etag`, `artifact_schema_version`, `config_version`, `parser_version`, `state`, `actual_transaction_id`, `updated_at` | Keep pending the exact writer/recovery matrix; these bind intent, immutable payload, deployed parser/config and observed Actual identity |
| `lease_owner`, `lease_fence`, `lease_expires_at` | Review moving to the fixed writer-lease store owned by WF18; retain on the outbox only if recovery requires the historical fence |
| `attempt_count`, `last_error_class` | Review moving attempt diagnostics to typed operation/failure receipts while retaining enough state for bounded recovery |

### `finance_actual_verifications`

| Columns | Candidate action and rationale |
| --- | --- |
| `outbox_id`, `verification_version`, `actual_file_id`, `account_id`, `period_start`, `period_end`, `expected_payload_sha256`, `observed_payload_sha256`, `expected_count`, `observed_count`, `expected_amount_sum_minor`, `observed_amount_sum_minor`, `invariants_passed`, `verified_at` | Keep as append-only independent readback proof, subject to producer and consumer confirmation |
| `expected_account_balance`, `observed_account_balance` | Keep only with an explicit minor-unit/currency contract; otherwise replace with unambiguous signed minor-unit fields |
| `invariants_passed` | Review whether this summary is useful or dangerously self-attesting; exact compared fields and validator receipt remain authoritative |

### `finance_reconciliations`

| Columns | Candidate action and rationale |
| --- | --- |
| `source_code`, `period_key`, `reconciliation_version`, `statement_sha256`, `actual_verification_sha256`, `cashback_close_id`, `state`, `difference_minor`, `verified_at` | Keep; these form the durable evidence-backed close state |
| `updated_at` | Review/remove if reconciliation versions are append-only and `verified_at` plus receipt time cover the lifecycle |

### `finance_config_versions`

| Columns | Candidate action and rationale |
| --- | --- |
| `config_name`, `version`, `source_path`, `content_sha256`, `git_commit`, `state`, `activated_at`, `retired_at` | Move to Git and deployment/operation receipts; do not maintain a second editable configuration registry |
| `readback_verified` | Remove as self-attestation; receipt validation proves the applied hash |

### `finance_provider_circuits`

| Columns | Candidate action and rationale |
| --- | --- |
| `provider_code`, `state`, `transient_failure_count`, `last_error_class`, `opened_at`, `retry_after`, `updated_at` | Move together to one fixed operational circuit table if Data Table concurrency cannot prove atomic transitions |
| `readback_verified` | Review/remove as self-attestation; transition compare-and-swap plus readback receipt should be authoritative |

### `finance_execution_failures`

| Columns | Candidate action and rationale |
| --- | --- |
| `execution_id`, `workflow_code`, `run_id`, `provider_code`, `error_class`, `error_message_redacted`, `first_seen_at`, `acknowledged_at`, `replay_execution_id` | Merge into the typed failure portion of `finance_operation_receipts` after privacy and replay-link review |
| `workflow_id`, `workflow_name`, `workflow_code` | Keep one stable workflow code plus only the minimum snapshot needed for incident readability; remove redundant identifiers unless drift forensics requires them |
| `readback_verified` | Review/remove as self-attestation |

### `finance_mcp_requests`

| Columns | Candidate action and rationale |
| --- | --- |
| `request_id`, `request_sha256`, `operation_code`, `caller_subject_hash`, `state`, `result_sha256`, `error_class`, `error_message_redacted`, `created_at`, `completed_at` | Merge into the typed MCP portion of `finance_operation_receipts`; retain Service Auth/privacy requirements |
| `target_workflow_code` | Review/remove if it is a server-owned deterministic mapping from `operation_code`; retain if dispatch-version forensics requires it |
| `readback_verified` | Review/remove as self-attestation |

### `finance_agent_jobs`

| Columns | Candidate action and rationale |
| --- | --- |
| `idempotency_key`, `operation_code`, `policy_id`, `policy_sha256`, `config_sha256`, `output_schema_sha256`, `request_sha256`, `state`, `runner_receipt_id`, `proposal_sha256`, `proposal_artifact_item_id`, `proposal_artifact_etag`, `proposal_artifact_schema`, `review_state`, `review_decision`, `reviewed_at`, `attempt_count`, `error_class`, `created_at`, `updated_at` | Keep or simplify; these bind one server-owned policy request to an immutable schema-valid proposal and durable review state |
| `job_id` | Review/remove if the platform row identity plus idempotency key is sufficient; retain only when external receipt linkage requires it |
| `reviewed_by_hash` | Privacy review required; retain only if accountability cannot be met through a less identifying audit identity |
| `terminal_readback_verified` | Review/remove as self-attestation |

### `finance_ai_policy_contracts`

| Columns | Candidate action and rationale |
| --- | --- |
| `policy_id`, `policy_version`, `agent_profile`, `agent_provider`, `policy_sha256`, `config_sha256`, `output_schema_sha256`, `allowed_fields_json`, `allowed_values_json`, `state` | Move to Git-canonical AI policy and immutable runtime bundle; agent jobs retain applied hashes |
| `updated_at` | Remove with the table; Git and receipt timestamps are authoritative |

## Mandatory producer and consumer audit

For every table and column, the implementation receipt must identify:

1. every workflow file, node ID and operation that creates, reads, updates or
   deletes it;
2. every setup/bootstrap generator and seed row;
3. every external consumer, including operations, recovery, agent runner,
   Actual writer and cashback close;
4. its authoritative owner and why a reference cannot replace duplicated data;
5. its invariant, allowed states, idempotency key and concurrency boundary;
6. its retention period, deletion authority and privacy classification;
7. whether the value is caller-controlled, server-owned, derived, immutable,
   self-attesting, or recoverable from another authoritative artifact.

The matrix must fail if a workflow references an unlisted table/column, if a
listed field has no producer or consumer without an explicit archival reason,
or if two stores claim authority for the same finance fact.

## Migration acceptance

Before the first schema change, the disposable receipt must bind the exact Git
commit, image digest, workflow corpus hash, current 15 schemas, every row count,
canonical row digest, Data Table ID mapping, configuration seed digest and
producer/consumer matrix hash. It must then prove:

- transformed row counts and hashes for every retained, merged, moved or
  deleted table and column;
- exact behavior parity for cursor commit, valid-empty acquisition, archive,
  document recovery, Actual outbox/recovery, reconciliation/close, bounded MCP,
  redacted failures and agent proposals;
- logical uniqueness under duplicate and concurrent delivery;
- restart persistence and retention/privacy scans;
- no posted transaction, cashback event, secret, provider response, document
  payload or decrypted text in Data Tables;
- a second migration changes no row, schema or timestamp.

## Rollback acceptance

Rollback must use a pre-migration immutable backup and a reviewed reverse map.
It must restore all 15 original schemas, Data Table identities, rows, hashes,
state transitions and workflow behavior exactly, including timestamps where the
platform supports deterministic preservation. It must not call Outlook,
OneDrive, an AI provider, Actual or cashback, and it must not activate or
publish a workflow. A collision, missing mapping, unexpected producer, row
count/hash mismatch or rollback drift fails closed before production promotion.
