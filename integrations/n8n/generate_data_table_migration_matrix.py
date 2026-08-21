"""Generate the finance-owned Data Table migration inventory.

The scanner is deliberately read-only with respect to n8n and the finance
runtime.  It inventories checked-in contracts and workflow exports so the
later migration step can be reviewed against an exact source commit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
N8N = ROOT / "integrations" / "n8n"
DATA_TABLES = N8N / "data-tables.json"
SCHEMA = N8N / "data-table-migration-matrix.schema.json"
OUTPUT = N8N / "data-table-migration-matrix.json"
SCAN_ROOTS = (
    "integrations/n8n/workflows/*.json",
    "integrations/n8n/disposable/generated/*.json",
    "integrations/n8n/setup-workflows/*.json",
)
# Provenance follows the last commit touching the scanned source tree.  The
# generator, schema, output, and tests live outside these paths, so committing
# the tooling cannot make its own --check stale.
SOURCE_REF_PATHS = (
    "integrations/n8n/data-tables.json",
    "integrations/n8n/workflows",
    "integrations/n8n/disposable/generated",
    "integrations/n8n/setup-workflows",
)
OPERATIONS = ("create", "get", "insert", "upsert", "update")
TARGETS = (
    "finance_ingestion_state",
    "finance_documents",
    "finance_actual_batches",
    "finance_ai_reviews",
)
DOCUMENT_IDENTITY_FIELDS = {
    "MAIL_LINKED": ("source_sha256", "source_message_id", "source_attachment_id"),
    "BROWSER_CAPTURE": ("source_sha256", "capture_id", "account_id", "period_key"),
    "PROCESSING_ONLY": ("source_sha256", "document_profile", "requested_schema_version"),
}


def _target_column(source_type: str, *sources: str) -> dict[str, Any]:
    """Describe one target field and the source columns allowed to populate it."""
    return {"type": source_type, "source_bindings": list(sources)}


def _resolver_column(source_type: str, artifact: str, field: str) -> dict[str, Any]:
    """Describe a field resolved from a durable artifact rather than an old row."""
    return _target_column(source_type, f"{artifact}.{field}")


# These are the four executable target contracts.  Source columns may merge
# only when their target field and type are explicitly listed here; this keeps
# a migration review from silently collapsing unrelated values into one field.
TARGET_SCHEMAS: dict[str, dict[str, Any]] = {
    "finance_ingestion_state": {
        "logical_key": ["source_code"],
        "identity_derivations": [
            {
                "source_table": "finance_source_cursors",
                "strategy": "direct",
                "target_key": ["source_code"],
                "source_fields": ["source_code"],
            },
            {
                "source_table": "finance_acquisition_receipts",
                "strategy": "direct",
                "target_key": ["source_code"],
                "source_fields": ["source_code"],
            },
        ],
        "columns": {
            "source_code": _target_column(
                "string",
                "finance_source_cursors.source_code",
                "finance_acquisition_receipts.source_code",
            ),
            "cursor_value": _target_column("date", "finance_source_cursors.cursor_value"),
            "committed_run_id": _target_column(
                "string",
                "finance_source_cursors.committed_run_id",
                "finance_acquisition_receipts.run_id",
            ),
            "run_upper_bound": _target_column(
                "date",
                "finance_source_cursors.run_upper_bound",
                "finance_acquisition_receipts.run_upper_bound",
            ),
            "overlap_seconds": _target_column("number", "finance_source_cursors.overlap_seconds"),
            "scanned_count": _target_column(
                "number",
                "finance_source_cursors.scanned_count",
                "finance_acquisition_receipts.scanned_count",
            ),
            "matched_count": _target_column(
                "number",
                "finance_source_cursors.matched_count",
                "finance_acquisition_receipts.matched_count",
            ),
            "cursor_version": _target_column("number", "finance_source_cursors.cursor_version"),
            "readback_verified": _target_column(
                "boolean",
                "finance_source_cursors.readback_verified",
                "finance_acquisition_receipts.readback_verified",
            ),
            "updated_at": _target_column(
                "date",
                "finance_source_cursors.updated_at",
                "finance_acquisition_receipts.updated_at",
            ),
            "last_window_start": _target_column("date", "finance_acquisition_receipts.window_start"),
            "last_pages_fetched": _target_column("number", "finance_acquisition_receipts.pages_fetched"),
            "last_pagination_exhausted": _target_column(
                "boolean", "finance_acquisition_receipts.pagination_exhausted"
            ),
            "last_heartbeat": _target_column("boolean", "finance_acquisition_receipts.heartbeat"),
            "last_terminal_state": _target_column("string", "finance_acquisition_receipts.terminal_state"),
            "last_receipt_created_at": _target_column("date", "finance_acquisition_receipts.created_at"),
            "downstream_receipt_sha256": _target_column(
                "string", "finance_acquisition_receipts.downstream_receipt_sha256"
            ),
            "attachment_verification_barrier": _target_column(
                "string", "finance_acquisition_receipts.attachment_verification_barrier"
            ),
            "attachment_ids_verified": _target_column(
                "boolean", "finance_acquisition_receipts.attachment_ids_verified"
            ),
            "attachment_identity_keys_json": _target_column(
                "string", "finance_acquisition_receipts.attachment_identity_keys_json"
            ),
            "attachments_verified": _target_column(
                "number", "finance_acquisition_receipts.attachments_verified"
            ),
            "email_evidence_receipt_barrier": _target_column(
                "string", "finance_acquisition_receipts.email_evidence_receipt_barrier"
            ),
            "email_evidence_receipts_verified": _target_column(
                "number", "finance_acquisition_receipts.email_evidence_receipts_verified"
            ),
            "email_evidence_identity_keys_json": _target_column(
                "string", "finance_acquisition_receipts.email_evidence_identity_keys_json"
            ),
            "archive_ready": _target_column(
                "boolean", "finance_acquisition_receipts.archive_ready"
            ),
            "inventory_run_id": _resolver_column("string", "inventory-v1", "inventory_run_id"),
            "inventory_fence": _resolver_column("number", "inventory-v1", "inventory_fence"),
            "inventory_sha256": _resolver_column("string", "inventory-v1", "inventory_sha256"),
            "inventory_item_id": _resolver_column("string", "inventory-v1", "inventory_item_id"),
            "inventory_path": _resolver_column("string", "inventory-v1", "inventory_path"),
            "inventory_etag": _resolver_column("string", "inventory-v1", "inventory_etag"),
            "inventory_schema_version": _resolver_column(
                "string", "inventory-v1", "inventory_schema_version"
            ),
            "inventory_length_bytes": _resolver_column(
                "number", "inventory-v1", "inventory_length_bytes"
            ),
        },
    },
    "finance_documents": {
        "logical_key": ["document_id"],
        "identity_derivations": [
            {
                "source_table": "finance_archive_receipts",
                "strategy": "versioned_length_prefixed_sha256",
                "target_key": ["document_id"],
                "source_fields": [
                    "source_sha256",
                    "source_message_id",
                    "source_attachment_id",
                ],
                "identity_kind": "MAIL_LINKED",
                "version": "document-identity-v1",
                "length_prefix": "uint64_be",
                "tuple_encoding": "versioned_length_prefixed_binary",
                "digest_encoding": "base64url_unpadded",
                "alias_fields": [],
            },
            {
                "source_table": "finance_document_operations",
                "strategy": "versioned_length_prefixed_sha256",
                "target_key": ["document_id"],
                "source_fields": [
                    "source_sha256",
                    "source_message_id",
                    "source_attachment_id",
                ],
                "identity_kind": "MAIL_LINKED",
                "version": "document-identity-v1",
                "length_prefix": "uint64_be",
                "tuple_encoding": "versioned_length_prefixed_binary",
                "digest_encoding": "base64url_unpadded",
                "alias_fields": ["document_id"],
                "legacy_to_canonical": {
                    "adapter": "document-identity-alias-v1",
                    "legacy_fields": ["document_id"],
                    "canonical_target": "finance_documents.document_id",
                },
                "fallback_identity": {
                    "identity_kind": "PROCESSING_ONLY",
                    "source_fields": [
                        "source_sha256",
                        "document_profile",
                        "requested_schema_version",
                    ],
                },
            },
        ],
        "columns": {
            "document_id": _target_column(
                "string",
                "finance_archive_receipts.source_sha256",
                "finance_archive_receipts.source_message_id",
                "finance_archive_receipts.source_attachment_id",
                "finance_document_operations.source_sha256",
                "finance_document_operations.source_message_id",
                "finance_document_operations.source_attachment_id",
                "finance_document_operations.document_profile",
                "finance_document_operations.requested_schema_version",
            ),
            "archive_receipt_id": _target_column("string", "finance_archive_receipts.archive_receipt_id"),
            "run_id": _target_column("string", "finance_archive_receipts.run_id"),
            "source_code": _target_column(
                "string",
                "finance_archive_receipts.source_code",
                "finance_document_operations.source_code",
            ),
            "source_message_id": _target_column(
                "string",
                "finance_archive_receipts.source_message_id",
                "finance_document_operations.source_message_id",
            ),
            "source_attachment_id": _target_column(
                "string",
                "finance_archive_receipts.source_attachment_id",
                "finance_document_operations.source_attachment_id",
            ),
            "source_sha256": _target_column(
                "string",
                "finance_archive_receipts.source_sha256",
                "finance_document_operations.source_sha256",
            ),
            "onedrive_item_id": _target_column(
                "string",
                "finance_archive_receipts.onedrive_item_id",
                "finance_document_operations.onedrive_item_id",
            ),
            "onedrive_etag": _target_column("string", "finance_archive_receipts.onedrive_etag"),
            "archive_state": _target_column("string", "finance_archive_receipts.archive_state"),
            "archive_verified_at": _target_column("date", "finance_archive_receipts.verified_at"),
            "document_profile": _target_column("string", "finance_document_operations.document_profile"),
            "requested_schema_version": _target_column(
                "string", "finance_document_operations.requested_schema_version"
            ),
            "config_version": _target_column("string", "finance_document_operations.config_version"),
            "actual_file_id": _target_column("string", "finance_document_operations.actual_file_id"),
            "account_id": _target_column("string", "finance_document_operations.account_id"),
            "period_key": _target_column("string", "finance_document_operations.period_key"),
            "state": _target_column("string", "finance_document_operations.state"),
            "attempt_count": _target_column("number", "finance_document_operations.attempt_count"),
            "last_execution_id": _target_column("string", "finance_document_operations.last_execution_id"),
            "parser_version": _target_column("string", "finance_document_operations.parser_version"),
            "output_sha256": _target_column("string", "finance_document_operations.output_sha256"),
            "error_class": _target_column("string", "finance_document_operations.error_class"),
            "error_detail_redacted": _target_column(
                "string", "finance_document_operations.error_detail_redacted"
            ),
            "updated_at": _target_column(
                "date",
                "finance_archive_receipts.updated_at",
                "finance_document_operations.updated_at",
            ),
        },
    },
    "finance_actual_batches": {
        "logical_key": ["idempotency_key"],
        "identity_derivations": [
            {
                "source_table": "finance_actual_outbox",
                "strategy": "direct",
                "target_key": ["idempotency_key"],
                "source_fields": ["imported_id"],
            },
            {
                "source_table": "finance_actual_verifications",
                "strategy": "join",
                "target_key": ["idempotency_key"],
                "cardinality": "exactly_one",
                "join_steps": [
                    {
                        "table": "finance_actual_outbox",
                        "left_fields": ["outbox_id"],
                        "right_fields": ["outbox_id"],
                    }
                ],
                "terminal_fields": ["imported_id"],
            },
            {
                "source_table": "finance_reconciliations",
                "strategy": "join",
                "target_key": ["idempotency_key"],
                "cardinality": "exactly_one",
                "join_key": {
                    "source_fields": [
                        "source_code",
                        "period_key",
                        "actual_verification_sha256",
                    ],
                    "target_fields": [
                        "source_code",
                        "period_key",
                        "verification_artifact_sha256",
                    ],
                },
                "join_steps": [
                    {
                        "table": "finance_actual_verifications",
                        "left_fields": ["actual_verification_sha256"],
                        "right_fields": ["verification_artifact_sha256"],
                    },
                    {
                        "table": "finance_actual_outbox",
                        "left_fields": ["outbox_id"],
                        "right_fields": ["outbox_id"],
                    },
                ],
                "terminal_fields": ["imported_id"],
            },
        ],
        "columns": {
            "batch_id": _target_column(
                "string",
                "finance_actual_outbox.outbox_id",
                "finance_actual_verifications.outbox_id",
            ),
            "run_id": _target_column("string", "finance_actual_outbox.run_id"),
            "idempotency_key": _target_column("string", "finance_actual_outbox.imported_id"),
            "actual_file_id": _target_column(
                "string",
                "finance_actual_outbox.actual_file_id",
                "finance_actual_verifications.actual_file_id",
            ),
            "delta_sha256": _target_column("string", "finance_actual_outbox.payload_sha256"),
            "delta_artifact_item_id": _target_column("string", "finance_actual_outbox.artifact_item_id"),
            "delta_artifact_etag": _target_column("string", "finance_actual_outbox.artifact_etag"),
            "delta_schema_version": _target_column(
                "string", "finance_actual_outbox.artifact_schema_version"
            ),
            "config_version": _target_column("string", "finance_actual_outbox.config_version"),
            "parser_version": _target_column("string", "finance_actual_outbox.parser_version"),
            "state": _target_column("string", "finance_actual_outbox.state"),
            "actual_transaction_id": _target_column(
                "string", "finance_actual_outbox.actual_transaction_id"
            ),
            "attempt_count": _target_column("number", "finance_actual_outbox.attempt_count"),
            "last_error_class": _target_column("string", "finance_actual_outbox.last_error_class"),
            "updated_at": _target_column(
                "date",
                "finance_actual_outbox.updated_at",
                "finance_reconciliations.updated_at",
            ),
            "verification_version": _target_column(
                "number", "finance_actual_verifications.verification_version"
            ),
            "account_id": _target_column(
                "string",
                "finance_actual_outbox.account_id",
                "finance_actual_verifications.account_id",
            ),
            "card_code": _target_column(
                "string",
                "finance_actual_outbox.card_code",
                "finance_actual_verifications.card_code",
            ),
            "period_start": _target_column("date", "finance_actual_verifications.period_start"),
            "period_end": _target_column("date", "finance_actual_verifications.period_end"),
            "expected_payload_sha256": _target_column(
                "string", "finance_actual_verifications.expected_payload_sha256"
            ),
            "observed_payload_sha256": _target_column(
                "string", "finance_actual_verifications.observed_payload_sha256"
            ),
            "expected_count": _target_column("number", "finance_actual_verifications.expected_count"),
            "observed_count": _target_column("number", "finance_actual_verifications.observed_count"),
            "expected_amount_sum_minor": _target_column(
                "number", "finance_actual_verifications.expected_amount_sum_minor"
            ),
            "observed_amount_sum_minor": _target_column(
                "number", "finance_actual_verifications.observed_amount_sum_minor"
            ),
            "invariants_passed": _target_column(
                "boolean", "finance_actual_verifications.invariants_passed"
            ),
            "verified_at": _target_column("date", "finance_actual_verifications.verified_at"),
            "source_code": _target_column("string", "finance_reconciliations.source_code"),
            "period_key": _target_column("string", "finance_reconciliations.period_key"),
            "reconciliation_version": _target_column(
                "number", "finance_reconciliations.reconciliation_version"
            ),
            "statement_sha256": _target_column("string", "finance_reconciliations.statement_sha256"),
            "verification_artifact_sha256": _target_column(
                "string", "finance_reconciliations.actual_verification_sha256"
            ),
            "reconciliation_state": _target_column("string", "finance_reconciliations.state"),
            "reconciliation_difference_minor": _target_column(
                "number", "finance_reconciliations.difference_minor"
            ),
            "reconciliation_verified_at": _target_column(
                "date", "finance_reconciliations.verified_at"
            ),
            "verification_artifact_item_id": _resolver_column(
                "string", "actual-verification-v2", "verification_artifact_item_id"
            ),
            "verification_artifact_path": _resolver_column(
                "string", "actual-verification-v2", "verification_artifact_path"
            ),
            "verification_artifact_etag": _resolver_column(
                "string", "actual-verification-v2", "verification_artifact_etag"
            ),
            "verification_artifact_schema_version": _resolver_column(
                "string", "actual-verification-v2", "verification_artifact_schema_version"
            ),
            "verification_artifact_length_bytes": _resolver_column(
                "number", "actual-verification-v2", "verification_artifact_length_bytes"
            ),
        },
    },
    "finance_ai_reviews": {
        "logical_key": ["idempotency_key"],
        "identity_derivations": [
            {
                "source_table": "finance_agent_jobs",
                "strategy": "direct",
                "target_key": ["idempotency_key"],
                "source_fields": ["idempotency_key"],
            }
        ],
        "columns": {
            "idempotency_key": _target_column("string", "finance_agent_jobs.idempotency_key"),
            "policy_id": _target_column("string", "finance_agent_jobs.policy_id"),
            "policy_sha256": _target_column("string", "finance_agent_jobs.policy_sha256"),
            "config_sha256": _target_column("string", "finance_agent_jobs.config_sha256"),
            "output_schema_sha256": _target_column(
                "string", "finance_agent_jobs.output_schema_sha256"
            ),
            "request_sha256": _target_column("string", "finance_agent_jobs.request_sha256"),
            "runner_receipt_id": _target_column("string", "finance_agent_jobs.runner_receipt_id"),
            "proposal_sha256": _target_column("string", "finance_agent_jobs.proposal_sha256"),
            "proposal_artifact_item_id": _target_column(
                "string", "finance_agent_jobs.proposal_artifact_item_id"
            ),
            "proposal_artifact_etag": _target_column(
                "string", "finance_agent_jobs.proposal_artifact_etag"
            ),
            "proposal_artifact_schema": _target_column(
                "string", "finance_agent_jobs.proposal_artifact_schema"
            ),
            "review_state": _target_column("string", "finance_agent_jobs.review_state"),
            "review_decision": _target_column("string", "finance_agent_jobs.review_decision"),
            "reviewed_by_hash": _target_column("string", "finance_agent_jobs.reviewed_by_hash"),
            "reviewed_at": _target_column("date", "finance_agent_jobs.reviewed_at"),
            "terminal_readback_verified": _target_column(
                "boolean", "finance_agent_jobs.terminal_readback_verified"
            ),
            "updated_at": _target_column("date", "finance_agent_jobs.updated_at"),
        },
    },
}


class MatrixError(ValueError):
    """Raised when the checked-in source cannot produce a safe matrix."""


# These are migration decisions, not source facts.  Keeping them explicit
# makes a review see which columns are intentionally retained or removed.
TABLE_METADATA: dict[str, dict[str, Any]] = {
    "finance_source_contracts": {
        "logical_key": ["source_code", "config_version"],
        "target_artifact": "integrations/n8n/generated/application-contract-bundle.json",
        "owner": "finance repository",
        "retention": "ACTIVE rows indefinitely; retired versions 400 days",
        "privacy": "restricted configuration",
        "rationale": "Repository-owned resolver replaces runtime configuration rows.",
    },
    "finance_source_cursors": {
        "logical_key": ["source_code"],
        "target_table": "finance_ingestion_state",
        "owner": "n8n acquisition workflow",
        "retention": "current row indefinitely; superseded receipts 400 days",
        "privacy": "internal operational metadata",
        "rationale": "Keep the latest verified non-Cashback cursor boundary.",
    },
    "finance_acquisition_receipts": {
        "logical_key": ["run_id", "source_code"],
        "target_table": "finance_ingestion_state",
        "owner": "n8n acquisition workflow",
        "retention": "400 days",
        "privacy": "internal operational metadata",
        "rationale": "Fold only the last verified receipt into ingestion state; older runs use n8n history.",
    },
    "finance_archive_receipts": {
        "logical_key": ["source_code", "source_message_id", "source_attachment_id", "source_sha256"],
        "target_table": "finance_documents",
        "owner": "finance evidence workflow",
        "retention": "co-retain with OneDrive evidence; default 7 years pending owner approval",
        "privacy": "restricted evidence identifiers",
        "rationale": "Merge archive identity with document processing while retaining separate state axes.",
    },
    "finance_document_operations": {
        "logical_key": ["source_sha256", "document_profile", "requested_schema_version"],
        "target_table": "finance_documents",
        "owner": "finance evidence workflow",
        "retention": "co-retain with evidence; default 7 years pending owner approval",
        "privacy": "restricted finance and evidence metadata",
        "rationale": "Merge processing state with archive identity without storing content.",
    },
    "finance_pipeline_runs": {
        "logical_key": ["run_id", "workflow_code"],
        "owner": "n8n execution history",
        "retention": "400 days",
        "privacy": "internal telemetry",
        "rationale": "n8n execution history replaces duplicated run telemetry.",
    },
    "finance_actual_outbox": {
        "logical_key": ["imported_id"],
        "target_table": "finance_actual_batches",
        "owner": "finance Actual writer",
        "retention": "co-retain with Actual import evidence; default 7 years pending owner approval",
        "privacy": "restricted finance metadata",
        "rationale": "Retain durable batch and recovery state; leases stay in finance-operations PostgreSQL.",
    },
    "finance_actual_verifications": {
        "logical_key": ["outbox_id", "verification_version"],
        "target_table": "finance_actual_batches",
        "target_artifact": "actual-verification-v2",
        "owner": "finance Actual verifier",
        "retention": "co-retain with Actual import evidence; default 7 years pending owner approval",
        "privacy": "restricted financial aggregates",
        "rationale": "Store immutable verification artifact identity on the batch.",
    },
    "finance_reconciliations": {
        "logical_key": ["source_code", "period_key", "reconciliation_version"],
        "target_table": "finance_actual_batches",
        "target_artifact": "actual-verification-v2",
        "owner": "finance reconciliation workflow",
        "retention": "co-retain with statement evidence; default 7 years pending owner approval",
        "privacy": "restricted reconciliation metadata",
        "rationale": "Keep Actual verification pointers on the batch; Cashback close authority stays in the companion.",
    },
    "finance_config_versions": {
        "logical_key": ["config_name", "version"],
        "target_artifact": "integrations/n8n/generated/application-contract-bundle.json",
        "owner": "finance repository",
        "retention": "ACTIVE indefinitely; retired and revoked 400 days",
        "privacy": "internal integrity metadata",
        "rationale": "Generated repository bundle replaces runtime config fingerprint rows.",
    },
    "finance_provider_circuits": {
        "logical_key": ["provider_code"],
        "owner": "workflow retry policy and external alerting",
        "retention": "current row indefinitely; failure history remains in execution failures",
        "privacy": "internal operational metadata",
        "rationale": "Remove unless a shared circuit breaker is proven by disposable evidence.",
    },
    "finance_execution_failures": {
        "logical_key": ["execution_id"],
        "owner": "n8n execution history and observability",
        "retention": "400 days",
        "privacy": "internal redacted telemetry",
        "rationale": "Use execution history and external observability.",
    },
    "finance_mcp_requests": {
        "logical_key": ["request_id"],
        "owner": "n8n execution evidence and edge logs",
        "retention": "400 days",
        "privacy": "security audit metadata",
        "rationale": "Use execution evidence and access logs.",
    },
    "finance_agent_jobs": {
        "logical_key": ["idempotency_key"],
        "target_table": "finance_ai_reviews",
        "owner": "finance proposal review workflow",
        "retention": "400 days; proposal artifact follows OneDrive evidence policy",
        "privacy": "restricted proposal metadata",
        "rationale": "Retain proposal artifact and human review state; remove execution telemetry.",
    },
    "finance_ai_policy_contracts": {
        "logical_key": ["policy_id", "policy_version"],
        "target_artifact": "integrations/n8n/generated/application-contract-bundle.json",
        "owner": "finance repository",
        "retention": "ACTIVE indefinitely; retired and revoked 400 days",
        "privacy": "internal policy metadata",
        "rationale": "Generated repository resolver replaces runtime policy rows.",
    },
}

REMOVE_TABLES = {
    "finance_pipeline_runs",
    "finance_provider_circuits",
    "finance_execution_failures",
    "finance_mcp_requests",
}

ARTIFACT_PREFIXES = {
    "finance_source_contracts": "source_contracts[]",
    "finance_config_versions": "config_versions[]",
    "finance_ai_policy_contracts": "ai_policy_contracts[]",
}

ACQUISITION_TARGET_FIELDS = {
    "run_id": "committed_run_id",
    "window_start": "last_window_start",
    "pages_fetched": "last_pages_fetched",
    "pagination_exhausted": "last_pagination_exhausted",
    "heartbeat": "last_heartbeat",
    "terminal_state": "last_terminal_state",
    "created_at": "last_receipt_created_at",
}

OUTBOX_TARGET_FIELDS = {
    "outbox_id": "batch_id",
    "imported_id": "idempotency_key",
    "payload_sha256": "delta_sha256",
    "artifact_item_id": "delta_artifact_item_id",
    "artifact_etag": "delta_artifact_etag",
    "artifact_schema_version": "delta_schema_version",
}

AGENT_REVIEW_COLUMNS = {
    "idempotency_key",
    "policy_id",
    "policy_sha256",
    "config_sha256",
    "output_schema_sha256",
    "request_sha256",
    "runner_receipt_id",
    "proposal_sha256",
    "proposal_artifact_item_id",
    "proposal_artifact_etag",
    "proposal_artifact_schema",
    "review_state",
    "review_decision",
    "reviewed_by_hash",
    "reviewed_at",
    "terminal_readback_verified",
    "updated_at",
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize_lf(value: bytes) -> bytes:
    """Normalize CRLF input so hashes and generated bytes are platform-stable."""
    return value.replace(b"\r\n", b"\n")


def normalized_bytes(path: Path) -> bytes:
    """Hash the LF bytes exposed by a normal Git checkout."""
    return normalize_lf(path.read_bytes())


def json_paths() -> list[Path]:
    paths = {path for pattern in SCAN_ROOTS for path in ROOT.glob(pattern)}
    return sorted(path for path in paths if path.is_file())


def source_ref() -> str:
    """Return the latest commit that changed the scanned source corpus."""
    commit = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", *SOURCE_REF_PATHS],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not commit:
        raise MatrixError("unable to resolve a commit for the scanned source corpus")
    return commit


def source_snapshot() -> dict[str, str]:
    lines = []
    for path in json_paths():
        relative = path.relative_to(ROOT).as_posix()
        lines.append(f"{sha256_bytes(normalized_bytes(path))}  {relative}\n".encode())
    corpus = sha256_bytes(b"".join(sorted(lines)))
    return {
        "finance_commit": source_ref(),
        "data_tables_sha256": sha256_bytes(normalized_bytes(DATA_TABLES)),
        "node_scan_corpus_sha256": corpus,
        "source_ref_selection": (
            "Latest git commit touching data-tables.json and the scan-root directories; "
            "generator, schema, matrix output, and tests are excluded."
        ),
        "node_scan_digest_method": (
            "Normalize CRLF to LF, hash each JSON file under scan_roots, sort the complete "
            "hash-and-relative-path lines bytewise, then hash that stream."
        ),
    }


def load_source_tables() -> list[dict[str, Any]]:
    payload = json.loads(normalized_bytes(DATA_TABLES))
    if payload.get("schema_version") != 4:
        raise MatrixError("data-tables.json must use schema_version 4")
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables:
        raise MatrixError("data-tables.json has no tables")
    names = [table.get("name") for table in tables]
    if len(names) != len(set(names)) or any(name not in TABLE_METADATA for name in names):
        raise MatrixError("source table names are not covered by migration policy")
    return tables


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise MatrixError(f"{label} must be a non-empty string")
    return value


def scan_references(source_tables: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    columns_by_table = {table["name"]: table["columns"] for table in source_tables}
    references: dict[str, list[dict[str, Any]]] = {name: [] for name in columns_by_table}
    for path in json_paths():
        payload = json.loads(normalized_bytes(path))
        nodes = payload.get("nodes", [])
        if not isinstance(nodes, list):
            continue
        relative = path.relative_to(ROOT).as_posix()
        for node in nodes:
            if not isinstance(node, dict) or node.get("type") != "n8n-nodes-base.dataTable":
                continue
            name = _string(node.get("name"), f"{relative} Data Table node name")
            parameters = node.get("parameters")
            if not isinstance(parameters, dict):
                raise MatrixError(f"{relative}#{name} has no parameters object")
            operation = _string(parameters.get("operation"), f"{relative}#{name} operation")
            if operation not in OPERATIONS:
                raise MatrixError(f"{relative}#{name} uses unsupported operation {operation!r}")
            if operation == "create":
                table_name = _string(parameters.get("tableName"), f"{relative}#{name} tableName")
                read_columns: list[str] = []
                write_columns: list[str] = []
                filter_keys: list[str] = []
                schema_columns = parameters.get("columns", {}).get("column", [])
                if not isinstance(schema_columns, list):
                    raise MatrixError(f"{relative}#{name} create columns must be a list")
                actual_schema = [(row.get("name"), row.get("type")) for row in schema_columns]
                # W19 creates the four migration targets, while this matrix
                # inventories the fifteen legacy source tables.  Target
                # schema creation is validated here but is intentionally not
                # added to source-table references: target tables are not
                # migration inputs and must not masquerade as source writes.
                if table_name in TARGET_SCHEMAS:
                    expected_schema = [
                        (column, definition["type"])
                        for column, definition in TARGET_SCHEMAS[table_name]["columns"].items()
                    ]
                    if actual_schema != expected_schema:
                        raise MatrixError(
                            f"{relative}#{name} target create schema differs from migration contract"
                        )
                    continue
                expected_schema = list(columns_by_table.get(table_name, {}).items())
                if actual_schema != expected_schema:
                    raise MatrixError(f"{relative}#{name} create schema differs from data-tables.json")
            else:
                table_id = parameters.get("dataTableId")
                if not isinstance(table_id, dict) or table_id.get("mode") != "name":
                    raise MatrixError(f"{relative}#{name} must use a named Data Table reference")
                table_name = _string(table_id.get("value"), f"{relative}#{name} dataTableId")
                if operation == "get":
                    read_columns = ["*"]
                else:
                    read_columns = []
                columns_value = parameters.get("columns", {}).get("value", {})
                if operation in {"insert", "upsert", "update"} and not isinstance(columns_value, dict):
                    raise MatrixError(f"{relative}#{name} write columns must be an object")
                write_columns = sorted(columns_value) if operation in {"insert", "upsert", "update"} else []
                if any(column not in columns_by_table.get(table_name, {}) for column in write_columns):
                    raise MatrixError(f"{relative}#{name} writes an undeclared Data Table column")
                conditions = parameters.get("filters", {}).get("conditions", [])
                if not isinstance(conditions, list):
                    raise MatrixError(f"{relative}#{name} filter conditions must be a list")
                filter_keys = []
                for condition in conditions:
                    if not isinstance(condition, dict):
                        raise MatrixError(f"{relative}#{name} has a malformed filter condition")
                    key = _string(condition.get("keyName"), f"{relative}#{name} filter key")
                    if key not in columns_by_table.get(table_name, {}):
                        raise MatrixError(f"{relative}#{name} filters on an undeclared Data Table column")
                    filter_keys.append(key)
            if table_name not in references:
                raise MatrixError(f"{relative}#{name} references unknown Data Table {table_name!r}")
            references[table_name].append(
                {
                    "table": table_name,
                    "file": relative,
                    "node": name,
                    "operation": operation,
                    "read_columns": read_columns,
                    "write_columns": write_columns,
                    "filter_keys": filter_keys,
                }
            )
    for table_references in references.values():
        table_references.sort(key=lambda row: (row["file"], row["node"]))
    return references


def ref_key(reference: dict[str, Any]) -> str:
    return f"{reference['file']}#{reference['node']}"


def target_for(table_name: str, column: str) -> dict[str, Any]:
    metadata = TABLE_METADATA[table_name]
    if table_name in REMOVE_TABLES:
        return {"disposition": "remove", "target_table": None, "target_artifact": None, "target_field": None}
    if table_name in ARTIFACT_PREFIXES:
        return {
            "disposition": "transform",
            "target_table": None,
            "target_artifact": metadata["target_artifact"],
            "target_field": f"{ARTIFACT_PREFIXES[table_name]}.{column}",
        }
    if table_name == "finance_source_cursors":
        return {"disposition": "keep", "target_table": "finance_ingestion_state", "target_artifact": None, "target_field": column}
    if table_name == "finance_acquisition_receipts":
        if column in {"cursor_commit_eligible", "immutable_inventory_json"}:
            return {"disposition": "remove", "target_table": None, "target_artifact": None, "target_field": None}
        target_field = ACQUISITION_TARGET_FIELDS.get(column, column)
        return {"disposition": "keep" if target_field == column else "transform", "target_table": "finance_ingestion_state", "target_artifact": None, "target_field": target_field}
    if table_name == "finance_document_operations" and column == "document_id":
        return {
            "disposition": "transform",
            "target_table": None,
            "target_artifact": "document-identity-aliases-v1",
            "target_field": "legacy_to_canonical.document_id",
        }
    if table_name in {"finance_archive_receipts", "finance_document_operations"}:
        target_field = "archive_verified_at" if column == "verified_at" else column
        return {"disposition": "keep" if target_field == column else "transform", "target_table": "finance_documents", "target_artifact": None, "target_field": target_field}
    if table_name == "finance_actual_outbox":
        if column in {"lease_owner", "lease_fence", "lease_expires_at"}:
            return {"disposition": "remove", "target_table": None, "target_artifact": None, "target_field": None}
        target_field = OUTBOX_TARGET_FIELDS.get(column, column)
        return {"disposition": "keep" if target_field == column else "transform", "target_table": "finance_actual_batches", "target_artifact": None, "target_field": target_field}
    if table_name == "finance_actual_verifications":
        if column in {"expected_account_balance", "observed_account_balance"}:
            return {"disposition": "remove", "target_table": None, "target_artifact": None, "target_field": None}
        if column == "outbox_id":
            return {"disposition": "transform", "target_table": "finance_actual_batches", "target_artifact": None, "target_field": "batch_id"}
        if column == "verification_version":
            return {"disposition": "keep", "target_table": "finance_actual_batches", "target_artifact": "actual-verification-v2", "target_field": column}
        return {
            "disposition": "keep",
            "target_table": "finance_actual_batches",
            "target_artifact": "actual-verification-v2",
            "target_field": column,
        }
    if table_name == "finance_reconciliations":
        if column in {"source_code", "period_key"}:
            return {"disposition": "keep", "target_table": "finance_actual_batches", "target_artifact": None, "target_field": column}
        if column == "cashback_close_id":
            return {"disposition": "transform", "target_table": None, "target_artifact": "cashback-companion", "target_field": column}
        reconciliation_fields = {
            "actual_verification_sha256": "verification_artifact_sha256",
            "state": "reconciliation_state",
            "difference_minor": "reconciliation_difference_minor",
            "verified_at": "reconciliation_verified_at",
        }
        target_field = reconciliation_fields.get(column, column)
        return {
            "disposition": "transform" if target_field != column else "keep",
            "target_table": "finance_actual_batches",
            "target_artifact": "actual-verification-v2",
            "target_field": target_field,
        }
    if table_name == "finance_agent_jobs":
        if column not in AGENT_REVIEW_COLUMNS:
            return {"disposition": "remove", "target_table": None, "target_artifact": None, "target_field": None}
        return {"disposition": "keep", "target_table": "finance_ai_reviews", "target_artifact": None, "target_field": column}
    raise MatrixError(f"no migration policy for {table_name}.{column}")


def column_matrix(table: dict[str, Any], references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metadata = TABLE_METADATA[table["name"]]
    columns = table["columns"]
    result = []
    for source_column, source_type in columns.items():
        target = target_for(table["name"], source_column)
        producer_nodes = sorted(
            ref_key(reference)
            for reference in references
            if reference["operation"] == "create" or source_column in reference["write_columns"]
        )
        consumer_nodes = sorted(
            ref_key(reference)
            for reference in references
            if "*" in reference["read_columns"]
            or source_column in reference["read_columns"]
            or source_column in reference["filter_keys"]
        )
        constraints = [f"type:{source_type}"]
        if source_column in metadata["logical_key"]:
            constraints.append("logical-key-member")
        row = {
            "source_column": source_column,
            "source_type": source_type,
            **target,
            "constraints": constraints,
            "owner": metadata["owner"],
            "retention": metadata["retention"],
            "privacy": metadata["privacy"],
            "producer_nodes": producer_nodes,
            "consumer_nodes": consumer_nodes,
            "rationale": metadata["rationale"],
        }
        result.append(row)
    return result


def target_schema_payload() -> dict[str, dict[str, Any]]:
    """Return the checked-in target contract without exposing mutable state."""
    return json.loads(json.dumps(TARGET_SCHEMAS))


def validate_identity_derivations(
    tables: list[dict[str, Any]], target_schemas: dict[str, dict[str, Any]]
) -> None:
    """Ensure every retained source has a deterministic target-row identity."""

    source_columns: dict[str, dict[str, str]] = {}
    for table in tables:
        source_name = table.get("name", table.get("source_table"))
        columns = table["columns"]
        if isinstance(columns, dict):
            source_columns[source_name] = columns
        else:
            source_columns[source_name] = {
                column["source_column"]: column["source_type"]
                for column in columns
            }
    target_sources: dict[str, set[str]] = {target: set() for target in target_schemas}
    for table in tables:
        source_name = table.get("name", table.get("source_table"))
        target = TABLE_METADATA[source_name].get("target_table")
        if target in target_sources:
            target_sources[target].add(source_name)

    for target, schema in target_schemas.items():
        logical_key = schema.get("logical_key")
        derivations = schema.get("identity_derivations")
        if not isinstance(logical_key, list) or not logical_key:
            raise MatrixError(f"{target} must declare a non-empty logical key")
        if not isinstance(derivations, list) or not derivations:
            raise MatrixError(f"{target} must declare identity derivations")
        seen_sources: set[str] = set()
        document_identity_contract: tuple[str, str, str, str, str, tuple[str, ...]] | None = None
        for derivation in derivations:
            if not isinstance(derivation, dict):
                raise MatrixError(f"{target} has a malformed identity derivation")
            source_table = derivation.get("source_table")
            if source_table not in source_columns:
                raise MatrixError(f"{target} identity source is unknown: {source_table!r}")
            if source_table in seen_sources:
                raise MatrixError(f"{target} has duplicate identity derivation for {source_table}")
            seen_sources.add(source_table)
            if source_table not in target_sources[target]:
                raise MatrixError(
                    f"{target} identity source {source_table} does not belong to the target"
                )
            if derivation.get("target_key") != logical_key:
                raise MatrixError(f"{target}.{source_table} identity does not produce the target logical key")
            strategy = derivation.get("strategy")
            if target == "finance_documents" and strategy != "versioned_length_prefixed_sha256":
                raise MatrixError(
                    f"{target}.{source_table} must use the approved versioned document identity tuple"
                )
            if strategy == "direct":
                source_fields = derivation.get("source_fields")
                if not isinstance(source_fields, list) or len(source_fields) != len(logical_key):
                    raise MatrixError(f"{target}.{source_table} direct identity fields are incomplete")
                if any(field not in source_columns[source_table] for field in source_fields):
                    raise MatrixError(f"{target}.{source_table} direct identity references an unknown source field")
            elif strategy == "versioned_length_prefixed_sha256":
                source_fields = derivation.get("source_fields")
                if (
                    not isinstance(source_fields, list)
                    or len(source_fields) < 2
                    or len(set(source_fields)) != len(source_fields)
                ):
                    raise MatrixError(
                        f"{target}.{source_table} versioned hash needs at least two source fields"
                    )
                if logical_key != ["document_id"]:
                    raise MatrixError(
                        f"{target}.{source_table} versioned hash may only produce document_id"
                    )
                if any(source_columns[source_table].get(field) != "string" for field in source_fields):
                    raise MatrixError(
                        f"{target}.{source_table} versioned hash requires string source fields"
                    )
                if not isinstance(derivation.get("version"), str) or not derivation["version"]:
                    raise MatrixError(
                        f"{target}.{source_table} versioned hash needs a non-empty version"
                    )
                if derivation.get("length_prefix") != "uint64_be":
                    raise MatrixError(
                        f"{target}.{source_table} versioned hash must use uint64_be length prefixes"
                    )
                if derivation.get("tuple_encoding") != "versioned_length_prefixed_binary":
                    raise MatrixError(
                        f"{target}.{source_table} versioned hash must use binary length-prefixed tuples"
                    )
                if derivation.get("digest_encoding") != "base64url_unpadded":
                    raise MatrixError(
                        f"{target}.{source_table} document identity must use unpadded base64url SHA-256"
                    )
                if "separator" in derivation or "prefix" in derivation:
                    raise MatrixError(
                        f"{target}.{source_table} versioned hash must not use delimiters or raw prefixes"
                    )
                identity_kind = derivation.get("identity_kind")
                version = derivation.get("version")
                length_prefix = derivation.get("length_prefix")
                if identity_kind not in DOCUMENT_IDENTITY_FIELDS:
                    raise MatrixError(
                        f"{target}.{source_table} versioned hash has an unsupported identity_kind"
                    )
                if target == "finance_documents" and tuple(source_fields) != DOCUMENT_IDENTITY_FIELDS[identity_kind]:
                    raise MatrixError(
                        f"{target}.{source_table} identity fields do not match {identity_kind}"
                    )
                contract = (
                    identity_kind,
                    version,
                    length_prefix,
                    derivation["tuple_encoding"],
                    derivation["digest_encoding"],
                    tuple(source_fields),
                )
                if target == "finance_documents":
                    if document_identity_contract is None:
                        document_identity_contract = contract
                    elif contract != document_identity_contract:
                        raise MatrixError(
                            f"{target}.{source_table} identity tuple differs from the approved document contract"
                        )
                aliases = derivation.get("alias_fields")
                if (
                    not isinstance(aliases, list)
                    or any(not isinstance(alias, str) for alias in aliases)
                    or len(set(aliases)) != len(aliases)
                ):
                    raise MatrixError(f"{target}.{source_table} alias fields are malformed")
                if any(alias not in source_columns[source_table] for alias in aliases):
                    raise MatrixError(f"{target}.{source_table} alias field is unknown")
                legacy_adapter = derivation.get("legacy_to_canonical")
                if aliases:
                    if (
                        not isinstance(legacy_adapter, dict)
                        or legacy_adapter.get("adapter") != "document-identity-alias-v1"
                        or legacy_adapter.get("legacy_fields") != aliases
                        or legacy_adapter.get("canonical_target") != "finance_documents.document_id"
                    ):
                        raise MatrixError(
                            f"{target}.{source_table} needs the approved legacy-to-canonical adapter"
                        )
                elif legacy_adapter is not None:
                    raise MatrixError(
                        f"{target}.{source_table} cannot declare a legacy adapter without aliases"
                    )
                fallback = derivation.get("fallback_identity")
                if fallback is not None:
                    fallback_fields = fallback.get("source_fields") if isinstance(fallback, dict) else None
                    if (
                        not isinstance(fallback, dict)
                        or fallback.get("identity_kind") != "PROCESSING_ONLY"
                        or not isinstance(fallback_fields, list)
                        or not fallback_fields
                        or len(set(fallback_fields)) != len(fallback_fields)
                        or tuple(fallback_fields) != DOCUMENT_IDENTITY_FIELDS["PROCESSING_ONLY"]
                        or any(field not in source_columns[source_table] for field in fallback_fields)
                    ):
                        raise MatrixError(
                            f"{target}.{source_table} fallback identity is invalid"
                        )
            elif strategy == "join":
                steps = derivation.get("join_steps")
                terminal_fields = derivation.get("terminal_fields")
                if (
                    not isinstance(steps, list)
                    or not steps
                    or not isinstance(terminal_fields, list)
                    or derivation.get("cardinality") != "exactly_one"
                ):
                    raise MatrixError(f"{target}.{source_table} join identity is incomplete")
                join_key = derivation.get("join_key")
                if join_key is not None:
                    if not isinstance(join_key, dict):
                        raise MatrixError(f"{target}.{source_table} join key is malformed")
                    source_fields = join_key.get("source_fields")
                    target_fields = join_key.get("target_fields")
                    target_columns = schema["columns"]
                    if (
                        not isinstance(source_fields, list)
                        or not source_fields
                        or not isinstance(target_fields, list)
                        or len(source_fields) != len(target_fields)
                        or len(set(source_fields)) != len(source_fields)
                        or len(set(target_fields)) != len(target_fields)
                        or any(field not in source_columns[source_table] for field in source_fields)
                        or any(field not in target_columns for field in target_fields)
                    ):
                        raise MatrixError(f"{target}.{source_table} join key fields are invalid")
                    for source_field, target_field in zip(source_fields, target_fields, strict=True):
                        source_type = source_columns[source_table][source_field]
                        target_type = target_columns[target_field]["type"]
                        if source_type != target_type:
                            raise MatrixError(
                                f"{target}.{source_table} join key type {source_type!r} cannot populate "
                                f"{target_field} ({target_type!r})"
                            )
                current_table = source_table
                for step in steps:
                    if not isinstance(step, dict) or step.get("table") not in source_columns:
                        raise MatrixError(f"{target}.{source_table} join identity references an unknown table")
                    left_fields = step.get("left_fields")
                    right_fields = step.get("right_fields")
                    joined_table = step["table"]
                    resolver_right_fields = {
                        "verification_artifact_sha256"
                    }
                    if (
                        not isinstance(left_fields, list)
                        or not isinstance(right_fields, list)
                        or len(left_fields) != len(right_fields)
                        or not left_fields
                        or any(field not in source_columns[current_table] for field in left_fields)
                        or any(
                            field not in source_columns[joined_table]
                            and not (
                                joined_table == "finance_actual_verifications"
                                and field in resolver_right_fields
                            )
                            for field in right_fields
                        )
                    ):
                        raise MatrixError(f"{target}.{source_table} join identity has invalid join fields")
                    for left_field, right_field in zip(left_fields, right_fields, strict=True):
                        left_type = source_columns[current_table][left_field]
                        # Verification artifacts are resolver-owned target
                        # fields, not columns in the legacy verification row.
                        if (
                            joined_table == "finance_actual_verifications"
                            and right_field == "verification_artifact_sha256"
                        ):
                            right_type = "string"
                        else:
                            right_type = source_columns[joined_table][right_field]
                        if left_type != right_type:
                            raise MatrixError(
                                f"{target}.{source_table} join field type {left_type!r} cannot match "
                                f"{joined_table}.{right_field} ({right_type!r})"
                            )
                    current_table = joined_table
                if any(field not in source_columns[current_table] for field in terminal_fields):
                    raise MatrixError(f"{target}.{source_table} join identity terminal field is unknown")
                if len(terminal_fields) != len(logical_key):
                    raise MatrixError(f"{target}.{source_table} join identity terminal fields are incomplete")
            else:
                raise MatrixError(f"{target}.{source_table} uses unsupported identity strategy {strategy!r}")
        if seen_sources != target_sources[target]:
            raise MatrixError(
                f"{target} identity derivations do not cover every source: "
                f"expected {sorted(target_sources[target])}, observed {sorted(seen_sources)}"
            )


def validate_target_mappings(
    tables: list[dict[str, Any]], target_schemas: dict[str, dict[str, Any]]
) -> None:
    """Reject unknown targets, type drift, and undeclared source coalescing."""
    if set(target_schemas) != set(TARGETS):
        raise MatrixError("target schemas must contain exactly the four approved targets")
    if target_schemas != TARGET_SCHEMAS:
        raise MatrixError("target schema payload differs from the generator contract")
    validate_identity_derivations(tables, target_schemas)

    observed: dict[tuple[str, str], list[str]] = {}
    source_binding_names = {
        f"{table['source_table']}.{column['source_column']}"
        for table in tables
        for column in table["columns"]
    }
    resolver_binding_prefixes = ("inventory-v1.", "actual-verification-v2.")
    for target, schema in target_schemas.items():
        for field, spec in schema["columns"].items():
            for binding in spec["source_bindings"]:
                if binding not in source_binding_names:
                    if not binding.startswith(resolver_binding_prefixes):
                        raise MatrixError(
                            f"{target}.{field} has an unknown source or resolver binding {binding!r}"
                        )
                    observed.setdefault((target, field), []).append(binding)
    for table in tables:
        source_table = table["source_table"]
        for column in table["columns"]:
            target_table = column["target_table"]
            target_field = column["target_field"]
            if target_table is None:
                if target_field is not None and column["target_artifact"] is None:
                    raise MatrixError(f"{source_table}.{column['source_column']} has a field without a target table")
                continue
            if target_table not in TARGET_SCHEMAS or not isinstance(target_field, str):
                raise MatrixError(
                    f"{source_table}.{column['source_column']} points to an undeclared target"
                )
            target_columns = TARGET_SCHEMAS[target_table]["columns"]
            expected = target_columns.get(target_field)
            if expected is None:
                raise MatrixError(
                    f"{source_table}.{column['source_column']} points to undeclared "
                    f"{target_table}.{target_field}"
                )
            if column["source_type"] != expected["type"]:
                raise MatrixError(
                    f"{source_table}.{column['source_column']} type {column['source_type']!r} "
                    f"cannot populate {target_table}.{target_field} ({expected['type']!r})"
                )
            observed.setdefault((target_table, target_field), []).append(
                f"{source_table}.{column['source_column']}"
            )

    # A document_id is generated from its identity tuple.  Its tuple inputs
    # are canonical bindings, while a legacy processing document_id is kept
    # only by the explicit alias adapter above and must not populate this key.
    document_schema = target_schemas["finance_documents"]
    for derivation in document_schema["identity_derivations"]:
        if derivation["strategy"] != "versioned_length_prefixed_sha256":
            continue
        source_table = derivation["source_table"]
        for source_field in derivation["source_fields"]:
            observed.setdefault(("finance_documents", "document_id"), []).append(
                f"{source_table}.{source_field}"
            )
        fallback = derivation.get("fallback_identity")
        if fallback is not None:
            for source_field in fallback["source_fields"]:
                observed.setdefault(("finance_documents", "document_id"), []).append(
                    f"{source_table}.{source_field}"
                )
    generated_document_sources = ("finance_documents", "document_id")
    observed[generated_document_sources] = list(
        dict.fromkeys(observed.get(generated_document_sources, []))
    )

    for target in TARGETS:
        declared_columns = TARGET_SCHEMAS[target]["columns"]
        observed_fields = {
            field for table_name, field in observed if table_name == target
        }
        if observed_fields != set(declared_columns):
            raise MatrixError(
                f"{target} fields differ from its exact target schema: "
                f"expected {sorted(declared_columns)}, observed {sorted(observed_fields)}"
            )
        for field, spec in declared_columns.items():
            actual_sources = observed.get((target, field), [])
            if len(actual_sources) != len(set(actual_sources)):
                raise MatrixError(f"{target}.{field} receives a source column more than once")
            if set(actual_sources) != set(spec["source_bindings"]):
                raise MatrixError(
                    f"{target}.{field} source bindings differ from its explicit merge policy"
                )


def build_matrix() -> dict[str, Any]:
    source_tables = load_source_tables()
    references = scan_references(source_tables)
    tables = []
    dispositions: dict[str, int] = {"keep": 0, "transform": 0, "remove": 0}
    source_columns = 0
    consumer_edges = producer_edges = write_edges = 0
    filter_only_columns = filter_only_edges = 0
    for table in source_tables:
        name = table["name"]
        cols = column_matrix(table, references[name])
        source_columns += len(cols)
        for column in cols:
            dispositions[column["disposition"]] += 1
            consumer_edges += len(column["consumer_nodes"])
            producer_edges += len(column["producer_nodes"])
            filter_only = [
                reference
                for reference in references[name]
                if column["source_column"] in reference["filter_keys"]
                and "*" not in reference["read_columns"]
                and column["source_column"] not in reference["read_columns"]
            ]
            if filter_only:
                filter_only_columns += 1
                filter_only_edges += len(filter_only)
        for reference in references[name]:
            write_edges += len(reference["write_columns"])
        metadata = TABLE_METADATA[name]
        table_row = {
            "source_table": name,
            "source_columns_count": len(table["columns"]),
            "logical_key": list(metadata["logical_key"]),
            "allowed_states": list(table.get("allowed_states", [])),
            "allowed_review_states": list(table.get("allowed_review_states", [])),
            "target_table": metadata.get("target_table"),
            "target_artifact": metadata.get("target_artifact"),
            "owner": metadata["owner"],
            "retention": metadata["retention"],
            "privacy": metadata["privacy"],
            "rationale": metadata["rationale"],
            "node_references": references[name],
            "columns": cols,
        }
        tables.append(table_row)
    matrix = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "schema_version": 1,
        "artifact_role": "GENERATED_SOURCE_MATRIX",
        "source_contract": "integrations/n8n/data-tables.json@schema_version=4",
        "source_snapshot": source_snapshot(),
        "scan_roots": list(SCAN_ROOTS),
        "targets": list(TARGETS),
        "target_schemas": target_schema_payload(),
        "invariants": {
            "source_tables": len(source_tables),
            "source_columns": source_columns,
            "node_references": sum(len(rows) for rows in references.values()),
            "consumer_node_edges": consumer_edges,
            "filter_only_consumer_columns": filter_only_columns,
            "filter_only_consumer_edges": filter_only_edges,
            "write_reference_edges": write_edges,
            "producer_node_edges": producer_edges,
            "every_source_column_has_one_disposition": all(value >= 0 for value in dispositions.values()),
            "node_operations": list(OPERATIONS),
            "read_columns_star_means_full_matching_row": True,
            "filter_keys_are_exact_node_parameters": True,
            "consumer_nodes_equal_read_and_filter_union": True,
            "producer_nodes_equal_write_mapping_plus_schema_creation": True,
            "deletion_requires_second_run_noop_and_reverse_rehearsal": True,
            "dispositions": dispositions,
        },
        "tables": tables,
    }
    validate_target_mappings(tables, matrix["target_schemas"])
    return matrix


def validate_matrix(matrix: dict[str, Any]) -> None:
    schema = json.loads(normalized_bytes(SCHEMA))
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(matrix), key=lambda error: list(error.path))
    if errors:
        raise MatrixError("generated matrix schema error: " + "; ".join(error.message for error in errors[:3]))
    actual_snapshot = source_snapshot()
    if matrix["source_snapshot"] != actual_snapshot:
        raise MatrixError("generated matrix source snapshot is stale")
    expected = build_matrix()
    if matrix != expected:
        raise MatrixError("generated matrix does not match the checked-in source corpus")


def render(matrix: dict[str, Any]) -> str:
    rendered = json.dumps(matrix, ensure_ascii=False, indent=2) + "\n"
    return normalize_lf(rendered.encode("utf-8")).decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="write the generated matrix")
    mode.add_argument("--check", action="store_true", help="verify the committed matrix")
    args = parser.parse_args()
    matrix = build_matrix()
    validate_matrix(matrix)
    rendered = render(matrix)
    if args.write:
        OUTPUT.write_text(rendered, encoding="utf-8", newline="\n")
        return 0
    if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != rendered:
        raise SystemExit(f"generated Data Table migration matrix drift: {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
