#!/usr/bin/env python3
"""Run receipt-bound four-table migration proofs.

The runner composes ``generate_data_table_migration.py`` and the redacted
readback parser. Runtime actions are deliberately limited to the disposable
bootstrap workflow and the local pre-delete reverse rehearsal.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
N8N = ROOT / "integrations" / "n8n"
MIGRATION_PATH = N8N / "generate_data_table_migration.py"
MATRIX_PATH = N8N / "data-table-migration-matrix.json"
DATA_TABLES_PATH = N8N / "data-tables.json"
READBACK_PARSER_PATH = Path(__file__).with_name("parse_n8n_redacted_wrapper_output.py")
WORKFLOW_ROOT = N8N / "workflows"
TARGETS = (
    "finance_ingestion_state",
    "finance_documents",
    "finance_actual_batches",
    "finance_ai_reviews",
)
HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
HEAD = re.compile(r"^[0-9a-f]{40,64}$")
REQUIRED_FORWARD_ACK = "FOUR_TABLE_FORWARD_REQUIRES_NAMED_OPERATOR_GATE"
REQUIRED_ROLLBACK_ACK = "FOUR_TABLE_ROLLBACK_REQUIRES_NAMED_OPERATOR_GATE"
FORWARD_RUNTIME_ACTION = "FOUR_TABLE_FORWARD_RUNTIME_EXECUTED"
ROLLBACK_RUNTIME_ACTION = "FOUR_TABLE_ROLLBACK_RUNTIME_EXECUTED"


class CutoverError(ValueError):
    """Raised when a cutover proof cannot be bound to its inputs."""


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(_canonical(value), ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _digest_json_without_newline(value: Any) -> str:
    payload = json.dumps(_canonical(value), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CutoverError(f"DUPLICATE_JSON_KEY:{key}")
        value[key] = item
    return value


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise CutoverError(f"INPUT_READ_FAILED:{path.name}") from error
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, CutoverError) as error:
        raise CutoverError(f"INPUT_JSON_INVALID:{path.name}") from error
    if not isinstance(value, dict):
        raise CutoverError(f"INPUT_OBJECT_REQUIRED:{path.name}")
    return value, raw


def _require_protected(path: Path) -> None:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as error:
        raise CutoverError(f"PROTECTED_RECEIPT_UNAVAILABLE:{path.name}") from error
    if mode & 0o077:
        raise CutoverError(f"PROTECTED_RECEIPT_MODE_REQUIRED:{path.name}")


def _require_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX_DIGEST.fullmatch(value) is None:
        raise CutoverError(f"{label}_INVALID")
    return value


def _require_head(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEAD.fullmatch(value) is None:
        raise CutoverError(f"{label}_INVALID")
    return value


def _load_migration_module() -> Any:
    spec = importlib.util.spec_from_file_location("finance_four_table_migration", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise CutoverError("MIGRATION_GENERATOR_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_matrix() -> dict[str, Any]:
    matrix, _ = _read_json(MATRIX_PATH)
    target_schemas = matrix.get("target_schemas")
    if not isinstance(target_schemas, dict) or set(target_schemas) != set(TARGETS):
        raise CutoverError("EXACT_TARGET_SCHEMA_SET_REQUIRED")
    return matrix


def _legacy_names() -> set[str]:
    tables, _ = _read_json(DATA_TABLES_PATH)
    values = tables.get("tables")
    if not isinstance(values, list):
        raise CutoverError("SOURCE_TABLE_CONTRACT_INVALID")
    names = {item.get("name") for item in values if isinstance(item, dict)}
    if not names or not all(isinstance(name, str) for name in names):
        raise CutoverError("SOURCE_TABLE_CONTRACT_INVALID")
    return names - set(TARGETS)


def _check_reference_rewrite(workflow_root: Path | None) -> dict[str, Any]:
    if workflow_root is None:
        return {"checked": False, "verified": False, "legacy_references": []}
    if not workflow_root.is_dir():
        raise CutoverError("WORKFLOW_ROOT_UNAVAILABLE")
    legacy = _legacy_names()
    references: list[dict[str, str]] = []
    for path in sorted(workflow_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".json", ".js", ".cjs", ".ts", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8")
        for name in sorted(legacy):
            if name in text:
                references.append({"path": str(path.relative_to(workflow_root)), "table": name})
    if references:
        raise CutoverError("LEGACY_TABLE_REFERENCES_REMAIN")
    return {"checked": True, "verified": True, "legacy_references": []}


def _source_and_receipt(
    source_path: Path,
    migration_receipt_path: Path,
    migration_receipt_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    _require_protected(migration_receipt_path)
    migration_receipt, receipt_bytes = _read_json(migration_receipt_path)
    observed_sha = hashlib.sha256(receipt_bytes).hexdigest()
    if observed_sha != migration_receipt_sha256:
        raise CutoverError("MIGRATION_RECEIPT_SHA256_MISMATCH")
    if migration_receipt.get("schema_version") != "data-table-migration-receipt-v1":
        raise CutoverError("MIGRATION_RECEIPT_SCHEMA_INVALID")
    source, _ = _read_json(source_path)
    if source.get("schema_version") != "finance-data-table-backup-v1":
        raise CutoverError("SOURCE_BACKUP_SCHEMA_INVALID")
    if not isinstance(source.get("tables"), dict):
        raise CutoverError("SOURCE_BACKUP_TABLES_INVALID")
    return source, migration_receipt, observed_sha


def _source_rows(source: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    tables = source.get("tables")
    if not isinstance(tables, dict):
        raise CutoverError("SOURCE_BACKUP_TABLES_INVALID")
    result: dict[str, list[dict[str, Any]]] = {}
    for name, value in tables.items():
        rows = value.get("rows") if isinstance(value, dict) and "rows" in value else value
        if not isinstance(name, str) or not isinstance(rows, list) or any(
            not isinstance(row, dict) for row in rows
        ):
            raise CutoverError("SOURCE_BACKUP_ROWS_INVALID")
        result[name] = [dict(row) for row in rows]
    return result


def _target_table_receipts(runner: Any, matrix: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for name in sorted(TARGETS):
        target_schema = matrix["target_schemas"][name]
        columns = target_schema.get("columns")
        if not isinstance(columns, dict):
            raise CutoverError(f"TARGET_SCHEMA_INVALID:{name}")
        schema = [
            {"name": field, "type": str(spec.get("type", "")).lower()}
            for field, spec in sorted(columns.items())
            if isinstance(spec, dict)
        ]
        if not schema or any(not column["type"] for column in schema):
            raise CutoverError(f"TARGET_SCHEMA_INVALID:{name}")
        rows = runner.target_tables.get(name)
        if not isinstance(rows, list):
            raise CutoverError(f"TARGET_ROWS_INVALID:{name}")
        row_strings = sorted(
            json.dumps(_canonical(row), ensure_ascii=False, separators=(",", ":")) for row in rows
        )
        table = {
            "name": name,
            "schema_sha256": _digest_json_without_newline(schema),
            "row_count": len(rows),
            "rows_sha256": _digest_json_without_newline(row_strings),
        }
        table["digest_sha256"] = _digest_json_without_newline(table)
        result.append(table)
    if [table["name"] for table in result] != sorted(TARGETS):
        raise CutoverError("EXACT_FINANCE_DATA_TABLE_NAMES_REQUIRED")
    return result


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_bytes(value)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
    finally:
        if descriptor != -1:
            os.close(descriptor)


def _seal(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result.pop("cutover_receipt_sha256", None)
    result["cutover_receipt_sha256"] = hashlib.sha256(_canonical_bytes(result)).hexdigest()
    return result


def _parse_readback(path: Path, migration_sha256: str, expected_phase: str) -> dict[str, Any]:
    parser_spec = importlib.util.spec_from_file_location("finance_readback_parser", READBACK_PARSER_PATH)
    if parser_spec is None or parser_spec.loader is None:
        raise CutoverError("READBACK_PARSER_UNAVAILABLE")
    parser = importlib.util.module_from_spec(parser_spec)
    parser_spec.loader.exec_module(parser)
    raw = path.read_text(encoding="utf-8")
    prefix = "finance data table digest verified:"
    if not raw.startswith(prefix):
        raise CutoverError("READBACK_RECEIPT_INVALID")
    try:
        raw_payload = json.loads(raw[len(prefix) :].strip(), object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, CutoverError) as error:
        raise CutoverError("READBACK_RECEIPT_INVALID") from error
    if not isinstance(raw_payload, dict):
        raise CutoverError("READBACK_RECEIPT_INVALID")
    migration = raw_payload.get("migration_receipt", {})
    if migration.get("bound") is not True or migration.get("sha256") != migration_sha256:
        raise CutoverError("READBACK_MIGRATION_RECEIPT_MISMATCH")
    if raw_payload.get("phase") != expected_phase:
        raise CutoverError("READBACK_PHASE_MISMATCH")
    if raw_payload.get("status") == "FORWARD_PRE_READBACK":
        if expected_phase != "FORWARD_PRE":
            raise CutoverError("READBACK_OPERATION_MISMATCH")
        if raw_payload.get("finance_tables") != 0 or raw_payload.get("total_rows") != 0:
            raise CutoverError("FORWARD_PRE_READBACK_MUST_BE_EMPTY")
        if raw_payload.get("tables") != []:
            raise CutoverError("FORWARD_PRE_READBACK_MUST_BE_EMPTY")
        if not isinstance(raw_payload.get("digest_sha256"), str) or not HEX_DIGEST.fullmatch(
            raw_payload["digest_sha256"]
        ):
            raise CutoverError("READBACK_DIGEST_INVALID")
        return {
            "verified": True,
            "phase": "FORWARD_PRE",
            "digest_sha256": raw_payload["digest_sha256"],
            "finance_tables": 0,
            "total_rows": 0,
            "tables": [],
        }
    try:
        payload = parser.parse_data_table_receipt(raw)
    except (ValueError, json.JSONDecodeError) as error:
        raise CutoverError("READBACK_RECEIPT_INVALID") from error
    if raw_payload.get("status") != "VERIFIED":
        raise CutoverError("READBACK_STATUS_INVALID")
    return {
        "verified": True,
        "phase": expected_phase,
        "digest_sha256": payload["digest_sha256"],
        "finance_tables": payload["finance_tables"],
        "total_rows": payload["total_rows"],
        "tables": [
            {
                "name": table["name"],
                "schema_sha256": table["schema_sha256"],
                "row_count": table["row_count"],
                "rows_sha256": table["rows_sha256"],
                "digest_sha256": table["digest_sha256"],
            }
            for table in payload["tables"]
        ],
    }


def _target_schema_digests(matrix: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in sorted(TARGETS):
        columns = matrix["target_schemas"][name].get("columns")
        if not isinstance(columns, dict):
            raise CutoverError(f"TARGET_SCHEMA_INVALID:{name}")
        schema = [
            {"name": field, "type": str(spec.get("type", "")).lower()}
            for field, spec in sorted(columns.items())
            if isinstance(spec, dict)
        ]
        result[name] = _digest_json_without_newline(schema)
    return result


def _compare_readbacks(
    before: Mapping[str, Any], after: Mapping[str, Any], matrix: Mapping[str, Any]
) -> dict[str, Any]:
    if not before.get("verified") or not after.get("verified"):
        raise CutoverError("PRE_POST_READBACK_REQUIRED")
    if before.get("finance_tables") != 4 or after.get("finance_tables") != 4:
        raise CutoverError("EXACT_READBACK_TABLE_COUNT_REQUIRED")
    expected_schema = _target_schema_digests(matrix)
    before_tables = before.get("tables")
    after_tables = after.get("tables")
    if not isinstance(before_tables, list) or not isinstance(after_tables, list):
        raise CutoverError("PER_TABLE_READBACK_REQUIRED")
    expected_names = sorted(TARGETS)
    if [table.get("name") for table in before_tables] != expected_names or [
        table.get("name") for table in after_tables
    ] != expected_names:
        raise CutoverError("EXACT_FINANCE_DATA_TABLE_NAMES_REQUIRED")
    comparisons: list[dict[str, Any]] = []
    for left, right in zip(before_tables, after_tables, strict=True):
        name = left["name"]
        if left["schema_sha256"] != expected_schema[name] or right["schema_sha256"] != expected_schema[name]:
            raise CutoverError(f"TARGET_SCHEMA_DIGEST_MISMATCH:{name}")
        digest_fields = ("schema_sha256", "row_count", "rows_sha256", "digest_sha256")
        if any(left[field] != right[field] for field in digest_fields):
            raise CutoverError(f"PRE_POST_TABLE_DIGEST_MISMATCH:{name}")
        comparisons.append({"name": name, **{field: right[field] for field in digest_fields}})
    if before.get("digest_sha256") != after.get("digest_sha256"):
        raise CutoverError("PRE_POST_READBACK_DIGEST_MISMATCH")
    return {
        "verified": True,
        "phase": after["phase"],
        "digest_sha256": after["digest_sha256"],
        "finance_tables": 4,
        "total_rows": after["total_rows"],
        "tables": comparisons,
    }


def _validate_target_readback(
    payload: Mapping[str, Any], matrix: Mapping[str, Any], *, label: str
) -> dict[str, Any]:
    if not payload.get("verified") or payload.get("finance_tables") != 4:
        raise CutoverError(f"{label}_READBACK_REQUIRED")
    tables = payload.get("tables")
    expected_names = sorted(TARGETS)
    if not isinstance(tables, list) or [table.get("name") for table in tables] != expected_names:
        raise CutoverError("EXACT_FINANCE_DATA_TABLE_NAMES_REQUIRED")
    expected_schema = _target_schema_digests(matrix)
    result_tables: list[dict[str, Any]] = []
    for table in tables:
        name = table["name"]
        if table.get("schema_sha256") != expected_schema[name]:
            raise CutoverError(f"TARGET_SCHEMA_DIGEST_MISMATCH:{name}")
        for field in ("schema_sha256", "rows_sha256", "digest_sha256"):
            _require_digest(table.get(field), f"{label}_{name}_{field}")
        if not isinstance(table.get("row_count"), int) or table["row_count"] < 0:
            raise CutoverError(f"{label}_{name}_ROW_COUNT_INVALID")
        result_tables.append(
            {
                "name": name,
                "schema_sha256": table["schema_sha256"],
                "row_count": table["row_count"],
                "rows_sha256": table["rows_sha256"],
                "digest_sha256": table["digest_sha256"],
            }
        )
    return {
        "verified": True,
        "phase": payload["phase"],
        "digest_sha256": _require_digest(payload.get("digest_sha256"), f"{label}_DIGEST"),
        "finance_tables": 4,
        "total_rows": payload["total_rows"],
        "tables": result_tables,
    }


def _compare_forward_readbacks(
    before: Mapping[str, Any],
    first_after: Mapping[str, Any],
    second_after: Mapping[str, Any],
    matrix: Mapping[str, Any],
    expected_tables: list[Mapping[str, Any]],
) -> dict[str, Any]:
    if before.get("phase") != "FORWARD_PRE" or before.get("finance_tables") != 0:
        raise CutoverError("FORWARD_FIRST_RUN_PRE_READBACK_REQUIRED")
    first = _validate_target_readback(first_after, matrix, label="FIRST_POST")
    second = _validate_target_readback(second_after, matrix, label="SECOND_POST")
    if first != second:
        raise CutoverError("SECOND_RUNTIME_RUN_NOT_NOOP")
    expected = {table["name"]: table for table in expected_tables}
    for table in first["tables"]:
        wanted = expected.get(table["name"])
        if wanted is None or any(
            table[field] != wanted[field]
            for field in ("schema_sha256", "row_count", "rows_sha256")
        ):
            raise CutoverError(f"RUNTIME_PROJECTION_DIGEST_MISMATCH:{table['name']}")
    return first


def _heads(args: argparse.Namespace, expected_ack: str, expected_action: str) -> tuple[str, str, str, str]:
    try:
        repository_root = args.repository_root.resolve(strict=True)
    except OSError as error:
        raise CutoverError("REPOSITORY_ROOT_UNAVAILABLE") from error
    if repository_root != ROOT:
        raise CutoverError("REPOSITORY_ROOT_MISMATCH")
    try:
        workflow_root = args.workflow_root.resolve(strict=True)
    except OSError as error:
        raise CutoverError("WORKFLOW_ROOT_UNAVAILABLE") from error
    if workflow_root != WORKFLOW_ROOT:
        raise CutoverError("WORKFLOW_ROOT_MISMATCH")
    try:
        clean = subprocess.run(
            ["git", "-C", str(repository_root), "status", "--porcelain", "--untracked-files=no"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if clean:
            raise CutoverError("CLEAN_CHECKOUT_REQUIRED")
        source_head = subprocess.run(
            ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        generator_head = subprocess.run(
            [sys.executable, str(MIGRATION_PATH), "--schema-digest"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()[-1].strip()
    except (OSError, subprocess.CalledProcessError, IndexError) as error:
        raise CutoverError("SOURCE_GENERATOR_HEAD_UNAVAILABLE") from error
    source_head = _require_head(source_head, "SOURCE_HEAD")
    generator_head = _require_head(generator_head, "GENERATOR_HEAD")
    identity_path = args.accepted_identity or args.migration_receipt.with_name(
        "finance-four-table-accepted-identity.json"
    )
    _require_protected(identity_path)
    identity, _ = _read_json(identity_path)
    if (
        identity.get("schema_version") != "finance-four-table-accepted-identity-v1"
        or identity.get("repository_root") != str(repository_root)
        or identity.get("workflow_root") != str(workflow_root)
        or identity.get("source_head") != source_head
        or identity.get("generator_head") != generator_head
        or identity.get("clean_checkout") is not True
        or identity.get("legacy_references") != []
    ):
        raise CutoverError("ACCEPTED_CHECKOUT_IDENTITY_MISMATCH")
    identity_digest = _require_digest(identity.get("identity_sha256"), "IDENTITY_SHA256")
    unsigned_identity = dict(identity)
    unsigned_identity.pop("identity_sha256", None)
    if hashlib.sha256(_canonical_bytes(unsigned_identity)).hexdigest() != identity_digest:
        raise CutoverError("ACCEPTED_CHECKOUT_IDENTITY_INTEGRITY_MISMATCH")
    receipt_sha = _require_digest(args.migration_receipt_sha256, "MIGRATION_RECEIPT_SHA256")
    if args.operator_ack != expected_ack or args.runtime_action != expected_action:
        raise CutoverError("NAMED_OPERATOR_ACK_REQUIRED")
    return source_head, generator_head, receipt_sha, identity_digest


def run_forward(args: argparse.Namespace) -> dict[str, Any]:
    source_head, generator_head, receipt_sha, identity_digest = _heads(
        args, REQUIRED_FORWARD_ACK, FORWARD_RUNTIME_ACTION
    )
    source, expected_receipt, _ = _source_and_receipt(
        args.source_backup, args.migration_receipt, receipt_sha
    )
    module = _load_migration_module()
    runner = module.MigrationRunner(_source_rows(source))
    first = runner.run()
    second = runner.run()
    if first != expected_receipt:
        raise CutoverError("MIGRATION_RECEIPT_CONTENT_MISMATCH")
    if not second.get("second_run_noop") or second.get("changed") is not False:
        raise CutoverError("SECOND_RUN_NOOP_REQUIRED")
    matrix = _load_matrix()
    schema_sha = module.generated_target_schema_digest()
    if first.get("target_schema_sha256") != schema_sha:
        raise CutoverError("TARGET_SCHEMA_DIGEST_MISMATCH")
    references = _check_reference_rewrite(args.workflow_root)
    before = _parse_readback(args.pre_readback_raw, receipt_sha, "FORWARD_PRE")
    after = _parse_readback(args.post_readback_raw, receipt_sha, "FORWARD_POST")
    second_after = _parse_readback(args.second_post_readback_raw, receipt_sha, "FORWARD_POST")
    table_receipts = _target_table_receipts(runner, matrix)
    readback = _compare_forward_readbacks(before, after, second_after, matrix, table_receipts)
    result = _seal({
        "schema_version": "finance-four-table-cutover-receipt-v1",
        "operation": "FORWARD",
        "migration_receipt_sha256": receipt_sha,
        "source_head": source_head,
        "generator_head": generator_head,
        "accepted_identity_sha256": identity_digest,
        "source_digest": first["source_digest"],
        "target_digest": first["target_digest"],
        "target_schema_sha256": schema_sha,
        "target_tables": table_receipts,
        "side_by_side_created": True,
        "exact_target_names": True,
        "reference_rewrite": references,
        "second_run_noop": True,
        "first_run_created": True,
        "readback": readback,
        "pre_readback": before,
        "post_readback": after,
        "second_post_readback": second_after,
        "runtime_execution": True,
        "runtime_action": FORWARD_RUNTIME_ACTION,
        "old_tables_preserved": True,
        "runtime_cutover": False,
        "deletion_authorized": False,
        "operator_ack": REQUIRED_FORWARD_ACK,
    })
    _write_json(args.output, result)
    return result


def run_rollback(args: argparse.Namespace) -> dict[str, Any]:
    source_head, generator_head, receipt_sha, identity_digest = _heads(
        args, REQUIRED_ROLLBACK_ACK, ROLLBACK_RUNTIME_ACTION
    )
    source, migration_receipt, _ = _source_and_receipt(
        args.source_backup, args.migration_receipt, receipt_sha
    )
    if migration_receipt.get("old_tables_preserved") is not True or migration_receipt.get(
        "deletion_authorized"
    ) is not False:
        raise CutoverError("ROLLBACK_ONLY_BEFORE_LEGACY_DELETION")
    _require_protected(args.forward_receipt)
    forward, _ = _read_json(args.forward_receipt)
    if (
        forward.get("schema_version") != "finance-four-table-cutover-receipt-v1"
        or forward.get("operation") != "FORWARD"
        or forward.get("migration_receipt_sha256") != receipt_sha
        or forward.get("source_head") != source_head
        or forward.get("generator_head") != generator_head
        or forward.get("old_tables_preserved") is not True
        or forward.get("runtime_cutover") is not False
        or forward.get("deletion_authorized") is not False
    ):
        raise CutoverError("FORWARD_RECEIPT_BINDING_MISMATCH")
    forward_integrity = _require_digest(
        forward.get("cutover_receipt_sha256"), "CUTOVER_RECEIPT_SHA256"
    )
    unsigned_forward = dict(forward)
    unsigned_forward.pop("cutover_receipt_sha256", None)
    if hashlib.sha256(_canonical_bytes(unsigned_forward)).hexdigest() != forward_integrity:
        raise CutoverError("FORWARD_RECEIPT_INTEGRITY_MISMATCH")
    module = _load_migration_module()
    matrix = _load_matrix()
    runner = module.MigrationRunner(_source_rows(source))
    expected_tables = _target_table_receipts(runner, matrix)
    before = _parse_readback(args.pre_readback_raw, receipt_sha, "ROLLBACK")
    after = _parse_readback(args.post_readback_raw, receipt_sha, "ROLLBACK")
    readback = _compare_readbacks(before, after, matrix)
    expected = {table["name"]: table for table in expected_tables}
    for table in readback["tables"]:
        wanted = expected.get(table["name"])
        if wanted is None or any(
            table[field] != wanted[field]
            for field in ("schema_sha256", "row_count", "rows_sha256")
        ):
            raise CutoverError(f"RUNTIME_PROJECTION_DIGEST_MISMATCH:{table['name']}")
    _verify_runtime_proof(
        args.runtime_proof, receipt_sha, source_head, generator_head, identity_digest, source
    )
    rehearsal = runner.reverse_rehearsal()
    source_digest = migration_receipt.get("source_digest")
    if (
        rehearsal.get("restore_roundtrip") is not True
        or rehearsal.get("source_digest") != source_digest
        or rehearsal.get("restored_source_digest") != source_digest
        or rehearsal.get("target_tables_untouched") is not True
    ):
        raise CutoverError("EXACT_ROLLBACK_DIGEST_RESTORATION_REQUIRED")
    result = _seal({
        "schema_version": "finance-four-table-cutover-receipt-v1",
        "operation": "ROLLBACK",
        "migration_receipt_sha256": receipt_sha,
        "source_head": source_head,
        "generator_head": generator_head,
        "accepted_identity_sha256": identity_digest,
        "source_digest": source_digest,
        "restored_source_digest": rehearsal["restored_source_digest"],
        "restore_roundtrip": True,
        "pre_delete": True,
        "target_tables_untouched": True,
        "old_tables_preserved": True,
        "runtime_cutover": False,
        "deletion_authorized": False,
        "operator_ack": REQUIRED_ROLLBACK_ACK,
        "readback": readback,
        "pre_readback": before,
        "post_readback": after,
        "runtime_execution": True,
        "runtime_action": ROLLBACK_RUNTIME_ACTION,
    })
    _write_json(args.output, result)
    return result


def _verify_runtime_proof(
    path: Path,
    receipt_sha: str,
    source_head: str,
    generator_head: str,
    identity_digest: str,
    source: Mapping[str, Any],
) -> None:
    _require_protected(path)
    proof, _ = _read_json(path)
    if (
        proof.get("schema_version") != "data-table-reverse-rehearsal-runtime-proof-v1"
        or proof.get("migration_receipt_sha256") != receipt_sha
        or proof.get("source_head") != source_head
        or proof.get("generator_head") != generator_head
        or proof.get("accepted_identity_sha256") != identity_digest
        or proof.get("operator_ack") != REQUIRED_ROLLBACK_ACK
        or proof.get("runtime_action") != ROLLBACK_RUNTIME_ACTION
        or proof.get("pre_delete") is not True
        or proof.get("restore_roundtrip") is not True
        or proof.get("target_tables_untouched") is not True
    ):
        raise CutoverError("ROLLBACK_RUNTIME_PROOF_BINDING_MISMATCH")
    source_digest = _load_migration_module().MigrationRunner(_source_rows(source)).backup_digest()
    if proof.get("source_digest") != source_digest or proof.get("restored_source_digest") != source_digest:
        raise CutoverError("ROLLBACK_RUNTIME_PROOF_DIGEST_MISMATCH")
    integrity = _require_digest(proof.get("runtime_proof_sha256"), "RUNTIME_PROOF_SHA256")
    unsigned = dict(proof)
    unsigned.pop("runtime_proof_sha256", None)
    if hashlib.sha256(_canonical_bytes(unsigned)).hexdigest() != integrity:
        raise CutoverError("ROLLBACK_RUNTIME_PROOF_INTEGRITY_MISMATCH")


def run_rollback_rehearsal(args: argparse.Namespace) -> dict[str, Any]:
    source_head, generator_head, receipt_sha, identity_digest = _heads(
        args, REQUIRED_ROLLBACK_ACK, ROLLBACK_RUNTIME_ACTION
    )
    source, migration_receipt, _ = _source_and_receipt(
        args.source_backup, args.migration_receipt, receipt_sha
    )
    if migration_receipt.get("old_tables_preserved") is not True or migration_receipt.get(
        "deletion_authorized"
    ) is not False:
        raise CutoverError("ROLLBACK_ONLY_BEFORE_LEGACY_DELETION")
    runner = _load_migration_module().MigrationRunner(_source_rows(source))
    rehearsal = runner.reverse_rehearsal()
    source_digest = migration_receipt.get("source_digest")
    if (
        rehearsal.get("restore_roundtrip") is not True
        or rehearsal.get("source_digest") != source_digest
        or rehearsal.get("restored_source_digest") != source_digest
        or rehearsal.get("target_tables_untouched") is not True
    ):
        raise CutoverError("EXACT_ROLLBACK_DIGEST_RESTORATION_REQUIRED")
    result = _seal(
        {
            "schema_version": "data-table-reverse-rehearsal-runtime-proof-v1",
            "migration_receipt_sha256": receipt_sha,
            "source_head": source_head,
            "generator_head": generator_head,
            "accepted_identity_sha256": identity_digest,
            "source_digest": source_digest,
            "restored_source_digest": rehearsal["restored_source_digest"],
            "restore_roundtrip": True,
            "target_tables_untouched": True,
            "pre_delete": True,
            "runtime_execution": True,
            "runtime_action": ROLLBACK_RUNTIME_ACTION,
            "operator_ack": REQUIRED_ROLLBACK_ACK,
        }
    )
    # _seal uses the cutover key; this proof has its own schema/key.
    result["runtime_proof_sha256"] = result.pop("cutover_receipt_sha256")
    _write_json(args.output, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for operation in ("forward", "rollback", "rollback-rehearsal"):
        command = subparsers.add_parser(operation)
        command.add_argument("--source-backup", type=Path, required=True)
        command.add_argument("--migration-receipt", type=Path, required=True)
        command.add_argument("--migration-receipt-sha256", required=True)
        command.add_argument("--repository-root", type=Path, required=True)
        command.add_argument("--accepted-identity", type=Path)
        command.add_argument("--operator-ack", required=True)
        command.add_argument("--runtime-action", required=True)
        command.add_argument("--workflow-root", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        if operation in {"forward", "rollback"}:
            command.add_argument("--pre-readback-raw", type=Path, required=True)
            command.add_argument("--post-readback-raw", type=Path, required=True)
        if operation == "forward":
            command.add_argument("--second-post-readback-raw", type=Path, required=True)
        if operation == "rollback":
            command.add_argument("--forward-receipt", type=Path, required=True)
            command.add_argument("--runtime-proof", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.operation == "forward":
            result = run_forward(args)
        elif args.operation == "rollback":
            result = run_rollback(args)
        else:
            result = run_rollback_rehearsal(args)
    except (CutoverError, OSError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
