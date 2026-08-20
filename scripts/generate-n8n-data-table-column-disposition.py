#!/usr/bin/env python3
"""Generate the column-exact n8n Data Table minimization audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finance_tracker.project_backlog import derive_n8n_data_table_column_access


CONTRACT_PATH = ROOT / "integrations" / "n8n" / "data-tables.json"
MATRIX_PATH = ROOT / "docs" / "project-audit" / "n8n-data-table-column-disposition-2026-08-20.json"
WORKFLOWS_PATH = ROOT / "integrations" / "n8n" / "workflows"


TARGET_COLUMNS = {
    "finance_ingestion_state": {
        "source_code": ("string", ["finance_source_cursors.source_code", "finance_acquisition_receipts.source_code"]),
        "cursor_value": ("date", ["finance_source_cursors.cursor_value"]),
        "cursor_version": ("number", ["finance_source_cursors.cursor_version"]),
        "committed_run_id": ("string", ["finance_source_cursors.committed_run_id", "finance_acquisition_receipts.run_id"]),
        "run_upper_bound": ("date", ["finance_source_cursors.run_upper_bound", "finance_acquisition_receipts.run_upper_bound"]),
        "scanned_count": ("number", ["finance_source_cursors.scanned_count", "finance_acquisition_receipts.scanned_count"]),
        "matched_count": ("number", ["finance_source_cursors.matched_count", "finance_acquisition_receipts.matched_count"]),
        "downstream_receipt_sha256": ("string", ["finance_acquisition_receipts.downstream_receipt_sha256"]),
        "updated_at": ("date", ["finance_source_cursors.updated_at", "finance_acquisition_receipts.updated_at"]),
    },
    "finance_documents": {
        "document_id": ("string", ["finance_document_operations.document_id", "finance_archive_receipts.archive_receipt_id"]),
        "source_code": ("string", ["finance_document_operations.source_code", "finance_archive_receipts.source_code"]),
        "source_message_id": ("string", ["finance_document_operations.source_message_id", "finance_archive_receipts.source_message_id"]),
        "source_attachment_id": ("string", ["finance_document_operations.source_attachment_id", "finance_archive_receipts.source_attachment_id"]),
        "source_sha256": ("string", ["finance_document_operations.source_sha256", "finance_archive_receipts.source_sha256"]),
        "onedrive_item_id": ("string", ["finance_document_operations.onedrive_item_id", "finance_archive_receipts.onedrive_item_id"]),
        "onedrive_etag": ("string", ["finance_archive_receipts.onedrive_etag"]),
        "document_profile": ("string", ["finance_document_operations.document_profile"]),
        "requested_schema_version": ("string", ["finance_document_operations.requested_schema_version"]),
        "archive_state": ("string", ["finance_archive_receipts.archive_state"]),
        "processing_state": ("string", ["finance_document_operations.state"]),
        "attempt_count": ("number", ["finance_document_operations.attempt_count"]),
        "parser_version": ("string", ["finance_document_operations.parser_version"]),
        "output_sha256": ("string", ["finance_document_operations.output_sha256"]),
        "error_class": ("string", ["finance_document_operations.error_class"]),
        "actual_file_id": ("string", ["finance_document_operations.actual_file_id"]),
        "account_id": ("string", ["finance_document_operations.account_id"]),
        "period_key": ("string", ["finance_document_operations.period_key"]),
        "verified_at": ("date", ["finance_archive_receipts.verified_at"]),
        "updated_at": ("date", ["finance_document_operations.updated_at", "finance_archive_receipts.updated_at"]),
    },
    "finance_actual_batches": {
        "batch_id": ("string", ["finance_actual_outbox.outbox_id"]),
        "run_id": ("string", ["finance_actual_outbox.run_id"]),
        "imported_id": ("string", ["finance_actual_outbox.imported_id"]),
        "actual_file_id": ("string", ["finance_actual_outbox.actual_file_id", "finance_actual_verifications.actual_file_id"]),
        "account_id": ("string", ["finance_actual_verifications.account_id"]),
        "period_start": ("date", ["finance_actual_verifications.period_start"]),
        "period_end": ("date", ["finance_actual_verifications.period_end"]),
        "payload_sha256": ("string", ["finance_actual_outbox.payload_sha256"]),
        "artifact_item_id": ("string", ["finance_actual_outbox.artifact_item_id"]),
        "artifact_etag": ("string", ["finance_actual_outbox.artifact_etag"]),
        "artifact_schema_version": ("string", ["finance_actual_outbox.artifact_schema_version"]),
        "config_sha256": ("string", ["finance_actual_outbox.config_version", "finance_config_versions.content_sha256"]),
        "parser_version": ("string", ["finance_actual_outbox.parser_version"]),
        "state": ("string", ["finance_actual_outbox.state"]),
        "actual_transaction_id": ("string", ["finance_actual_outbox.actual_transaction_id"]),
        "verification_artifact_item_id": ("string", ["workflow:WF20.Read Back Immutable Verification Artifact.id"]),
        "verification_artifact_sha256": ("string", ["finance_actual_verifications.expected_payload_sha256", "finance_actual_verifications.observed_payload_sha256", "finance_actual_verifications.invariants_passed", "workflow:WF20.SHA-256 Immutable Verification Artifact Readback.value"]),
        "updated_at": ("date", ["finance_actual_outbox.updated_at", "finance_actual_verifications.verified_at"]),
    },
    "finance_ai_reviews": {
        "review_id": ("string", ["finance_agent_jobs.job_id"]),
        "idempotency_key": ("string", ["finance_agent_jobs.idempotency_key"]),
        "operation_code": ("string", ["finance_agent_jobs.operation_code"]),
        "proposal_artifact_item_id": ("string", ["finance_agent_jobs.proposal_artifact_item_id"]),
        "proposal_sha256": ("string", ["finance_agent_jobs.proposal_sha256"]),
        "review_state": ("string", ["finance_agent_jobs.review_state"]),
        "review_decision": ("string", ["finance_agent_jobs.review_decision"]),
        "reviewed_at": ("date", ["finance_agent_jobs.reviewed_at"]),
        "updated_at": ("date", ["finance_agent_jobs.updated_at"]),
    },
}


TARGET_OWNERS = {
    "finance_ingestion_state": "n8n durable acquisition boundary",
    "finance_documents": "n8n document state with immutable evidence in OneDrive",
    "finance_actual_batches": "n8n batch state; Actual remains posted-ledger owner",
    "finance_ai_reviews": "n8n human review state only",
}


PLANNED_BINDINGS = {
    "finance_ingestion_state": {
        "producer": ("WF12", "planned-n8n011-wf12-ingestion-upsert", "Upsert finance_ingestion_state After Durable Receipt", "upsert"),
        "consumer": ("WF12", "planned-n8n011-wf12-ingestion-read", "Read finance_ingestion_state", "get"),
    },
    "finance_documents": {
        "producer": ("WF13", "planned-n8n011-wf13-document-upsert", "Upsert finance_documents Processing State", "upsert"),
        "consumer": ("WF11", "planned-n8n011-wf11-document-read", "Read finance_documents Durable Record", "get"),
    },
    "finance_actual_batches": {
        "producer": ("WF20", "planned-n8n011-wf20-batch-upsert", "Persist Verification Artifact Identity on finance_actual_batches", "upsert"),
        "consumer": ("WF17", "planned-n8n011-wf17-batch-read", "Read Nonterminal finance_actual_batches", "get"),
    },
    "finance_ai_reviews": {
        "producer": ("WF09", "planned-n8n011-wf09-review-upsert", "Upsert finance_ai_reviews Proposal", "upsert"),
        "consumer": ("WF09", "planned-n8n011-wf09-review-read", "Read finance_ai_reviews Proposal", "get"),
    },
}

WORKFLOW_FILES = {
    "WF09": "09-ai-proposal.json",
    "WF11": "11-interactive-artifact-handoff.json",
    "WF12": "12-outlook-message-sweep.json",
    "WF13": "13-document-extraction-request.json",
    "WF17": "17-actual-outbox-recovery.json",
    "WF20": "20-actual-outbox-apply.json",
}


def constraints(table: str, column: str, value_type: str) -> list[str]:
    result = ["not null"]
    if column in {"source_attachment_id", "onedrive_etag", "parser_version", "output_sha256", "error_class", "actual_transaction_id", "review_decision", "reviewed_at"}:
        result = ["nullable until its named lifecycle stage"]
    if column.endswith("sha256"):
        result.append("lowercase SHA-256 hex when present")
    if value_type == "number":
        result.append("integer greater than or equal to zero")
    if column in {"source_code", "document_id", "batch_id", "review_id", "idempotency_key", "imported_id", "account_id"}:
        result.append("non-empty canonical identifier")
    if column == "source_code" and table == "finance_ingestion_state":
        result.append("logical primary key")
    if column == "document_id":
        result.append("logical primary key; source identity plus source_sha256 is unique")
    if column == "batch_id":
        result.append("logical primary key; imported_id is unique")
    if column == "review_id":
        result.append("logical primary key; idempotency_key is unique")
    if column == "period_end":
        result.append("greater than or equal to period_start")
    if column == "archive_state":
        result.append("archive lifecycle enum independent of processing_state")
    if column == "processing_state":
        result.append("document-processing lifecycle enum independent of archive_state")
    if column == "state" and table == "finance_actual_batches":
        result.append("monotonic PREPARED to ACTUAL_OBSERVED to VERIFIED to COMMITTED transition")
    if column == "review_state":
        result.append("review-only lifecycle; never authorizes an Actual write")
    return result


def _binding_projection(entry: dict[str, str]) -> dict[str, str]:
    return {
        "workflow_code": entry["workflow_code"],
        "workflow_path": entry["workflow_path"],
        "data_table_node_id": entry["data_table_node_id"],
        "data_table_node_name": entry["data_table_node_name"],
        "operation": entry["operation"],
        "binding_kind": entry["binding_kind"],
        "binding_path": entry["binding_path"],
        "binding_sha256": entry["binding_sha256"],
        "status": "current source binding",
    }


def _planned_binding(table: str, kind: str) -> dict[str, str]:
    code, node_id, node_name, operation = PLANNED_BINDINGS[table][kind]
    return {
        "workflow_code": code,
        "workflow_path": f"integrations/n8n/workflows/{WORKFLOW_FILES[code]}",
        "data_table_node_id": node_id,
        "data_table_node_name": node_name,
        "operation": operation,
        "binding_kind": f"planned_{kind}",
        "binding_path": "design-only; workflow mutation is gated by N8N-011",
        "binding_sha256": "0" * 64,
        "status": "planned target binding; not implemented",
    }


def _unique_bindings(bindings: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    result = []
    for binding in bindings:
        key = json.dumps(binding, sort_keys=True, separators=(",", ":"))
        if key not in seen:
            seen.add(key)
            result.append(binding)
    return result


def build() -> dict:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    current = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    access = derive_n8n_data_table_column_access(WORKFLOWS_PATH, contract)
    current_rows = {(row["table"], row["column"]): row for row in current["rows"]}

    rows = []
    for table in contract["tables"]:
        for column in table["columns"]:
            row = dict(current_rows[(table["name"], column)])
            row["workflow_access"] = access[(table["name"], column)]
            kinds = sorted({entry["binding_kind"] for entry in row["workflow_access"]})
            row["rationale"] = (
                f"{table['name']}.{column} is evaluated independently. Its exact current bindings are "
                f"{', '.join(kinds)} only; no access is inferred from another column in the same table. "
                f"The {row['disposition']} disposition follows the stated owner, invariant, retention and privacy boundary."
            )
            rows.append(row)

    target_schemas = {}
    for target_table, columns in TARGET_COLUMNS.items():
        target_rows = []
        for column, (value_type, lineage_values) in columns.items():
            source_bindings = []
            for lineage in lineage_values:
                if lineage.startswith("workflow:"):
                    continue
                source_table, source_column = lineage.split(".", 1)
                source_bindings.extend(access[(source_table, source_column)])
            producers = [
                _binding_projection(entry)
                for entry in source_bindings
                if entry["access_kind"] == "write"
            ]
            consumers = [
                _binding_projection(entry)
                for entry in source_bindings
                if entry["access_kind"] == "read"
            ]
            producers = _unique_bindings(producers + [_planned_binding(target_table, "producer")])
            consumers = _unique_bindings(consumers + [_planned_binding(target_table, "consumer")])
            transformations = []
            for lineage in lineage_values:
                transformation = "copy with type validation"
                if column in {"batch_id", "review_id", "processing_state"}:
                    transformation = f"rename to {column} with type validation"
                elif column == "document_id" and lineage.endswith("archive_receipt_id"):
                    transformation = "derive document_id from immutable source identity and source_sha256"
                elif column == "config_sha256":
                    transformation = "resolve config_version to the content-addressed Git config SHA-256"
                elif column == "verification_artifact_item_id":
                    transformation = "take the immutable OneDrive upload/readback item ID before terminal batch transition"
                elif column == "verification_artifact_sha256":
                    transformation = "canonicalize the full expected/observed verification receipt, upload it, and bind its readback SHA-256"
                transformations.append({"source": lineage, "transformation": transformation})
            target_rows.append({
                "name": column,
                "type": value_type,
                "constraints": constraints(target_table, column, value_type),
                "authoritative_owner": TARGET_OWNERS[target_table],
                "source_lineage": transformations,
                "producers": producers,
                "consumers": consumers,
                "rationale": (
                    f"Keep {target_table}.{column} only because it is required for the durable domain boundary "
                    "identified by its exact lineage and lifecycle consumers; generic execution data stays in n8n."
                ),
            })
        target_schemas[target_table] = target_rows

    current["target_schemas"] = target_schemas
    current["rows"] = rows
    current["schema_version"] = "n8n-data-table-column-disposition-v2"
    current["resolver_contract"] = {
        "owner": "Git",
        "subworkflows": [
            "Resolve Finance Source and Runtime Config",
            "Resolve AI Policy and Output Contract",
        ],
        "placements": {
            "Resolve Finance Source and Runtime Config": "Finance/Shared",
            "Resolve AI Policy and Output Contract": "Global/Shared",
        },
        "node_type": "n8n-nodes-base.set",
        "content_addressed": True,
        "caller_mutable": False,
        "activation_gate": "Generated resolver bytes and SHA-256, exact source/policy cardinality, Git commit and image-bound manifest must match before activation; caller override, missing/duplicate key or drift fails closed.",
        "callable_workflow_parameters": {
            "layers": {"input": "strict typed trigger schema", "config": "caller-immutable generated resolver", "params": "typed Workflow Parameters Edit Fields entry node"},
            "input_schema": "strict typed schema; unknown and override fields rejected",
            "local_node": "exactly one Workflow Parameters Edit Fields node",
            "downstream_expression": "$('Workflow Parameters').first().json.<field>",
            "caller_fact_fields": ["ids", "hashes", "window", "cursor", "version", "operation"],
            "forbidden_override_fields": ["source_code", "provider", "model", "reasoning", "account", "mail_folder", "onedrive_path", "credential", "url", "commit"],
            "generic_parameter_table_allowed": False,
        },
        "secret_runtime_owners": {
            "secrets_and_auth": "n8n credentials or 1Password-rendered environment",
            "runtime": "compose/environment owns URLs, images, mounts, ports, timezone and concurrency",
            "enterprise_variables_assumed": False,
        },
    }
    return current


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if MATRIX_PATH.read_text(encoding="utf-8") != rendered:
            raise SystemExit("n8n Data Table column disposition matrix is stale")
        print("n8n Data Table column disposition matrix valid")
        return 0
    MATRIX_PATH.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"generated {MATRIX_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
