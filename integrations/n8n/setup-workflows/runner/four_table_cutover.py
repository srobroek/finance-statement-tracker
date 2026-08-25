#!/usr/bin/env python3
"""Run receipt-bound four-table migration proofs.

The runner composes ``generate_data_table_migration.py`` and the redacted
readback parser.  It writes proof receipts only; target creation, workflow
execution, and legacy-table deletion remain separate named-operator actions.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
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


def _parse_readback(path: Path, migration_sha256: str) -> dict[str, Any]:
    parser_spec = importlib.util.spec_from_file_location("finance_readback_parser", READBACK_PARSER_PATH)
    if parser_spec is None or parser_spec.loader is None:
        raise CutoverError("READBACK_PARSER_UNAVAILABLE")
    parser = importlib.util.module_from_spec(parser_spec)
    parser_spec.loader.exec_module(parser)
    raw = path.read_text(encoding="utf-8")
    try:
        payload = parser.parse_data_table_receipt(raw)
    except (ValueError, json.JSONDecodeError) as error:
        raise CutoverError("READBACK_RECEIPT_INVALID") from error
    migration = payload.get("migration_receipt", {})
    if migration.get("bound") is not True or migration.get("sha256") != migration_sha256:
        raise CutoverError("READBACK_MIGRATION_RECEIPT_MISMATCH")
    return {
        "verified": True,
        "digest_sha256": payload["digest_sha256"],
        "finance_tables": payload["finance_tables"],
        "total_rows": payload["total_rows"],
    }


def _heads(args: argparse.Namespace, expected_ack: str) -> tuple[str, str, str]:
    source_head = _require_head(args.source_head, "SOURCE_HEAD")
    generator_head = _require_head(args.generator_head, "GENERATOR_HEAD")
    receipt_sha = _require_digest(args.migration_receipt_sha256, "MIGRATION_RECEIPT_SHA256")
    if args.operator_ack != expected_ack:
        raise CutoverError("NAMED_OPERATOR_ACK_REQUIRED")
    return source_head, generator_head, receipt_sha


def run_forward(args: argparse.Namespace) -> dict[str, Any]:
    source_head, generator_head, receipt_sha = _heads(args, REQUIRED_FORWARD_ACK)
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
    readback = _parse_readback(args.readback_raw, receipt_sha) if args.readback_raw else {
        "verified": False,
        "digest_sha256": None,
        "finance_tables": None,
        "total_rows": None,
    }
    table_receipts = _target_table_receipts(runner, matrix)
    result = _seal({
        "schema_version": "finance-four-table-cutover-receipt-v1",
        "operation": "FORWARD",
        "migration_receipt_sha256": receipt_sha,
        "source_head": source_head,
        "generator_head": generator_head,
        "source_digest": first["source_digest"],
        "target_digest": first["target_digest"],
        "target_schema_sha256": schema_sha,
        "target_tables": table_receipts,
        "side_by_side_created": True,
        "exact_target_names": True,
        "reference_rewrite": references,
        "second_run_noop": True,
        "readback": readback,
        "old_tables_preserved": True,
        "runtime_cutover": False,
        "deletion_authorized": False,
        "operator_ack": REQUIRED_FORWARD_ACK,
    })
    _write_json(args.output, result)
    return result


def run_rollback(args: argparse.Namespace) -> dict[str, Any]:
    source_head, generator_head, receipt_sha = _heads(args, REQUIRED_ROLLBACK_ACK)
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
    runner = module.MigrationRunner(_source_rows(source))
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
        "source_digest": source_digest,
        "restored_source_digest": rehearsal["restored_source_digest"],
        "restore_roundtrip": True,
        "pre_delete": True,
        "target_tables_untouched": True,
        "old_tables_preserved": True,
        "runtime_cutover": False,
        "deletion_authorized": False,
        "operator_ack": REQUIRED_ROLLBACK_ACK,
    })
    _write_json(args.output, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for operation in ("forward", "rollback"):
        command = subparsers.add_parser(operation)
        command.add_argument("--source-backup", type=Path, required=True)
        command.add_argument("--migration-receipt", type=Path, required=True)
        command.add_argument("--migration-receipt-sha256", required=True)
        command.add_argument("--source-head", required=True)
        command.add_argument("--generator-head", required=True)
        command.add_argument("--operator-ack", required=True)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--readback-raw", type=Path)
        command.add_argument("--workflow-root", type=Path, required=True)
        if operation == "rollback":
            command.add_argument("--forward-receipt", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_forward(args) if args.operation == "forward" else run_rollback(args)
    except (CutoverError, OSError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
