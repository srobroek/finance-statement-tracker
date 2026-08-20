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
OPERATIONS = ("create", "get", "upsert", "update")
TARGETS = (
    "finance_ingestion_state",
    "finance_documents",
    "finance_actual_batches",
    "finance_ai_reviews",
)


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
        lines.append(f"{sha256_bytes(normalized_bytes(path))}  {relative}\n".encode("utf-8"))
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
                if operation in {"upsert", "update"} and not isinstance(columns_value, dict):
                    raise MatrixError(f"{relative}#{name} write columns must be an object")
                write_columns = sorted(columns_value) if operation in {"upsert", "update"} else []
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
    for table_name in references:
        references[table_name].sort(key=lambda row: (row["file"], row["node"]))
        if not any(row["operation"] == "create" for row in references[table_name]):
            raise MatrixError(f"{table_name} has no create/schema reference")
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
        if column == "cursor_commit_eligible":
            return {"disposition": "remove", "target_table": None, "target_artifact": None, "target_field": None}
        target_field = ACQUISITION_TARGET_FIELDS.get(column, column)
        return {"disposition": "keep" if target_field == column else "transform", "target_table": "finance_ingestion_state", "target_artifact": None, "target_field": target_field}
    if table_name in {"finance_archive_receipts", "finance_document_operations"}:
        target_field = "archive_verified_at" if column == "verified_at" else column
        return {"disposition": "keep" if target_field == column else "transform", "target_table": "finance_documents", "target_artifact": None, "target_field": target_field}
    if table_name == "finance_actual_outbox":
        if column in {"lease_owner", "lease_fence", "lease_expires_at", "actual_transaction_id"}:
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
        return {"disposition": "transform", "target_table": "finance_actual_batches", "target_artifact": "actual-verification-v2", "target_field": "verification_artifact_sha256"}
    if table_name == "finance_reconciliations":
        if column in {"source_code", "period_key"}:
            return {"disposition": "keep", "target_table": "finance_actual_batches", "target_artifact": None, "target_field": column}
        if column == "cashback_close_id":
            return {"disposition": "transform", "target_table": None, "target_artifact": "cashback-companion", "target_field": column}
        return {"disposition": "transform", "target_table": "finance_actual_batches", "target_artifact": "actual-verification-v2", "target_field": "verification_artifact_sha256"}
    if table_name == "finance_agent_jobs":
        if column not in AGENT_REVIEW_COLUMNS:
            return {"disposition": "remove", "target_table": None, "target_artifact": None, "target_field": None}
        return {"disposition": "keep", "target_table": "finance_ai_reviews", "target_artifact": None, "target_field": column}
    raise MatrixError(f"no migration policy for {table_name}.{column} ({source_type})")


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
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "schema_version": 1,
        "artifact_role": "GENERATED_SOURCE_MATRIX",
        "source_contract": "integrations/n8n/data-tables.json@schema_version=4",
        "source_snapshot": source_snapshot(),
        "scan_roots": list(SCAN_ROOTS),
        "targets": list(TARGETS),
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
