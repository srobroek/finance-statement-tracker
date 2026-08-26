#!/usr/bin/env python3
"""Run receipt-bound four-table migration proofs.

The runner composes ``generate_data_table_migration.py`` and the redacted
readback parser. Runtime actions are deliberately limited to the disposable
bootstrap workflow and its persisted pre-delete reverse transition.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
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
LIVE_EXPORT_SCHEMA = "finance-four-table-live-export-v1"
LOCK_RECEIPT_SCHEMA = "finance-four-table-writer-lock-v1"
PRECONDITION_SCHEMA = "finance-four-table-precondition-v1"
LOCK_NAME = "finance_four_table_cutover"
LOCK_RESOURCE_PREFIX = "finance_four_table_cutover"
LIVE_EXPORT_FILENAME = "finance-four-table-live-export.json"
LOCK_FILENAME = "finance-four-table-cutover.lock"
LOCK_RECEIPT_FILENAME = "finance-four-table-lock-receipt.json"
EXPECTED_REFERENCE_ACTIONS = {
    "69bc4d4c53bb6d6c": "remove_legacy_selector_bind_repository_contract",
    "0aa028c8c6718006": "remove_legacy_selector_bind_repository_contract",
    "2cf0d630316ed869": "remove_legacy_selector_bind_repository_contract",
    "5cdf4624fc19e530": "rewrite_selector_bind_live_id_finance_ingestion_state",
    "2350a8eab53e3a4c": "rewrite_selector_bind_live_id_finance_documents",
    "0a715ebcb4ec89b1": "rewrite_selector_bind_live_id_finance_documents",
    "49ea5a109b9c1364": "rewrite_selector_bind_live_id_finance_documents",
    "1aa95e1b762c882d": "rewrite_selector_bind_live_id_finance_documents",
    "cc7f3f4d50c5558b": "rewrite_selector_bind_live_id_finance_documents",
    "dfe99dc3203f61a9": "rewrite_selector_bind_live_id_finance_documents",
    "3db0ee3211804d57": "rewrite_selector_bind_live_id_finance_documents",
    "17ce600d102fd604": "rewrite_selector_bind_live_id_finance_documents",
    "2cff1af31f4edb7e": "rewrite_selector_bind_live_id_finance_documents",
    "31fbff104b25f7db": "rewrite_selector_bind_live_id_finance_documents",
    "29c9d87af62cc285": "rewrite_selector_bind_live_id_finance_documents",
    "f5f15e7ba51f763d": "rewrite_selector_bind_live_id_finance_documents",
    "f4765c3f0a27648d": "rewrite_selector_bind_live_id_finance_documents",
    "aceeeb3fae25df6d": "rewrite_selector_bind_live_id_finance_documents",
    "d5bc2da3a9259da3": "rewrite_selector_bind_live_id_finance_documents",
    "4d4f532c113b4146": "remove_legacy_selector_preserve_execution_history_receipt",
    "e00db431d8a31659": "remove_legacy_selector_preserve_execution_history_receipt",
    "445f262510fc7385": "remove_legacy_selector_preserve_execution_history_receipt",
    "d291b77fff83d018": "remove_legacy_selector_preserve_execution_history_receipt",
    "0043e6578ca2d069": "remove_legacy_selector_preserve_execution_history_receipt",
    "917a90ed37df6e2e": "remove_legacy_selector_preserve_execution_history_receipt",
    "4bc96e0ae665ea19": "rewrite_selector_bind_live_id_finance_actual_batches",
    "193cabb690bb918e": "rewrite_selector_bind_live_id_finance_actual_batches",
    "f828a8c9b2fcc3b3": "remove_legacy_selector_bind_mcp_audit_contract",
    "a0799010d763cd86": "remove_legacy_selector_bind_mcp_audit_contract",
    "439708cd77695352": "remove_legacy_selector_bind_mcp_audit_contract",
    "e970341672eab21f": "remove_legacy_selector_bind_mcp_audit_contract",
    "6f99252a931a2e20": "remove_legacy_selector_bind_mcp_audit_contract",
    "5c77dce64a30fe30": "remove_legacy_selector_bind_mcp_audit_contract",
}
DEFAULT_OPERATION_NONCE = "r6-20260826-orc-partial-cutover-recovery-plan"
APPROVED_QUIESCENCE_RECEIPT_DIGEST = "74b77a7f4c1c870815bbde8cf4563b20984d76785d076a050fcef8880a7a4b69"
APPROVED_PROTECTED_EXPORT_DIGEST = "e6a226d0d7c6949e1d4263505f8bcf2405aba5f908eeb09bb7427ebb5f86f154"
APPROVED_CONTRACT_BIJECTION_DIGEST = "b8c25ec57b00e1bd8b511a33fa576d390d3a46c7aa58708237268cb51c29d00a"
ABSENT_REFERENCE_TARGETS = {
    "finance_pipeline_runs": "finance_ingestion_state",
}
LEGACY_TABLE_IDS = {
    "finance_source_contracts": "sha256:73b62207",
    "finance_source_cursors": "sha256:60e428cd",
    "finance_archive_receipts": "sha256:49bf4e32",
    "finance_document_operations": "sha256:2ad2a52a",
    "finance_pipeline_runs": "sha256:48eb19e5",
    "finance_reconciliations": "sha256:f47bf1e1",
    "finance_mcp_requests": "sha256:3b9034f0",
}


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


def _require_protected(path: Path, label: str = "PROTECTED_RECEIPT") -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise CutoverError(f"{label}_UNAVAILABLE:{path.name}") from error
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise CutoverError(f"{label}_MODE_REQUIRED:{path.name}")


def _require_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX_DIGEST.fullmatch(value) is None:
        raise CutoverError(f"{label}_INVALID")
    return value


def _require_head(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEAD.fullmatch(value) is None:
        raise CutoverError(f"{label}_INVALID")
    return value


def _require_text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value and not allow_empty):
        raise CutoverError(f"{label}_INVALID")
    return value


def _binding_inputs(args: argparse.Namespace, export_sha: str) -> dict[str, str]:
    """Resolve the one operation binding shared by every cutover receipt."""
    operation_nonce = _require_text(
        getattr(args, "operation_nonce", None) or DEFAULT_OPERATION_NONCE,
        "OPERATION_NONCE",
    )
    quiescence_digest = _require_digest(
        getattr(args, "protected_quiescence_receipt_digest", None)
        or APPROVED_QUIESCENCE_RECEIPT_DIGEST,
        "PROTECTED_QUIESCENCE_RECEIPT_DIGEST",
    )
    required_export_digest = _require_digest(
        getattr(args, "required_live_export_digest", None) or APPROVED_PROTECTED_EXPORT_DIGEST,
        "REQUIRED_LIVE_EXPORT_DIGEST",
    )
    contract_digest = _require_digest(
        getattr(args, "contract_bijection_digest", None)
        or APPROVED_CONTRACT_BIJECTION_DIGEST,
        "CONTRACT_BIJECTION_DIGEST",
    )
    return {
        "operation_nonce": operation_nonce,
        "protected_quiescence_receipt_digest": quiescence_digest,
        "required_live_export_digest": required_export_digest,
        "contract_bijection_digest": contract_digest,
    }


def _validate_binding(
    value: Mapping[str, Any], binding: Mapping[str, str], label: str
) -> None:
    for field, expected in binding.items():
        if value.get(field) != expected:
            raise CutoverError(f"{label}_{field.upper()}_MISMATCH")


def _atomic_write(path: Path, payload: bytes) -> None:
    """Durably replace a protected receipt without exposing a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor != -1:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


@contextlib.contextmanager
def _exclusive_writer_lock(path: Path):
    """Hold one process-safe writer lock for the complete operation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        _require_protected(path, "WRITER_LOCK")
        descriptor = os.open(path, os.O_RDWR)
    else:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        os.fchmod(descriptor, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise CutoverError("EXCLUSIVE_WRITER_LOCK_UNAVAILABLE") from error
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _reference_id(source_table: str, reference: Mapping[str, Any]) -> str:
    identity = "|".join(
        (
            source_table,
            _require_text(reference.get("file"), "REFERENCE_FILE"),
            _require_text(reference.get("node"), "REFERENCE_NODE"),
            _require_text(reference.get("operation"), "REFERENCE_OPERATION"),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _reference_inventory(matrix: Mapping[str, Any]) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for table in matrix.get("tables", []):
        if not isinstance(table, Mapping):
            raise CutoverError("REFERENCE_INVENTORY_INVALID")
        source_table = _require_text(table.get("source_table"), "REFERENCE_SOURCE_TABLE")
        references = table.get("node_references")
        if not isinstance(references, list):
            raise CutoverError("REFERENCE_INVENTORY_INVALID")
        for reference in references:
            if not isinstance(reference, Mapping):
                raise CutoverError("REFERENCE_INVENTORY_INVALID")
            identifier = _reference_id(source_table, reference)
            action = EXPECTED_REFERENCE_ACTIONS.get(identifier)
            if action is None:
                raise CutoverError(f"UNDECLARED_REFERENCE_ACTION:{identifier}")
            target = table.get("target_table")
            if action.endswith(("repository_contract", "mcp_audit_contract", "execution_history_receipt")):
                target = None
            elif target is None:
                target = ABSENT_REFERENCE_TARGETS.get(source_table)
            elif target not in TARGETS:
                raise CutoverError(f"REFERENCE_TARGET_UNDECLARED:{identifier}")
            if target is not None and target not in TARGETS:
                raise CutoverError(f"REFERENCE_TARGET_UNDECLARED:{identifier}")
            inventory.append(
                {
                    "reference_id": identifier,
                    "action": action,
                    "source_table": source_table,
                    "workflow_path": reference.get("file"),
                    "node_name": reference.get("node"),
                    "operation": reference.get("operation"),
                    "canonical_table_name": target,
                    "filter_keys": list(reference.get("filter_keys", [])),
                }
            )
    if len(inventory) != len(EXPECTED_REFERENCE_ACTIONS) or {
        item["reference_id"] for item in inventory
    } != set(EXPECTED_REFERENCE_ACTIONS):
        raise CutoverError("COMPLETE_REFERENCE_ACTION_MAP_REQUIRED")
    if {item["source_table"] for item in inventory} != set(LEGACY_TABLE_IDS):
        raise CutoverError("EXACT_SEVEN_LEGACY_TABLE_ID_MAP_REQUIRED")
    for item in inventory:
        item["legacy_table_id"] = LEGACY_TABLE_IDS[item["source_table"]]
    return sorted(inventory, key=lambda item: item["reference_id"])


def _export_without_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result.pop("export_sha256", None)
    result.pop("workflow_export_sha256", None)
    return result


def _validate_live_export(
    path: Path,
    *,
    source_head: str,
    generator_head: str,
    migration_receipt_sha: str,
    source_backup_sha: str,
    identity_digest: str,
    matrix: Mapping[str, Any],
) -> dict[str, Any]:
    _require_protected(path, "PROTECTED_LIVE_EXPORT")
    export, raw = _read_json(path)
    if export.get("schema_version") != LIVE_EXPORT_SCHEMA:
        raise CutoverError("LIVE_EXPORT_SCHEMA_INVALID")
    export_sha = export.get("export_sha256", export.get("workflow_export_sha256"))
    export_sha = _require_digest(export_sha, "LIVE_EXPORT_SHA256")
    if hashlib.sha256(_canonical_bytes(_export_without_hash(export))).hexdigest() != export_sha:
        raise CutoverError("LIVE_EXPORT_INTEGRITY_MISMATCH")
    if export.get("repository_root") != str(ROOT):
        raise CutoverError("LIVE_EXPORT_REPOSITORY_ROOT_MISMATCH")
    project_id = _require_text(export.get("project_id"), "LIVE_EXPORT_PROJECT_ID")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", project_id):
        raise CutoverError("LIVE_EXPORT_PROJECT_ID_INVALID")
    for field, expected in (
        ("source_head", source_head),
        ("generator_head", generator_head),
        ("migration_receipt_sha256", migration_receipt_sha),
        ("source_backup_sha256", source_backup_sha),
        ("accepted_identity_sha256", identity_digest),
    ):
        if export.get(field) != expected:
            raise CutoverError(f"LIVE_EXPORT_{field.upper()}_MISMATCH")
    if export.get("redacted") is not True:
        raise CutoverError("LIVE_EXPORT_REDACTION_REQUIRED")
    workflows = export.get("workflows")
    if export.get("workflow_count") != 19 or not isinstance(workflows, list) or len(workflows) != 19:
        raise CutoverError("EXACT_19_WORKFLOW_EXPORT_REQUIRED")
    if export.get("in_flight") != 0:
        raise CutoverError("WORKFLOW_QUIESCENCE_REQUIRED")
    workflow_ids: set[str] = set()
    workflow_revisions: dict[str, str] = {}
    for workflow in workflows:
        if not isinstance(workflow, Mapping):
            raise CutoverError("LIVE_EXPORT_WORKFLOW_INVALID")
        workflow_id = _require_text(workflow.get("workflow_id"), "WORKFLOW_ID")
        if workflow_id in workflow_ids:
            raise CutoverError("DUPLICATE_WORKFLOW_ID")
        workflow_ids.add(workflow_id)
        if (
            workflow.get("active") is not False
            or workflow.get("published") is not False
            or workflow.get("in_flight") != 0
        ):
            raise CutoverError("WORKFLOW_NOT_QUIESCENT")
        revision_id = _require_text(workflow.get("revision_id"), "WORKFLOW_REVISION_ID")
        if workflow_id in workflow_revisions:
            raise CutoverError("DUPLICATE_WORKFLOW_ID")
        workflow_revisions[workflow_id] = revision_id
    target_ids = export.get("targets")
    if (
        not isinstance(target_ids, list)
        or not all(isinstance(item, Mapping) for item in target_ids)
        or [item.get("name") for item in target_ids] != sorted(TARGETS)
    ):
        raise CutoverError("EXACT_TARGET_EXPORT_REQUIRED")
    expected_schemas = _target_schema_digests(matrix)
    for target in target_ids:
        _require_text(target.get("table_id"), "LIVE_TARGET_ID")
        if target.get("schema_sha256") != expected_schemas[target["name"]]:
            raise CutoverError(f"LIVE_TARGET_SCHEMA_DIGEST_MISMATCH:{target['name']}")
    inventory = _reference_inventory(matrix)
    references = export.get("references")
    if not isinstance(references, list) or len(references) != len(inventory):
        raise CutoverError("COMPLETE_LIVE_REFERENCE_EXPORT_REQUIRED")
    by_id = {}
    for reference in references:
        if not isinstance(reference, Mapping):
            raise CutoverError("LIVE_REFERENCE_INVALID")
        identifier = _require_text(reference.get("reference_id"), "LIVE_REFERENCE_ID")
        if identifier in by_id:
            raise CutoverError("DUPLICATE_LIVE_REFERENCE_ID")
        by_id[identifier] = reference
    if set(by_id) != set(EXPECTED_REFERENCE_ACTIONS):
        raise CutoverError("COMPLETE_LIVE_REFERENCE_EXPORT_REQUIRED")
    actions: list[dict[str, Any]] = []
    target_by_name = {item["name"]: item for item in target_ids}
    node_aliases: set[tuple[str, str]] = set()
    for expected in inventory:
        observed = by_id[expected["reference_id"]]
        for field, expected_value in (
            ("old_table_name", expected["source_table"]),
            ("workflow_path", expected["workflow_path"]),
            ("node_name", expected["node_name"]),
            ("operation", expected["operation"]),
            ("canonical_table_name", expected["canonical_table_name"]),
        ):
            if observed.get(field) != expected_value:
                raise CutoverError(f"LIVE_REFERENCE_{field.upper()}_MISMATCH:{expected['reference_id']}")
        _require_text(observed.get("workflow_id"), "LIVE_REFERENCE_WORKFLOW_ID")
        observed_workflow_id = observed["workflow_id"]
        observed_revision_id = _require_text(observed.get("revision_id"), "LIVE_REFERENCE_REVISION_ID")
        if observed_workflow_id not in workflow_revisions:
            raise CutoverError(f"LIVE_REFERENCE_WORKFLOW_UNKNOWN:{expected['reference_id']}")
        if observed_revision_id != workflow_revisions[observed_workflow_id]:
            raise CutoverError(f"LIVE_REFERENCE_REVISION_MISMATCH:{expected['reference_id']}")
        _require_text(observed.get("node_id"), "LIVE_REFERENCE_NODE_ID")
        _require_text(observed.get("old_table_id"), "LIVE_REFERENCE_OLD_TABLE_ID")
        if observed["old_table_id"] != expected["legacy_table_id"]:
            raise CutoverError(f"LIVE_REFERENCE_OLD_TABLE_ID_CONFLICT:{expected['reference_id']}")
        node_key = (observed_workflow_id, observed["node_id"])
        if node_key in node_aliases:
            raise CutoverError(f"LIVE_REFERENCE_NODE_ALIAS_CONFLICT:{expected['reference_id']}")
        node_aliases.add(node_key)
        if observed.get("active") is not False or observed.get("published") is not False or observed.get("in_flight") != 0:
            raise CutoverError("LIVE_REFERENCE_NOT_QUIESCENT")
        target_name = expected["canonical_table_name"]
        if target_name is None:
            if observed.get("canonical_table_id") not in {None, ""}:
                raise CutoverError(f"UNDECLARED_REFERENCE_TARGET:{expected['reference_id']}")
        else:
            if observed.get("canonical_table_id") != target_by_name[target_name]["table_id"]:
                raise CutoverError(f"LIVE_REFERENCE_TARGET_ID_MISMATCH:{expected['reference_id']}")
        actions.append({**expected, "workflow_id": observed["workflow_id"], "revision_id": observed["revision_id"], "node_id": observed["node_id"], "old_table_id": observed["old_table_id"], "canonical_table_id": observed.get("canonical_table_id")})
    return {
        "path": str(path),
        "project_id": project_id,
        "export_sha256": export_sha,
        "workflow_count": 19,
        "in_flight": 0,
        "redacted": True,
        "target_names": sorted(TARGETS),
        "reference_count": len(actions),
        "unresolved": [],
        "actions": actions,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _validate_lock_receipt(
    path: Path,
    *,
    export_sha: str,
    migration_receipt_sha: str,
    source_backup_sha: str,
    identity_digest: str,
    project_id: str,
    binding: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], str]:
    _require_protected(path, "PROTECTED_WRITER_LOCK_RECEIPT")
    receipt, raw = _read_json(path)
    if receipt.get("schema_version") != LOCK_RECEIPT_SCHEMA or receipt.get("lock_name") != LOCK_NAME:
        raise CutoverError("WRITER_LOCK_RECEIPT_SCHEMA_INVALID")
    for field, expected in (
        ("project_id", project_id),
        ("export_sha256", export_sha),
        ("migration_receipt_sha256", migration_receipt_sha),
        ("source_backup_sha256", source_backup_sha),
        ("accepted_identity_sha256", identity_digest),
    ):
        if receipt.get(field) != expected:
            raise CutoverError(f"WRITER_LOCK_{field.upper()}_MISMATCH")
    if binding is not None:
        _validate_binding(receipt, binding, "WRITER_LOCK")
    if receipt.get("held") is not True or receipt.get("in_flight") != 0:
        raise CutoverError("EXCLUSIVE_WRITER_PRECONDITION_REQUIRED")
    integrity = _require_digest(receipt.get("lock_receipt_sha256"), "LOCK_RECEIPT_SHA256")
    unsigned = dict(receipt)
    unsigned.pop("lock_receipt_sha256", None)
    if hashlib.sha256(_canonical_bytes(unsigned)).hexdigest() != integrity:
        raise CutoverError("WRITER_LOCK_RECEIPT_INTEGRITY_MISMATCH")
    return receipt, hashlib.sha256(raw).hexdigest()


def _lock_receipt(
    *,
    export: Mapping[str, Any],
    migration_receipt_sha: str,
    source_backup_sha: str,
    identity_digest: str,
    project_id: str,
    operation: str,
    binding: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    binding = dict(binding or {
        "operation_nonce": DEFAULT_OPERATION_NONCE,
        "protected_quiescence_receipt_digest": APPROVED_QUIESCENCE_RECEIPT_DIGEST,
        "required_live_export_digest": APPROVED_PROTECTED_EXPORT_DIGEST,
        "contract_bijection_digest": APPROVED_CONTRACT_BIJECTION_DIGEST,
    })
    return _seal_with_key(
        {
            "schema_version": LOCK_RECEIPT_SCHEMA,
            "lock_name": LOCK_NAME,
            "project_id": project_id,
            "resource_key": f"{LOCK_RESOURCE_PREFIX}:{project_id}",
            "operation": operation,
            "export_sha256": export["export_sha256"],
            "migration_receipt_sha256": migration_receipt_sha,
            "source_backup_sha256": source_backup_sha,
            "accepted_identity_sha256": identity_digest,
            **binding,
            "workflow_count": 19,
            "in_flight": 0,
            "held": True,
        },
        "lock_receipt_sha256",
    )


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
    source_backup_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    _require_protected(source_path, "PROTECTED_SOURCE_BACKUP")
    _require_protected(migration_receipt_path)
    try:
        source_bytes = source_path.read_bytes()
    except OSError as error:
        raise CutoverError(f"SOURCE_BACKUP_UNAVAILABLE:{source_path.name}") from error
    observed_source_sha = hashlib.sha256(source_bytes).hexdigest()
    if observed_source_sha != source_backup_sha256:
        raise CutoverError("SOURCE_BACKUP_SHA256_MISMATCH")
    migration_receipt, receipt_bytes = _read_json(migration_receipt_path)
    observed_sha = hashlib.sha256(receipt_bytes).hexdigest()
    if observed_sha != migration_receipt_sha256:
        raise CutoverError("MIGRATION_RECEIPT_SHA256_MISMATCH")
    if migration_receipt.get("schema_version") != "data-table-migration-receipt-v1":
        raise CutoverError("MIGRATION_RECEIPT_SCHEMA_INVALID")
    try:
        source = json.loads(source_bytes, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, CutoverError) as error:
        raise CutoverError(f"INPUT_JSON_INVALID:{source_path.name}") from error
    if not isinstance(source, dict):
        raise CutoverError(f"INPUT_OBJECT_REQUIRED:{source_path.name}")
    if source.get("schema_version") != "finance-data-table-backup-v1":
        raise CutoverError("SOURCE_BACKUP_SCHEMA_INVALID")
    if not isinstance(source.get("tables"), dict):
        raise CutoverError("SOURCE_BACKUP_TABLES_INVALID")
    return source, migration_receipt, observed_sha, observed_source_sha


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
    _atomic_write(path, _canonical_bytes(value))


def _seal(value: Mapping[str, Any]) -> dict[str, Any]:
    return _seal_with_key(value, "cutover_receipt_sha256")


def _seal_with_key(value: Mapping[str, Any], key: str) -> dict[str, Any]:
    result = dict(value)
    result.pop(key, None)
    result[key] = hashlib.sha256(_canonical_bytes(result)).hexdigest()
    return result


RUNTIME_STATE_SCHEMA = "finance-four-table-disposable-runtime-state-v1"


def _runtime_state_without_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result.pop("runtime_state_sha256", None)
    return result


def _write_runtime_state(path: Path, value: Mapping[str, Any]) -> str:
    state = _seal_with_key(_runtime_state_without_hash(value), "runtime_state_sha256")
    _write_json(path, state)
    return state["runtime_state_sha256"]


def _read_runtime_state(path: Path) -> tuple[dict[str, Any], str]:
    _require_protected(path)
    state, _ = _read_json(path)
    if state.get("schema_version") != RUNTIME_STATE_SCHEMA:
        raise CutoverError("RUNTIME_STATE_SCHEMA_INVALID")
    observed = _require_digest(state.get("runtime_state_sha256"), "RUNTIME_STATE_SHA256")
    if hashlib.sha256(_canonical_bytes(_runtime_state_without_hash(state))).hexdigest() != observed:
        raise CutoverError("RUNTIME_STATE_INTEGRITY_MISMATCH")
    return state, observed


def _validate_runtime_state(
    state: Mapping[str, Any],
    *,
    operation: str,
    receipt_sha: str,
    source_head: str,
    generator_head: str,
    identity_digest: str,
    source_digest: str,
    source_backup_sha256: str,
    workflow_export_sha256: str | None = None,
    lock_receipt_sha256: str | None = None,
    binding: Mapping[str, str] | None = None,
) -> None:
    if (
        state.get("schema_version") != RUNTIME_STATE_SCHEMA
        or state.get("operation") != operation
        or state.get("migration_receipt_sha256") != receipt_sha
        or state.get("source_head") != source_head
        or state.get("generator_head") != generator_head
        or state.get("accepted_identity_sha256") != identity_digest
        or state.get("source_digest") != source_digest
        or state.get("source_backup_sha256") != source_backup_sha256
        or state.get("old_tables_preserved") is not True
        or state.get("runtime_cutover") is not False
        or state.get("deletion_authorized") is not False
    ):
        raise CutoverError("RUNTIME_STATE_BINDING_MISMATCH")
    if workflow_export_sha256 is not None and state.get("workflow_export_sha256") != workflow_export_sha256:
        raise CutoverError("RUNTIME_STATE_EXPORT_BINDING_MISMATCH")
    if lock_receipt_sha256 is not None and state.get("lock_receipt_sha256") != lock_receipt_sha256:
        raise CutoverError("RUNTIME_STATE_LOCK_BINDING_MISMATCH")
    if binding is not None:
        _validate_binding(state, binding, "RUNTIME_STATE")


def _parse_readback(path: Path, migration_sha256: str, expected_phase: str) -> dict[str, Any]:
    parser_spec = importlib.util.spec_from_file_location("finance_readback_parser", READBACK_PARSER_PATH)
    if parser_spec is None or parser_spec.loader is None:
        raise CutoverError("READBACK_PARSER_UNAVAILABLE")
    parser = importlib.util.module_from_spec(parser_spec)
    parser_spec.loader.exec_module(parser)
    raw = path.read_text(encoding="utf-8")
    prefix = "finance data table digest verified:"
    try:
        raw_payload = parser.extract_payload(raw, prefix)
        payload = parser.parse_data_table_receipt(raw, expected_phase=expected_phase)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise CutoverError("READBACK_RECEIPT_INVALID") from error
    migration_receipt = payload.get("migration_receipt")
    if (
        not isinstance(migration_receipt, dict)
        or migration_receipt.get("bound") is not True
        or migration_receipt.get("sha256") != migration_sha256
    ):
        raise CutoverError("READBACK_MIGRATION_RECEIPT_MISMATCH")
    if raw_payload.get("status") in {"FORWARD_PRE_READBACK", "ROLLBACK_PRE_READBACK"}:
        return {
            "verified": True,
            "phase": expected_phase,
            "digest_sha256": payload["digest_sha256"],
            "finance_tables": 0,
            "total_rows": 0,
            "tables": [],
        }
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
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    matrix: Mapping[str, Any],
    *,
    before_phase: str,
    after_phase: str,
) -> dict[str, Any]:
    if not before.get("verified") or not after.get("verified"):
        raise CutoverError("PRE_POST_READBACK_REQUIRED")
    if before.get("phase") != before_phase or after.get("phase") != after_phase:
        raise CutoverError("READBACK_PHASE_MISMATCH")
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
    if first.get("phase") != "FORWARD_POST" or second.get("phase") != "FORWARD_POST":
        raise CutoverError("FORWARD_POST_READBACK_REQUIRED")
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


def _heads(
    args: argparse.Namespace, expected_ack: str, expected_action: str
) -> tuple[str, str, str, str, str]:
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
    source_backup_sha = _require_digest(
        args.source_backup_sha256, "SOURCE_BACKUP_SHA256"
    )
    if identity.get("migration_receipt_sha256") != receipt_sha:
        raise CutoverError("ACCEPTED_MIGRATION_RECEIPT_SHA256_MISMATCH")
    if identity.get("source_backup_sha256") != source_backup_sha:
        raise CutoverError("ACCEPTED_SOURCE_BACKUP_SHA256_MISMATCH")
    if args.operator_ack != expected_ack or args.runtime_action != expected_action:
        raise CutoverError("NAMED_OPERATOR_ACK_REQUIRED")
    return source_head, generator_head, receipt_sha, source_backup_sha, identity_digest


def _bound_live_inputs(
    args: argparse.Namespace,
    *,
    source_head: str,
    generator_head: str,
    receipt_sha: str,
    source_backup_sha: str,
    identity_digest: str,
    operation: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None, dict[str, str]]:
    live_export_path = getattr(args, "live_export", None) or args.migration_receipt.with_name(LIVE_EXPORT_FILENAME)
    lock_receipt_path = getattr(args, "lock_receipt", None) or args.migration_receipt.with_name(LOCK_RECEIPT_FILENAME)
    matrix = _load_matrix()
    export = _validate_live_export(
        live_export_path,
        source_head=source_head,
        generator_head=generator_head,
        migration_receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        matrix=matrix,
    )
    project_id = export["project_id"]
    binding = _binding_inputs(args, export["export_sha256"])
    lock_receipt, lock_sha = _validate_lock_receipt(
        lock_receipt_path,
        export_sha=export["export_sha256"],
        migration_receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        project_id=project_id,
        binding=binding,
    )
    if lock_receipt.get("operation") not in {operation, "PRECONDITION"}:
        raise CutoverError("WRITER_LOCK_OPERATION_MISMATCH")
    return export, lock_receipt, lock_sha, binding


def _assert_currentness(
    args: argparse.Namespace,
    *,
    source_head: str,
    generator_head: str,
    receipt_sha: str,
    source_backup_sha: str,
    identity_digest: str,
    export_sha: str | None,
) -> None:
    """Reject a source, receipt, or export changed after preflight."""
    try:
        observed_head = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        observed_generator = subprocess.run(
            [sys.executable, str(MIGRATION_PATH), "--schema-digest"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()[-1].strip()
    except (OSError, subprocess.CalledProcessError, IndexError) as error:
        raise CutoverError("CURRENTNESS_CHECK_UNAVAILABLE") from error
    if observed_head != source_head or observed_generator != generator_head:
        raise CutoverError("SOURCE_CURRENTNESS_DRIFT")
    try:
        observed_source_sha = hashlib.sha256(args.source_backup.read_bytes()).hexdigest()
        observed_receipt_sha = hashlib.sha256(args.migration_receipt.read_bytes()).hexdigest()
    except OSError as error:
        raise CutoverError("CURRENTNESS_INPUT_UNAVAILABLE") from error
    if observed_source_sha != source_backup_sha or observed_receipt_sha != receipt_sha:
        raise CutoverError("RECEIPT_CURRENTNESS_DRIFT")
    if export_sha is not None:
        export_path = getattr(args, "live_export", None) or args.migration_receipt.with_name(LIVE_EXPORT_FILENAME)
        export = _read_json(export_path)[0]
        observed_export_sha = export.get("export_sha256", export.get("workflow_export_sha256"))
        if observed_export_sha != export_sha:
            raise CutoverError("LIVE_EXPORT_CURRENTNESS_DRIFT")
    if args.accepted_identity:
        identity, _ = _read_json(args.accepted_identity)
        observed_identity = _require_digest(identity.get("identity_sha256"), "IDENTITY_SHA256")
        if observed_identity != identity_digest:
            raise CutoverError("ACCEPTED_IDENTITY_CURRENTNESS_DRIFT")


def run_forward(args: argparse.Namespace) -> dict[str, Any]:
    source_head, generator_head, receipt_sha, source_backup_sha, identity_digest = _heads(
        args, REQUIRED_FORWARD_ACK, FORWARD_RUNTIME_ACTION
    )
    export, lock_receipt, lock_sha, binding = _bound_live_inputs(
        args,
        source_head=source_head,
        generator_head=generator_head,
        receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        operation="FORWARD",
    )
    _assert_currentness(
        args,
        source_head=source_head,
        generator_head=generator_head,
        receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        export_sha=export["export_sha256"] if export else None,
    )
    source, expected_receipt, _, observed_source_backup_sha = _source_and_receipt(
        args.source_backup, args.migration_receipt, receipt_sha, source_backup_sha
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
    runtime_state_sha = _write_runtime_state(
        args.runtime_state,
        {
            "schema_version": RUNTIME_STATE_SCHEMA,
            "operation": "FORWARD",
            "status": "MIGRATED",
            "migration_receipt_sha256": receipt_sha,
            "source_head": source_head,
            "generator_head": generator_head,
            "accepted_identity_sha256": identity_digest,
            "source_backup_sha256": observed_source_backup_sha,
            "source_digest": first["source_digest"],
            "target_digest": first["target_digest"],
            "target_tables_created": True,
            "old_tables_preserved": True,
            "runtime_cutover": False,
            "deletion_authorized": False,
            "target_tables_untouched": False,
            "restored_source_digest": None,
            "runtime_action": FORWARD_RUNTIME_ACTION,
            "operator_ack": REQUIRED_FORWARD_ACK,
            "workflow_export_sha256": export["export_sha256"] if export else None,
            "lock_receipt_sha256": lock_sha,
            **binding,
        },
    )
    result = _seal({
        "schema_version": "finance-four-table-cutover-receipt-v1",
        "operation": "FORWARD",
        "migration_receipt_sha256": receipt_sha,
        **binding,
        "source_head": source_head,
        "generator_head": generator_head,
        "accepted_identity_sha256": identity_digest,
        "source_backup_sha256": observed_source_backup_sha,
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
        "runtime_state_sha256": runtime_state_sha,
        "old_tables_preserved": True,
        "runtime_cutover": False,
        "deletion_authorized": False,
        "operator_ack": REQUIRED_FORWARD_ACK,
    })
    if export is not None:
        result.update(
            {
                "workflow_export_sha256": export["export_sha256"],
                "reference_action_plan": {
                    "reference_count": export["reference_count"],
                    "actions": export["actions"],
                    "unresolved": export["unresolved"],
                    "replay_noop": True,
                },
                "exclusive_writer_precondition": {
                    "lock_name": LOCK_NAME,
                    "project_id": export["project_id"],
                    "lock_receipt_sha256": lock_sha,
                    "held": lock_receipt["held"] if lock_receipt else False,
                    "in_flight": lock_receipt["in_flight"] if lock_receipt else None,
                },
            }
        )
        result = _seal(result)
    _assert_currentness(
        args,
        source_head=source_head,
        generator_head=generator_head,
        receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        export_sha=export["export_sha256"] if export else None,
    )
    _write_json(args.output, result)
    return result


def run_rollback(args: argparse.Namespace) -> dict[str, Any]:
    source_head, generator_head, receipt_sha, source_backup_sha, identity_digest = _heads(
        args, REQUIRED_ROLLBACK_ACK, ROLLBACK_RUNTIME_ACTION
    )
    export, lock_receipt, lock_sha, binding = _bound_live_inputs(
        args,
        source_head=source_head,
        generator_head=generator_head,
        receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        operation="ROLLBACK",
    )
    _assert_currentness(
        args,
        source_head=source_head,
        generator_head=generator_head,
        receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        export_sha=export["export_sha256"] if export else None,
    )
    source, migration_receipt, _, observed_source_backup_sha = _source_and_receipt(
        args.source_backup, args.migration_receipt, receipt_sha, source_backup_sha
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
        or forward.get("source_backup_sha256") != observed_source_backup_sha
        or forward.get("old_tables_preserved") is not True
        or forward.get("runtime_cutover") is not False
        or forward.get("deletion_authorized") is not False
        or (
            export is not None
            and forward.get("workflow_export_sha256") != export["export_sha256"]
        )
    ):
        raise CutoverError("FORWARD_RECEIPT_BINDING_MISMATCH")
    _validate_binding(forward, binding, "FORWARD_RECEIPT")
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
    if runner.run() != migration_receipt:
        raise CutoverError("MIGRATION_RECEIPT_CONTENT_MISMATCH")
    expected_tables = _target_table_receipts(runner, matrix)
    before = _parse_readback(args.pre_readback_raw, receipt_sha, "ROLLBACK_PRE")
    after = _parse_readback(args.post_readback_raw, receipt_sha, "ROLLBACK_POST")
    readback = _compare_readbacks(
        before, after, matrix, before_phase="ROLLBACK_PRE", after_phase="ROLLBACK_POST"
    )
    expected = {table["name"]: table for table in expected_tables}
    for table in readback["tables"]:
        wanted = expected.get(table["name"])
        if wanted is None or any(
            table[field] != wanted[field]
            for field in ("schema_sha256", "row_count", "rows_sha256")
        ):
            raise CutoverError(f"RUNTIME_PROJECTION_DIGEST_MISMATCH:{table['name']}")
    runtime_state, runtime_state_sha = _read_runtime_state(args.runtime_state)
    _validate_runtime_state(
        runtime_state,
        operation="ROLLBACK",
        receipt_sha=receipt_sha,
        source_head=source_head,
        generator_head=generator_head,
        identity_digest=identity_digest,
        source_digest=migration_receipt["source_digest"],
        source_backup_sha256=observed_source_backup_sha,
        workflow_export_sha256=export["export_sha256"] if export else None,
        lock_receipt_sha256=lock_sha,
        binding=binding,
    )
    if (
        runtime_state.get("status") != "RESTORED"
        or runtime_state.get("target_tables_created") is not True
        or runtime_state.get("target_tables_untouched") is not True
        or runtime_state.get("restored_source_digest") != migration_receipt.get("source_digest")
    ):
        raise CutoverError("RUNTIME_STATE_RESTORATION_REQUIRED")
    _verify_runtime_proof(
        args.runtime_proof,
        receipt_sha,
        source_head,
        generator_head,
        identity_digest,
        source,
        observed_source_backup_sha,
        runtime_state_sha,
        workflow_export_sha256=export["export_sha256"] if export else None,
        lock_receipt_sha256=lock_sha,
        binding=binding,
    )
    _require_protected(args.source_backup, "PROTECTED_SOURCE_BACKUP")
    try:
        current_source_backup_sha = hashlib.sha256(args.source_backup.read_bytes()).hexdigest()
    except OSError as error:
        raise CutoverError("SOURCE_BACKUP_UNAVAILABLE_AFTER_ROLLBACK") from error
    if current_source_backup_sha != observed_source_backup_sha:
        raise CutoverError("SOURCE_BACKUP_CHANGED_DURING_ROLLBACK")
    source_digest = migration_receipt.get("source_digest")
    if (
        runtime_state.get("restore_roundtrip") is not True
        or runtime_state.get("source_digest") != source_digest
        or runtime_state.get("restored_source_digest") != source_digest
        or runtime_state.get("target_tables_untouched") is not True
    ):
        raise CutoverError("EXACT_ROLLBACK_DIGEST_RESTORATION_REQUIRED")
    result = _seal({
        "schema_version": "finance-four-table-cutover-receipt-v1",
        "operation": "ROLLBACK",
        "migration_receipt_sha256": receipt_sha,
        **binding,
        "source_head": source_head,
        "generator_head": generator_head,
        "accepted_identity_sha256": identity_digest,
        "source_backup_sha256": observed_source_backup_sha,
        "source_digest": source_digest,
        "restored_source_digest": runtime_state["restored_source_digest"],
        "restore_roundtrip": runtime_state["restore_roundtrip"],
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
        "runtime_state_sha256": runtime_state_sha,
    })
    if export is not None:
        result.update(
            {
                "workflow_export_sha256": export["export_sha256"],
                "reference_action_plan": {
                    "reference_count": export["reference_count"],
                    "actions": export["actions"],
                    "unresolved": export["unresolved"],
                    "replay_noop": True,
                },
                "exclusive_writer_precondition": {
                    "lock_name": LOCK_NAME,
                    "project_id": export["project_id"],
                    "lock_receipt_sha256": lock_sha,
                    "held": lock_receipt["held"] if lock_receipt else False,
                    "in_flight": lock_receipt["in_flight"] if lock_receipt else None,
                },
            }
        )
        result = _seal(result)
    _assert_currentness(
        args,
        source_head=source_head,
        generator_head=generator_head,
        receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        export_sha=export["export_sha256"] if export else None,
    )
    _write_json(args.output, result)
    return result


def _verify_runtime_proof(
    path: Path,
    receipt_sha: str,
    source_head: str,
    generator_head: str,
    identity_digest: str,
    source: Mapping[str, Any],
    source_backup_sha256: str,
    runtime_state_sha: str,
    workflow_export_sha256: str | None = None,
    lock_receipt_sha256: str | None = None,
    binding: Mapping[str, str] | None = None,
) -> None:
    _require_protected(path)
    proof, _ = _read_json(path)
    if (
        proof.get("schema_version") != "data-table-reverse-runtime-proof-v1"
        or proof.get("migration_receipt_sha256") != receipt_sha
        or proof.get("source_head") != source_head
        or proof.get("generator_head") != generator_head
        or proof.get("accepted_identity_sha256") != identity_digest
        or proof.get("source_backup_sha256") != source_backup_sha256
        or proof.get("operator_ack") != REQUIRED_ROLLBACK_ACK
        or proof.get("runtime_action") != ROLLBACK_RUNTIME_ACTION
        or proof.get("runtime_command") != "rollback-runtime"
        or proof.get("runtime_state_sha256") != runtime_state_sha
        or proof.get("pre_delete") is not True
        or proof.get("restore_roundtrip") is not True
        or proof.get("target_tables_untouched") is not True
    ):
        raise CutoverError("ROLLBACK_RUNTIME_PROOF_BINDING_MISMATCH")
    if workflow_export_sha256 is not None and proof.get("workflow_export_sha256") != workflow_export_sha256:
        raise CutoverError("ROLLBACK_RUNTIME_PROOF_EXPORT_MISMATCH")
    if lock_receipt_sha256 is not None and proof.get("lock_receipt_sha256") != lock_receipt_sha256:
        raise CutoverError("ROLLBACK_RUNTIME_PROOF_LOCK_MISMATCH")
    if binding is not None:
        _validate_binding(proof, binding, "ROLLBACK_RUNTIME_PROOF")
    source_digest = _load_migration_module().MigrationRunner(_source_rows(source)).backup_digest()
    if proof.get("source_digest") != source_digest or proof.get("restored_source_digest") != source_digest:
        raise CutoverError("ROLLBACK_RUNTIME_PROOF_DIGEST_MISMATCH")
    integrity = _require_digest(proof.get("runtime_proof_sha256"), "RUNTIME_PROOF_SHA256")
    unsigned = dict(proof)
    unsigned.pop("runtime_proof_sha256", None)
    if hashlib.sha256(_canonical_bytes(unsigned)).hexdigest() != integrity:
        raise CutoverError("ROLLBACK_RUNTIME_PROOF_INTEGRITY_MISMATCH")


def run_rollback_runtime(args: argparse.Namespace) -> dict[str, Any]:
    source_head, generator_head, receipt_sha, source_backup_sha, identity_digest = _heads(
        args, REQUIRED_ROLLBACK_ACK, ROLLBACK_RUNTIME_ACTION
    )
    export, _lock_receipt, lock_sha, binding = _bound_live_inputs(
        args,
        source_head=source_head,
        generator_head=generator_head,
        receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        operation="ROLLBACK",
    )
    _assert_currentness(
        args,
        source_head=source_head,
        generator_head=generator_head,
        receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        export_sha=export["export_sha256"] if export else None,
    )
    source, migration_receipt, _, observed_source_backup_sha = _source_and_receipt(
        args.source_backup, args.migration_receipt, receipt_sha, source_backup_sha
    )
    if migration_receipt.get("old_tables_preserved") is not True or migration_receipt.get(
        "deletion_authorized"
    ) is not False:
        raise CutoverError("ROLLBACK_ONLY_BEFORE_LEGACY_DELETION")
    module = _load_migration_module()
    runner = module.MigrationRunner(_source_rows(source))
    if runner.run() != migration_receipt:
        raise CutoverError("MIGRATION_RECEIPT_CONTENT_MISMATCH")
    source_digest = migration_receipt.get("source_digest")
    runtime_state, previous_state_sha = _read_runtime_state(args.runtime_state)
    _validate_runtime_state(
        runtime_state,
        operation="FORWARD",
        receipt_sha=receipt_sha,
        source_head=source_head,
        generator_head=generator_head,
        identity_digest=identity_digest,
        source_digest=source_digest,
        source_backup_sha256=observed_source_backup_sha,
        workflow_export_sha256=export["export_sha256"] if export else None,
        lock_receipt_sha256=lock_sha,
        binding=binding,
    )
    if (
        runtime_state.get("status") != "MIGRATED"
        or runtime_state.get("target_tables_created") is not True
        or runtime_state.get("target_tables_untouched") is not False
    ):
        raise CutoverError("FORWARD_RUNTIME_STATE_REQUIRED")
    restored = runner.restore_backup()
    source_digest = migration_receipt.get("source_digest")
    if (
        restored.get("restore_roundtrip") is not True
        or runner.backup_digest() != source_digest
        or restored.get("source_digest") != source_digest
    ):
        raise CutoverError("EXACT_ROLLBACK_DIGEST_RESTORATION_REQUIRED")
    _require_protected(args.source_backup, "PROTECTED_SOURCE_BACKUP")
    try:
        current_source_backup_sha = hashlib.sha256(args.source_backup.read_bytes()).hexdigest()
    except OSError as error:
        raise CutoverError("SOURCE_BACKUP_UNAVAILABLE_AFTER_ROLLBACK") from error
    if current_source_backup_sha != observed_source_backup_sha:
        raise CutoverError("SOURCE_BACKUP_CHANGED_DURING_ROLLBACK")
    restored_state_sha = _write_runtime_state(
        args.runtime_state,
        {
            "schema_version": RUNTIME_STATE_SCHEMA,
            "operation": "ROLLBACK",
            "status": "RESTORED",
            "migration_receipt_sha256": receipt_sha,
            "source_head": source_head,
            "generator_head": generator_head,
            "accepted_identity_sha256": identity_digest,
            "source_backup_sha256": observed_source_backup_sha,
            "source_digest": source_digest,
            "target_digest": migration_receipt["target_digest"],
            "target_tables_created": True,
            "old_tables_preserved": True,
            "runtime_cutover": False,
            "deletion_authorized": False,
            "target_tables_untouched": True,
            "restored_source_digest": restored["source_digest"],
            "restore_roundtrip": restored["restore_roundtrip"],
            "runtime_state_before_sha256": previous_state_sha,
            "runtime_action": ROLLBACK_RUNTIME_ACTION,
            "operator_ack": REQUIRED_ROLLBACK_ACK,
            "workflow_export_sha256": export["export_sha256"] if export else None,
            "lock_receipt_sha256": lock_sha,
            **binding,
        },
    )
    result = _seal(
        {
            "schema_version": "data-table-reverse-runtime-proof-v1",
            "migration_receipt_sha256": receipt_sha,
            "source_head": source_head,
            "generator_head": generator_head,
            "accepted_identity_sha256": identity_digest,
            "source_backup_sha256": observed_source_backup_sha,
            "source_digest": source_digest,
            "restored_source_digest": restored["source_digest"],
            "restore_roundtrip": restored["restore_roundtrip"],
            "target_tables_untouched": True,
            "pre_delete": True,
            "runtime_execution": True,
            "runtime_action": ROLLBACK_RUNTIME_ACTION,
            "operator_ack": REQUIRED_ROLLBACK_ACK,
            "runtime_state_sha256": restored_state_sha,
            "runtime_state_before_sha256": previous_state_sha,
            "runtime_command": "rollback-runtime",
            "workflow_export_sha256": export["export_sha256"] if export else None,
            "lock_receipt_sha256": lock_sha,
            **binding,
        }
    )
    # _seal uses the cutover key; this runtime proof has its own schema/key.
    result["runtime_proof_sha256"] = result.pop("cutover_receipt_sha256")
    _assert_currentness(
        args,
        source_head=source_head,
        generator_head=generator_head,
        receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        export_sha=export["export_sha256"] if export else None,
    )
    _write_json(args.output, result)
    return result


def validate_inputs(args: argparse.Namespace) -> dict[str, Any]:
    expected_ack, expected_action = (
        (REQUIRED_ROLLBACK_ACK, ROLLBACK_RUNTIME_ACTION)
        if args.operation_kind == "ROLLBACK"
        else (REQUIRED_FORWARD_ACK, FORWARD_RUNTIME_ACTION)
    )
    source_head, generator_head, receipt_sha, source_backup_sha, identity_digest = _heads(
        args, expected_ack, expected_action
    )
    source, migration_receipt, observed_receipt_sha, observed_source_backup_sha = _source_and_receipt(
        args.source_backup, args.migration_receipt, receipt_sha, source_backup_sha
    )
    module = _load_migration_module()
    if module.MigrationRunner(_source_rows(source)).run() != migration_receipt:
        raise CutoverError("MIGRATION_RECEIPT_CONTENT_MISMATCH")
    export, _lock_receipt, lock_sha, binding = _bound_live_inputs(
        args,
        source_head=source_head,
        generator_head=generator_head,
        receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        operation=args.operation_kind,
    )
    if export is not None:
        _assert_currentness(
            args,
            source_head=source_head,
            generator_head=generator_head,
            receipt_sha=receipt_sha,
            source_backup_sha=source_backup_sha,
            identity_digest=identity_digest,
            export_sha=export["export_sha256"],
        )
    return {
        "schema_version": "finance-four-table-cutover-inputs-v1",
        "source_head": source_head,
        "generator_head": generator_head,
        "accepted_identity_sha256": identity_digest,
        "migration_receipt_sha256": observed_receipt_sha,
        **binding,
        "source_backup_sha256": observed_source_backup_sha,
        "source_digest": migration_receipt["source_digest"],
        "inputs_verified": True,
        "workflow_export_sha256": export["export_sha256"] if export else None,
        "lock_receipt_sha256": lock_sha,
    }


def validate_preconditions(args: argparse.Namespace) -> dict[str, Any]:
    expected_ack, expected_action = (
        (REQUIRED_ROLLBACK_ACK, ROLLBACK_RUNTIME_ACTION)
        if args.operation_kind == "ROLLBACK"
        else (REQUIRED_FORWARD_ACK, FORWARD_RUNTIME_ACTION)
    )
    source_head, generator_head, receipt_sha, source_backup_sha, identity_digest = _heads(
        args, expected_ack, expected_action
    )
    if args.live_export is None:
        raise CutoverError("PROTECTED_LIVE_EXPORT_REQUIRED")
    export = _validate_live_export(
        args.live_export,
        source_head=source_head,
        generator_head=generator_head,
        migration_receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        matrix=_load_matrix(),
    )
    binding = _binding_inputs(args, export["export_sha256"])
    lock = _lock_receipt(
        export=export,
        migration_receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        project_id=export["project_id"],
        operation="PRECONDITION",
        binding=binding,
    )
    _write_json(args.output, lock)
    result = {
        "schema_version": PRECONDITION_SCHEMA,
        "operation": args.operation_kind,
        "source_head": source_head,
        "generator_head": generator_head,
        "migration_receipt_sha256": receipt_sha,
        **binding,
        "source_backup_sha256": source_backup_sha,
        "accepted_identity_sha256": identity_digest,
        "project_id": export["project_id"],
        "workflow_export_sha256": export["export_sha256"],
        "reference_count": export["reference_count"],
        "unresolved": export["unresolved"],
        "replay_noop": True,
        "lock_receipt_sha256": lock["lock_receipt_sha256"],
    }
    _assert_currentness(
        args,
        source_head=source_head,
        generator_head=generator_head,
        receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        export_sha=export["export_sha256"],
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for operation in ("forward", "rollback", "rollback-runtime", "validate-inputs", "preflight"):
        command = subparsers.add_parser(operation)
        command.add_argument("--source-backup", type=Path, required=True)
        command.add_argument("--migration-receipt", type=Path, required=True)
        command.add_argument("--migration-receipt-sha256", required=True)
        command.add_argument("--source-backup-sha256", required=True)
        command.add_argument("--operation-nonce")
        command.add_argument("--protected-quiescence-receipt-digest")
        command.add_argument("--required-live-export-digest")
        command.add_argument("--contract-bijection-digest")
        command.add_argument("--repository-root", type=Path, required=True)
        command.add_argument("--accepted-identity", type=Path)
        command.add_argument("--operator-ack", required=True)
        command.add_argument("--runtime-action", required=True)
        command.add_argument("--workflow-root", type=Path, required=True)
        command.add_argument("--output", type=Path, required=operation != "validate-inputs")
        command.add_argument("--live-export", type=Path)
        command.add_argument("--lock-receipt", type=Path)
        command.add_argument("--lock-path", type=Path)
        if operation in {"validate-inputs", "preflight"}:
            command.add_argument("--operation-kind", choices=("FORWARD", "ROLLBACK"), default="FORWARD")
        if operation in {"forward", "rollback"}:
            command.add_argument("--pre-readback-raw", type=Path, required=True)
            command.add_argument("--post-readback-raw", type=Path, required=True)
        if operation == "forward":
            command.add_argument("--second-post-readback-raw", type=Path, required=True)
        if operation == "rollback":
            command.add_argument("--forward-receipt", type=Path, required=True)
            command.add_argument("--runtime-proof", type=Path, required=True)
        if operation in {"forward", "rollback", "rollback-runtime"}:
            command.add_argument("--runtime-state", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        lock_path = getattr(args, "lock_path", None)
        with (_exclusive_writer_lock(lock_path) if lock_path is not None else contextlib.nullcontext()):
            if args.operation == "forward":
                result = run_forward(args)
            elif args.operation == "rollback":
                result = run_rollback(args)
            elif args.operation == "validate-inputs":
                result = validate_inputs(args)
            elif args.operation == "preflight":
                result = validate_preconditions(args)
            else:
                result = run_rollback_runtime(args)
    except (CutoverError, OSError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
