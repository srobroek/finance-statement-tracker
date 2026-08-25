#!/usr/bin/env python3
"""Extract one exact redacted receipt from allowlisted n8n 2.36.2 startup output."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from typing import Any

MAX_BYTES = 65536
ANSI_CSI_PREFIX = re.compile(r"^(?:\x1b\[[0-9;]{0,32}[A-Za-z])+")
ANSI_CSI_SUFFIX = re.compile(r"(?:\x1b\[[0-9;]{0,32}[A-Za-z])+$")
ALLOWED_DIAGNOSTICS = {
    '>>>> Executing external compose provider "/usr/local/bin/docker-compose". Please refer to the documentation for details. <<<<',
    "Postgres 16 is outside the supported range and receives compatibility support only. Upgrade to Postgres 17 or newer.",
    "Acquiring database migration lock...",
    'Deprecation warning: The storage directory "/home/node/.n8n/binaryData" will be renamed to "/home/node/.n8n/storage" in n8n v3. To migrate now, set N8N_MIGRATE_FS_STORAGE_PATH=true. If you have a volume mounted at the old path, update your mount configuration after migration.',
}


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"DUPLICATE_JSON_KEY:{key}")
        value[key] = item
    return value


def remove_boundary_csi(line: str) -> str:
    line = ANSI_CSI_PREFIX.sub("", line)
    line = ANSI_CSI_SUFFIX.sub("", line)
    if "\x1b" in line:
        raise ValueError("CSI_ONLY_ALLOWED_AT_LINE_BOUNDARY")
    return line


def strict_int(value: Any) -> bool:
    return type(value) is int


def strict_bool(value: Any) -> bool:
    return type(value) is bool


def extract_payload(raw: str, prefix: str) -> dict[str, Any]:
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise ValueError("WRAPPER_OUTPUT_BOUND_EXCEEDED")
    receipt_lines: list[str] = []
    for original_line in raw.splitlines():
        line = remove_boundary_csi(original_line)
        if line == "":
            continue
        if line.startswith(prefix):
            receipt_lines.append(line[len(prefix) :])
        elif line not in ALLOWED_DIAGNOSTICS:
            raise ValueError("UNEXPECTED_N8N_WRAPPER_OUTPUT")
    if len(receipt_lines) != 1:
        raise ValueError("EXACT_ONE_REDACTED_RECEIPT_REQUIRED")
    payload = json.loads(receipt_lines[0], object_pairs_hook=reject_duplicate_keys)
    if not isinstance(payload, dict):
        raise TypeError("REDACTED_RECEIPT_OBJECT_REQUIRED")
    return payload


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_data_table_receipt(raw: str) -> dict[str, Any]:
    value = extract_payload(raw, "finance data table digest verified:")
    expected_keys = {
        "schema_version", "receipt_contract", "status", "scope", "finance_tables",
        "tables", "total_rows", "digest_sha256", "migration_receipt",
        "forward_gate", "rollback_gate", "writes_performed", "provider_calls",
        "row_values_recorded", "secret_values_recorded",
    }
    if set(value) not in {expected_keys, expected_keys | {"phase"}}:
        raise ValueError("DATA_TABLE_DIGEST_RECEIPT_KEYS_MISMATCH")
    expected_table_names = [
        "finance_actual_batches",
        "finance_ai_reviews",
        "finance_documents",
        "finance_ingestion_state",
    ]
    table_keys = {
        "name", "table_id_sha256", "schema", "schema_sha256", "row_count",
        "rows_sha256", "digest_sha256",
    }
    tables = value.get("tables")
    if (
        not isinstance(tables, list)
        or len(tables) != 4
        or any(not isinstance(table, dict) or set(table) != table_keys for table in tables)
        or [table["name"] for table in tables] != expected_table_names
    ):
        raise ValueError("DATA_TABLE_DIGEST_TABLES_MISMATCH")
    sha256_pattern = re.compile(r"[0-9a-f]{64}")
    for table in tables:
        schema = table["schema"]
        if (
            not isinstance(schema, list)
            or not schema
            or any(
                not isinstance(column, dict)
                or set(column) != {"name", "type"}
                or not isinstance(column["name"], str)
                or not column["name"]
                or column["type"] not in {"string", "number", "boolean", "date"}
                for column in schema
            )
            or [column["name"] for column in schema] != sorted(column["name"] for column in schema)
            or len({column["name"] for column in schema}) != len(schema)
            or not strict_int(table["row_count"])
            or not 0 <= table["row_count"] <= 100000
            or any(
                not isinstance(table[key], str) or sha256_pattern.fullmatch(table[key]) is None
                for key in ("table_id_sha256", "schema_sha256", "rows_sha256", "digest_sha256")
            )
            or table["schema_sha256"] != canonical_sha256(schema)
            or table["digest_sha256"] != canonical_sha256({key: item for key, item in table.items() if key != "digest_sha256"})
        ):
            raise ValueError("DATA_TABLE_DIGEST_TABLE_CONTRACT_MISMATCH")
    if len({table["table_id_sha256"] for table in tables}) != 4:
        raise ValueError("DATA_TABLE_DIGEST_TABLE_IDENTITY_MISMATCH")
    migration_receipt = value.get("migration_receipt")
    if not isinstance(migration_receipt, dict) or set(migration_receipt) != {"schema_version", "required", "bound", "sha256"}:
        raise ValueError("DATA_TABLE_DIGEST_MIGRATION_RECEIPT_MISMATCH")
    migration_bound = migration_receipt.get("bound")
    migration_sha256 = migration_receipt.get("sha256")
    if (
        migration_receipt.get("schema_version") != "data-table-migration-receipt-v1"
        or migration_receipt.get("required") is not True
        or not strict_bool(migration_bound)
        or (migration_bound and (not isinstance(migration_sha256, str) or sha256_pattern.fullmatch(migration_sha256) is None))
        or (not migration_bound and migration_sha256 is not None)
    ):
        raise ValueError("DATA_TABLE_DIGEST_MIGRATION_RECEIPT_MISMATCH")
    if migration_bound and value.get("phase") not in {"FORWARD_POST", "ROLLBACK"}:
        raise ValueError("DATA_TABLE_DIGEST_PHASE_MISMATCH")
    gate_keys = {"gate", "status", "required_ack", "migration_receipt_required", "command_executed"}
    for key, gate, required_ack in (
        ("forward_gate", "FORWARD", "FOUR_TABLE_FORWARD_REQUIRES_NAMED_OPERATOR_GATE"),
        ("rollback_gate", "ROLLBACK", "FOUR_TABLE_ROLLBACK_REQUIRES_NAMED_OPERATOR_GATE"),
    ):
        observed = value.get(key)
        if not isinstance(observed, dict) or observed != {
            "gate": gate,
            "status": "BLOCKED",
            "required_ack": required_ack,
            "migration_receipt_required": True,
            "command_executed": False,
        } or set(observed) != gate_keys:
            raise ValueError("DATA_TABLE_DIGEST_GATE_MISMATCH")
    if (
        not strict_int(value["schema_version"])
        or value["schema_version"] != 1
        or value["receipt_contract"] != "finance-data-table-readback-receipt-v1"
        or value["status"] != "VERIFIED"
        or ("phase" in value and value["phase"] not in {"FORWARD_POST", "ROLLBACK"})
        or value["scope"] != "READ_ONLY_IN_MEMORY_FINANCE_DATA_TABLE_DIGEST"
        or not strict_int(value["finance_tables"])
        or value["finance_tables"] != 4
        or not strict_int(value["total_rows"])
        or not 0 <= value["total_rows"] <= 400000
        or value["total_rows"] != sum(table["row_count"] for table in tables)
        or not isinstance(value["digest_sha256"], str)
        or sha256_pattern.fullmatch(value["digest_sha256"]) is None
        or value["digest_sha256"] != canonical_sha256(tables)
        or not strict_bool(value["writes_performed"])
        or value["writes_performed"] is not False
        or not strict_bool(value["provider_calls"])
        or value["provider_calls"] is not False
        or not strict_bool(value["row_values_recorded"])
        or value["row_values_recorded"] is not False
        or not strict_bool(value["secret_values_recorded"])
        or value["secret_values_recorded"] is not False
    ):
        raise ValueError("DATA_TABLE_DIGEST_RECEIPT_CONTRACT_MISMATCH")
    return value


def parse_data_table(raw: str) -> str:
    return parse_data_table_receipt(raw)["digest_sha256"]


def parse_oauth_metadata(raw: str) -> dict[str, Any]:
    value = extract_payload(raw, "microsoft oauth metadata readback verified:")
    expected_keys = {
        "schema_version", "status", "scope", "observed_at_utc", "credentials",
        "provider_calls", "database_writes", "credential_ids_recorded",
        "secret_values_recorded", "token_fingerprints_recorded",
    }
    if set(value) != expected_keys or set(value.get("credentials", {})) != {"outlook", "onedrive"}:
        raise ValueError("OAUTH_METADATA_RECEIPT_KEYS_MISMATCH")
    expected_credential_keys = {
        "credential_type", "credential_updated_at_utc", "access_token_present",
        "refresh_token_present", "expiration_observed", "expires_at_utc",
        "expired_at_readback",
    }
    if any(set(row) != expected_credential_keys for row in value["credentials"].values()):
        raise ValueError("OAUTH_METADATA_CREDENTIAL_KEYS_MISMATCH")
    expected_types = {
        "outlook": "microsoftOutlookOAuth2Api",
        "onedrive": "microsoftOneDriveOAuth2Api",
    }
    if (
        not strict_int(value["schema_version"])
        or value["schema_version"] != 1
        or value["status"] != "VERIFIED"
        or value["scope"] != "READ_ONLY_MICROSOFT_OAUTH_METADATA"
        or not isinstance(value["observed_at_utc"], str)
        or not strict_bool(value["provider_calls"])
        or value["provider_calls"] is not False
        or not strict_bool(value["database_writes"])
        or value["database_writes"] is not False
        or not strict_bool(value["credential_ids_recorded"])
        or value["credential_ids_recorded"] is not False
        or not strict_bool(value["secret_values_recorded"])
        or value["secret_values_recorded"] is not False
        or not strict_bool(value["token_fingerprints_recorded"])
        or value["token_fingerprints_recorded"] is not False
        or any(value["credentials"][label]["credential_type"] != expected_type for label, expected_type in expected_types.items())
        or any(
            not isinstance(row["credential_type"], str)
            or not isinstance(row["credential_updated_at_utc"], str)
            or not isinstance(row["expires_at_utc"], str)
            or not strict_bool(row["access_token_present"])
            or row["access_token_present"] is not True
            or not strict_bool(row["refresh_token_present"])
            or row["refresh_token_present"] is not True
            or not strict_bool(row["expiration_observed"])
            or row["expiration_observed"] is not True
            or not strict_bool(row["expired_at_readback"])
            for row in value["credentials"].values()
        )
    ):
        raise ValueError("OAUTH_METADATA_RECEIPT_CONTRACT_MISMATCH")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("data-table", "data-table-receipt", "oauth-metadata"))
    args = parser.parse_args()
    raw = sys.stdin.read(MAX_BYTES + 1)
    try:
        if args.kind == "data-table":
            print(parse_data_table(raw))
        elif args.kind == "data-table-receipt":
            print(json.dumps(parse_data_table_receipt(raw), separators=(",", ":"), sort_keys=True))
        else:
            print(json.dumps(parse_oauth_metadata(raw), separators=(",", ":")))
        return 0
    except (ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
