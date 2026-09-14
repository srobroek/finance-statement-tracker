#!/usr/bin/env python3
"""Run receipt-bound four-table migration proofs.

The runner composes ``generate_data_table_migration.py`` and the redacted
readback parser. Runtime actions are deliberately limited to the disposable
bootstrap workflow and its persisted pre-delete reverse transition.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import re
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
N8N = ROOT / "integrations" / "n8n"
MIGRATION_PATH = N8N / "generate_data_table_migration.py"
MATRIX_PATH = N8N / "data-table-migration-matrix.json"
LEGACY_REFERENCE_INVENTORY_PATH = Path(__file__).with_name(
    "finance-four-table-legacy-reference-inventory-v1.json"
)
DATA_TABLES_PATH = N8N / "data-tables.json"
READBACK_PARSER_PATH = Path(__file__).with_name("parse_n8n_redacted_wrapper_output.py")
WORKFLOW_ROOT = N8N / "workflows"
CREDENTIAL_BINDINGS_PATH = N8N / "credential-bindings.json"
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
APPROVED_QUIESCENCE_RECEIPT_DIGEST = (
    "74b77a7f4c1c870815bbde8cf4563b20984d76785d076a050fcef8880a7a4b69"
)
APPROVED_PROTECTED_EXPORT_SEMANTIC_DIGEST = (
    "9b49963355aa4d025e414eb1fd02abcb2891b340afa96f8d2ed4f00102301154"
)
APPROVED_CONTRACT_BIJECTION_DIGEST = (
    "b8c25ec57b00e1bd8b511a33fa576d390d3a46c7aa58708237268cb51c29d00a"
)
APPROVED_LEGACY_REFERENCE_INVENTORY_SHA256 = (
    "e414e2ee0e2a31aa9f7aec8bce03498b9f9e1d2c8598a9c193150f339248a6a3"
)
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
PRESERVED_SOURCE_TABLES = frozenset({"finance_source_contracts"})
PRESERVED_LEGACY_AUDIT_TABLES = frozenset(
    {"finance_pipeline_runs", "finance_mcp_requests"}
)


class CutoverError(ValueError):
    """Raised when a cutover proof cannot be bound to its inputs."""


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(_canonical(value), ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


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
    if metadata.st_uid != os.geteuid():
        raise CutoverError(f"{label}_OWNER_REQUIRED:{path.name}")
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise CutoverError(f"{label}_MODE_REQUIRED:{path.name}")


def _require_artifact_output(path: Path, protected: Sequence[Path], label: str) -> None:
    """Keep receipt writes private and never replace a protected input."""
    try:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            metadata = None
        parent = path.parent.lstat()
    except OSError as error:
        raise CutoverError(f"{label}_PATH_UNAVAILABLE:{path}") from error
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_IMODE(parent.st_mode) != 0o700
        or parent.st_uid != os.geteuid()
    ):
        raise CutoverError(f"{label}_PARENT_UNSAFE:{path.parent}")
    if metadata is not None:
        if metadata.st_uid != os.geteuid():
            raise CutoverError(f"{label}_OWNER_REQUIRED:{path.name}")
        if stat.S_ISLNK(metadata.st_mode):
            raise CutoverError(f"{label}_SYMLINK_FORBIDDEN:{path.name}")
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise CutoverError(f"{label}_MODE_REQUIRED:{path.name}")
    try:
        resolved = path.resolve(strict=False)
        protected_paths = [candidate.resolve(strict=False) for candidate in protected]
    except (OSError, RuntimeError) as error:
        raise CutoverError(f"{label}_PATH_INVALID:{path}") from error
    if resolved in protected_paths:
        raise CutoverError(f"{label}_PROTECTED_INPUT_CONFLICT:{path.name}")


def _validate_output_path(args: argparse.Namespace, path: Path, label: str) -> None:
    protected = [
        value
        for value in vars(args).values()
        if isinstance(value, Path) and value != path
    ]
    _require_artifact_output(path, protected, label)


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


def _binding_inputs(args: argparse.Namespace) -> dict[str, str]:
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
        getattr(args, "required_live_export_digest", None)
        or APPROVED_PROTECTED_EXPORT_SEMANTIC_DIGEST,
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


def _load_legacy_reference_inventory() -> dict[str, Any]:
    raw = _protected_bytes(
        LEGACY_REFERENCE_INVENTORY_PATH,
        "LEGACY_REFERENCE_INVENTORY",
        limit=64 * 1024,
        require_private=False,
    ).replace(b"\r\n", b"\n")
    if hashlib.sha256(raw).hexdigest() != APPROVED_LEGACY_REFERENCE_INVENTORY_SHA256:
        raise CutoverError("LEGACY_REFERENCE_INVENTORY_SHA256_MISMATCH")
    try:
        inventory = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (ValueError, UnicodeError) as error:
        raise CutoverError("LEGACY_REFERENCE_INVENTORY_JSON_INVALID") from error
    if (
        not isinstance(inventory, dict)
        or set(inventory)
        != {
            "schema_version",
            "source_matrix",
            "source_contract",
            "source_snapshot",
            "tables",
        }
        or inventory["schema_version"]
        != "finance-four-table-legacy-reference-inventory-v1"
        or not isinstance(inventory["source_snapshot"], dict)
        or not isinstance(inventory["tables"], list)
        or inventory["source_matrix"]
        != {
            "revision": "c235494fe90153be1be6b987b5cd56c264f12546",
            "path": "integrations/n8n/data-table-migration-matrix.json",
            "sha256": "b14bf92cc456f95170fc8fdab4b6789b0bba689eb27ac7bb820da682b997dd49",
        }
    ):
        raise CutoverError("LEGACY_REFERENCE_INVENTORY_SCHEMA_INVALID")
    return inventory


def _validate_legacy_reference_identity(
    identity: Mapping[str, Any], args: argparse.Namespace
) -> None:
    _load_legacy_reference_inventory()
    if "legacy_reference_inventory_sha256" not in identity:
        receipt, _ = _read_forward_runtime_receipt(args)
        if (
            receipt is not None
            and receipt["schema_version"] == "finance-four-table-runtime-plan-v1"
        ):
            return
        raise CutoverError("ACCEPTED_LEGACY_REFERENCE_INVENTORY_PIN_REQUIRED")
    if (
        identity["legacy_reference_inventory_sha256"]
        != APPROVED_LEGACY_REFERENCE_INVENTORY_SHA256
    ):
        raise CutoverError("ACCEPTED_LEGACY_REFERENCE_INVENTORY_PIN_MISMATCH")


def _reference_inventory() -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for table in _load_legacy_reference_inventory()["tables"]:
        if not isinstance(table, Mapping):
            raise CutoverError("REFERENCE_INVENTORY_INVALID")
        source_table = _require_text(
            table.get("source_table"), "REFERENCE_SOURCE_TABLE"
        )
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
            if action.endswith(
                (
                    "repository_contract",
                    "mcp_audit_contract",
                    "execution_history_receipt",
                )
            ):
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


LIVE_EXPORT_FIELDS = frozenset(
    {
        "schema_version",
        "export_sha256",
        "repository_root",
        "project_id",
        "source_head",
        "generator_head",
        "migration_receipt_sha256",
        "source_backup_sha256",
        "accepted_identity_sha256",
        "redacted",
        "workflow_count",
        "in_flight",
        "workflows",
        "targets",
        "references",
    }
)
WORKFLOW_SEMANTIC_FIELDS = (
    "workflow_id",
    "active",
    "published",
    "in_flight",
    "workflow_body_sha256",
)
TARGET_SEMANTIC_FIELDS = ("name", "table_id", "schema_sha256")
REFERENCE_SEMANTIC_FIELDS = (
    "reference_id",
    "workflow_id",
    "workflow_path",
    "node_id",
    "node_name",
    "operation",
    "old_table_name",
    "old_table_id",
    "canonical_table_name",
    "canonical_table_id",
    "active",
    "published",
    "in_flight",
)
WORKFLOW_BODY_FIELDS = ("name", "nodes", "connections", "settings", "meta", "pinData")


def _credential_binding_leaves() -> dict[tuple[str, str], tuple[str, str]]:
    contract, _ = _read_json(CREDENTIAL_BINDINGS_PATH)
    if set(contract) != {
        "bindings",
        "schema_version",
        "source",
        "workflow_code_metadata_key",
    }:
        raise CutoverError("CREDENTIAL_BINDINGS_SCHEMA_INVALID")
    source = contract.get("source")
    if (
        not isinstance(source, Mapping)
        or set(source) != {"file_count", "path", "sha256"}
        or source.get("path") != "integrations/n8n/workflows"
        or source.get("file_count") != 19
        or HEX_DIGEST.fullmatch(str(source.get("sha256", ""))) is None
        or contract.get("schema_version") != 1
        or contract.get("workflow_code_metadata_key") != "financeWorkflowCode"
        or not isinstance(contract.get("bindings"), list)
    ):
        raise CutoverError("CREDENTIAL_BINDINGS_SCHEMA_INVALID")
    leaves: dict[tuple[str, str], tuple[str, str]] = {}
    placeholders: set[str] = set()
    for binding in contract["bindings"]:
        if not isinstance(binding, Mapping) or set(binding) not in (
            {"credential_type", "node_type", "nodes", "placeholder"},
            {"credential_name", "credential_type", "node_type", "nodes", "placeholder"},
        ):
            raise CutoverError("CREDENTIAL_BINDING_KEYS_INVALID")
        placeholder = binding.get("placeholder")
        credential_type = binding.get("credential_type")
        node_type = binding.get("node_type")
        credential_name = binding.get("credential_name")
        if (
            not isinstance(placeholder, str)
            or re.fullmatch(r"BIND_[A-Z0-9_]+", placeholder) is None
            or not isinstance(credential_type, str)
            or not credential_type
            or not isinstance(node_type, str)
            or not node_type
            or (
                credential_name is not None
                and (not isinstance(credential_name, str) or not credential_name)
            )
            or not isinstance(binding.get("nodes"), list)
            or not binding["nodes"]
        ):
            raise CutoverError("CREDENTIAL_BINDING_INVALID")
        if placeholder in placeholders:
            raise CutoverError("CREDENTIAL_BINDING_AMBIGUOUS")
        placeholders.add(placeholder)
    for binding in contract["bindings"]:
        for item in binding["nodes"]:
            if not isinstance(item, Mapping) or set(item) != {"node", "workflow"}:
                raise CutoverError("CREDENTIAL_BINDING_AMBIGUOUS")
            workflow = item.get("workflow")
            node = item.get("node")
            if (
                not isinstance(workflow, Mapping)
                or set(workflow) != {"code", "file", "id"}
                or not isinstance(node, Mapping)
                or set(node) != {"id", "name"}
                or not all(
                    isinstance(workflow.get(field), str) and workflow[field]
                    for field in ("code", "file", "id")
                )
                or re.fullmatch(r"\S+\.json", workflow["file"]) is None
                or not all(
                    isinstance(node.get(field), str) and node[field]
                    for field in ("id", "name")
                )
            ):
                raise CutoverError("CREDENTIAL_BINDING_AMBIGUOUS")
            key = (workflow["id"], node["id"])
            if key in leaves:
                raise CutoverError("CREDENTIAL_BINDING_AMBIGUOUS")
            leaves[key] = (binding["placeholder"], binding["credential_type"])
    if len(contract["bindings"]) != 9 or len(leaves) != 40:
        raise CutoverError("CREDENTIAL_BINDING_COVERAGE_INVALID")
    return leaves


def _export_without_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result.pop("export_sha256", None)
    return result


def _workflow_body_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        body = _canonical({field: value[field] for field in WORKFLOW_BODY_FIELDS})
    except (KeyError, TypeError) as error:
        raise CutoverError("WORKFLOW_BODY_INVALID") from error
    leaves = _credential_binding_leaves()
    workflow_id = str(value.get("id", value.get("workflow_id", "")))
    for node in body.get("nodes", []):
        binding = leaves.get((workflow_id, str(node.get("id", ""))))
        if not binding or not isinstance(node.get("credentials"), Mapping):
            continue
        placeholder, credential_type = binding
        credentials = node["credentials"]
        if set(credentials) != {credential_type}:
            raise CutoverError("CREDENTIAL_REFERENCE_INVALID")
        reference = credentials[credential_type]
        if (
            not isinstance(reference, dict)
            or set(reference) != {"id", "name"}
            or not isinstance(reference.get("id"), str)
            or not reference["id"]
            or not isinstance(reference.get("name"), str)
            or not reference["name"]
        ):
            raise CutoverError("CREDENTIAL_REFERENCE_INVALID")
        reference["id"] = placeholder
        reference["name"] = placeholder
    expected = {
        node_id for (bound_workflow, node_id) in leaves if bound_workflow == workflow_id
    }
    observed = {
        str(node.get("id", ""))
        for node in body.get("nodes", [])
        if (workflow_id, str(node.get("id", ""))) in leaves
    }
    if expected != observed:
        raise CutoverError("CREDENTIAL_BINDING_COVERAGE_INVALID")
    return body


def _workflow_body_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _canonical_bytes(_workflow_body_projection(value))
    ).hexdigest()


def _export_semantic_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != LIVE_EXPORT_FIELDS:
        raise CutoverError("LIVE_EXPORT_FIELDS_INVALID")
    workflows = value.get("workflows")
    targets = value.get("targets")
    references = value.get("references")
    if (
        not isinstance(workflows, list)
        or not isinstance(targets, list)
        or not isinstance(references, list)
    ):
        raise CutoverError("LIVE_EXPORT_SEMANTIC_COLLECTIONS_INVALID")
    try:
        projected_workflows = sorted(
            [
                {
                    **{
                        field: workflow[field]
                        for field in WORKFLOW_SEMANTIC_FIELDS[:-1]
                    },
                    "workflow_body_sha256": _require_digest(
                        workflow["workflow_body_sha256"], "WORKFLOW_BODY_SHA256"
                    ),
                }
                for workflow in workflows
            ],
            key=lambda workflow: workflow["workflow_id"],
        )
        projected_targets = sorted(
            [
                {
                    "name": target["name"],
                    "table_id": target["table_id"],
                    "schema_sha256": _require_digest(
                        target["schema_sha256"], "TARGET_SCHEMA_SHA256"
                    ),
                }
                for target in targets
            ],
            key=lambda target: target["name"],
        )
        projected_references = sorted(
            [
                {field: reference[field] for field in REFERENCE_SEMANTIC_FIELDS}
                for reference in references
            ],
            key=lambda reference: reference["reference_id"],
        )
    except (KeyError, TypeError) as error:
        raise CutoverError("LIVE_EXPORT_SEMANTIC_RECORD_INVALID") from error
    return {
        "schema_version": value["schema_version"],
        "workflow_count": value["workflow_count"],
        "in_flight": value["in_flight"],
        "workflows": projected_workflows,
        "targets": projected_targets,
        "references": projected_references,
    }


def _export_semantic_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _canonical_bytes(_export_semantic_projection(value))
    ).hexdigest()


def _validate_live_export(
    path: Path,
    *,
    source_head: str,
    generator_head: str,
    migration_receipt_sha: str,
    source_backup_sha: str,
    identity_digest: str,
    required_export_digest: str,
    matrix: Mapping[str, Any],
    project_id: str | None = None,
    validate_target_schema: bool = True,
) -> dict[str, Any]:
    _require_protected(path, "PROTECTED_LIVE_EXPORT")
    export, raw = _read_json(path)
    if set(export) != LIVE_EXPORT_FIELDS:
        raise CutoverError("LIVE_EXPORT_FIELDS_INVALID")
    if export.get("schema_version") != LIVE_EXPORT_SCHEMA:
        raise CutoverError("LIVE_EXPORT_SCHEMA_INVALID")
    export_sha = export.get("export_sha256")
    export_sha = _require_digest(export_sha, "LIVE_EXPORT_SHA256")
    if (
        hashlib.sha256(_canonical_bytes(_export_without_hash(export))).hexdigest()
        != export_sha
    ):
        raise CutoverError("LIVE_EXPORT_INTEGRITY_MISMATCH")
    semantic_digest = _export_semantic_digest(export)
    if semantic_digest != required_export_digest:
        raise CutoverError("LIVE_EXPORT_REQUIRED_DIGEST_MISMATCH")
    if export.get("repository_root") != str(ROOT):
        raise CutoverError("LIVE_EXPORT_REPOSITORY_ROOT_MISMATCH")
    export_project_id = _require_text(
        export.get("project_id"), "LIVE_EXPORT_PROJECT_ID"
    )
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", export_project_id):
        raise CutoverError("LIVE_EXPORT_PROJECT_ID_INVALID")
    if project_id is not None and export_project_id != project_id:
        raise CutoverError("LIVE_EXPORT_PROJECT_ID_MISMATCH")
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
    if (
        export.get("workflow_count") != 19
        or not isinstance(workflows, list)
        or len(workflows) != 19
    ):
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
        _require_digest(workflow.get("workflow_body_sha256"), "WORKFLOW_BODY_SHA256")
        revision_id = _require_text(workflow.get("revision_id"), "WORKFLOW_REVISION_ID")
        if workflow_id in workflow_revisions:
            raise CutoverError("DUPLICATE_WORKFLOW_ID")
        workflow_revisions[workflow_id] = revision_id
    target_ids = export.get("targets")
    if (
        not isinstance(target_ids, list)
        or not all(isinstance(item, Mapping) for item in target_ids)
        or len(target_ids) != len(TARGETS)
        or {item.get("name") for item in target_ids} != set(TARGETS)
    ):
        raise CutoverError("EXACT_TARGET_EXPORT_REQUIRED")
    expected_schemas = (
        _target_schema_digests(matrix) if validate_target_schema else None
    )
    for target in target_ids:
        _require_text(target.get("table_id"), "LIVE_TARGET_ID")
        _require_digest(target.get("schema_sha256"), "LIVE_TARGET_SCHEMA_SHA256")
        if (
            expected_schemas is not None
            and target.get("schema_sha256") != expected_schemas[target["name"]]
        ):
            raise CutoverError(f"LIVE_TARGET_SCHEMA_DIGEST_MISMATCH:{target['name']}")
    inventory = _reference_inventory()
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
                raise CutoverError(
                    f"LIVE_REFERENCE_{field.upper()}_MISMATCH:{expected['reference_id']}"
                )
        if "source_table" in observed and observed.get("source_table") != observed.get(
            "old_table_name"
        ):
            raise CutoverError(
                f"LIVE_REFERENCE_SOURCE_TABLE_MISMATCH:{expected['reference_id']}"
            )
        _require_text(observed.get("workflow_id"), "LIVE_REFERENCE_WORKFLOW_ID")
        observed_workflow_id = observed["workflow_id"]
        observed_revision_id = _require_text(
            observed.get("revision_id"), "LIVE_REFERENCE_REVISION_ID"
        )
        if observed_workflow_id not in workflow_revisions:
            raise CutoverError(
                f"LIVE_REFERENCE_WORKFLOW_UNKNOWN:{expected['reference_id']}"
            )
        if observed_revision_id != workflow_revisions[observed_workflow_id]:
            raise CutoverError(
                f"LIVE_REFERENCE_REVISION_MISMATCH:{expected['reference_id']}"
            )
        _require_text(observed.get("node_id"), "LIVE_REFERENCE_NODE_ID")
        _require_text(observed.get("old_table_id"), "LIVE_REFERENCE_OLD_TABLE_ID")
        if observed["old_table_id"] != expected["legacy_table_id"]:
            raise CutoverError(
                f"LIVE_REFERENCE_OLD_TABLE_ID_CONFLICT:{expected['reference_id']}"
            )
        node_key = (observed_workflow_id, observed["node_id"])
        if node_key in node_aliases:
            raise CutoverError(
                f"LIVE_REFERENCE_NODE_ALIAS_CONFLICT:{expected['reference_id']}"
            )
        node_aliases.add(node_key)
        if (
            observed.get("active") is not False
            or observed.get("published") is not False
            or observed.get("in_flight") != 0
        ):
            raise CutoverError("LIVE_REFERENCE_NOT_QUIESCENT")
        target_name = expected["canonical_table_name"]
        if target_name is None:
            if observed.get("canonical_table_id") not in {None, ""}:
                raise CutoverError(
                    f"UNDECLARED_REFERENCE_TARGET:{expected['reference_id']}"
                )
        else:
            if (
                observed.get("canonical_table_id")
                != target_by_name[target_name]["table_id"]
            ):
                raise CutoverError(
                    f"LIVE_REFERENCE_TARGET_ID_MISMATCH:{expected['reference_id']}"
                )
        actions.append(
            {
                **expected,
                "workflow_id": observed["workflow_id"],
                "revision_id": observed["revision_id"],
                "node_id": observed["node_id"],
                "old_table_id": observed["old_table_id"],
                "canonical_table_id": observed.get("canonical_table_id"),
            }
        )
    return {
        "path": str(path),
        "project_id": export_project_id,
        "export_sha256": export_sha,
        "semantic_digest": semantic_digest,
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
    if (
        receipt.get("schema_version") != LOCK_RECEIPT_SCHEMA
        or receipt.get("lock_name") != LOCK_NAME
    ):
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
    integrity = _require_digest(
        receipt.get("lock_receipt_sha256"), "LOCK_RECEIPT_SHA256"
    )
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
    binding = dict(
        binding
        or {
            "operation_nonce": DEFAULT_OPERATION_NONCE,
            "protected_quiescence_receipt_digest": APPROVED_QUIESCENCE_RECEIPT_DIGEST,
            "required_live_export_digest": APPROVED_PROTECTED_EXPORT_SEMANTIC_DIGEST,
            "contract_bijection_digest": APPROVED_CONTRACT_BIJECTION_DIGEST,
        }
    )
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
    spec = importlib.util.spec_from_file_location(
        "finance_four_table_migration", MIGRATION_PATH
    )
    if spec is None or spec.loader is None:
        raise CutoverError("MIGRATION_GENERATOR_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_matrix() -> dict[str, Any]:
    matrix, _ = _read_json(MATRIX_PATH)
    target_schemas = matrix.get("target_schemas")
    if not isinstance(target_schemas, dict) or not set(TARGETS).issubset(
        target_schemas
    ):
        raise CutoverError("EXACT_TARGET_SCHEMA_SET_REQUIRED")
    return matrix


def _legacy_names() -> set[str]:
    tables, _ = _read_json(DATA_TABLES_PATH)
    values = tables.get("tables")
    if not isinstance(values, list):
        raise CutoverError("SOURCE_TABLE_CONTRACT_INVALID")
    names: set[str] = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str):
            raise CutoverError("SOURCE_TABLE_CONTRACT_INVALID")
        names.add(name)
    if not names:
        raise CutoverError("SOURCE_TABLE_CONTRACT_INVALID")
    return names - set(TARGETS)


def _protected_bytes(
    path: Path, label: str, *, limit: int, require_private: bool = True
) -> bytes:
    if require_private:
        _require_protected(path, f"PROTECTED_{label}")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or (
                require_private and stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise CutoverError(f"PROTECTED_{label}_MODE_REQUIRED")
            raw = handle.read(limit + 1)
    except OSError as error:
        raise CutoverError(f"{label}_READ_FAILED") from error
    if len(raw) > limit:
        raise CutoverError(f"{label}_SIZE_EXCEEDED")
    return raw


def _read_forward_runtime_receipt(
    args: argparse.Namespace,
) -> tuple[dict[str, Any] | None, str | None]:
    path = getattr(args, "forward_runtime_receipt", None)
    if path is None:
        return None, None
    operation_name = str(getattr(args, "operation", "")).upper()
    operation_kind = getattr(args, "operation_kind", None) or (
        "ROLLBACK"
        if operation_name in {"ROLLBACK", "ROLLBACK-RUNTIME"}
        else operation_name
    )
    if operation_kind not in {"FORWARD", "ROLLBACK"}:
        raise CutoverError("FORWARD_RUNTIME_RECEIPT_OPERATION_INVALID")
    raw = _protected_bytes(path, "FORWARD_RUNTIME_RECEIPT", limit=4 * 1024 * 1024)
    try:
        receipt = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (ValueError, UnicodeError) as error:
        raise CutoverError("FORWARD_RUNTIME_RECEIPT_JSON_INVALID") from error
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema_version")
        not in {
            "finance-four-table-runtime-plan-v1",
            "finance-four-table-runtime-plan-v2",
            "finance-four-table-runtime-plan-v3",
        }
        or receipt.get("operation") != "FORWARD"
        or receipt.get("durable_journal") is not True
        or receipt.get("commit_protocol") != "postgresql_synchronous_wal"
        or receipt.get("readback_verified") is not True
        or receipt.get("action_count") != len(EXPECTED_REFERENCE_ACTIONS)
        or not isinstance(receipt.get("actions"), list)
        or len(receipt["actions"]) != len(EXPECTED_REFERENCE_ACTIONS)
    ):
        raise CutoverError("FORWARD_RUNTIME_RECEIPT_SCHEMA_INVALID")
    unsigned = dict(receipt)
    digest = _require_digest(
        unsigned.pop("runtime_plan_receipt_sha256", None),
        "FORWARD_RUNTIME_RECEIPT_SHA256",
    )
    if hashlib.sha256(_canonical_bytes(unsigned)).hexdigest() != digest:
        raise CutoverError("FORWARD_RUNTIME_RECEIPT_INTEGRITY_INVALID")
    if receipt["schema_version"] in {
        "finance-four-table-runtime-plan-v2",
        "finance-four-table-runtime-plan-v3",
    }:
        _require_digest(
            receipt.get("canonical_source_sha256"), "CANONICAL_SOURCE_SHA256"
        )
        _require_digest(
            receipt.get("rollback_workflows_sha256"), "ROLLBACK_WORKFLOWS_SHA256"
        )
    elif "canonical_source_sha256" in receipt or "rollback_workflows_sha256" in receipt:
        raise CutoverError("FORWARD_RUNTIME_RECEIPT_VERSION_MISMATCH")
    if receipt["schema_version"] == "finance-four-table-runtime-plan-v3":
        for field in (
            "target_digest",
            "target_projection_sha256",
            "target_readback_sha256",
            "rollback_targets_sha256",
        ):
            _require_digest(receipt.get(field), field.upper())
        target_prestate = receipt.get("target_prestate")
        if (
            receipt.get("preserved_table_writes") is not False
            or not isinstance(target_prestate, list)
            or len(target_prestate) != len(TARGETS)
        ):
            raise CutoverError("FORWARD_RUNTIME_TARGET_RECEIPT_INVALID")
    return receipt, hashlib.sha256(raw).hexdigest()


def _read_rollback_runtime_receipt(
    args: argparse.Namespace,
) -> tuple[dict[str, Any] | None, str | None]:
    path = getattr(args, "rollback_runtime_receipt", None)
    if path is None:
        return None, None
    raw = _protected_bytes(path, "ROLLBACK_RUNTIME_RECEIPT", limit=4 * 1024 * 1024)
    try:
        receipt = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (ValueError, UnicodeError) as error:
        raise CutoverError("ROLLBACK_RUNTIME_RECEIPT_JSON_INVALID") from error
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema_version") != "finance-four-table-runtime-plan-v3"
        or receipt.get("operation") != "ROLLBACK"
        or receipt.get("durable_journal") is not True
        or receipt.get("commit_protocol") != "postgresql_synchronous_wal"
        or receipt.get("readback_verified") is not True
        or receipt.get("action_count") != len(EXPECTED_REFERENCE_ACTIONS)
        or not isinstance(receipt.get("actions"), list)
        or len(receipt["actions"]) != len(EXPECTED_REFERENCE_ACTIONS)
        or receipt.get("target_rows_restored") is not True
        or receipt.get("preserved_table_writes") is not False
    ):
        raise CutoverError("ROLLBACK_RUNTIME_RECEIPT_SCHEMA_INVALID")
    for field in (
        "canonical_source_sha256",
        "target_digest",
        "target_projection_sha256",
        "target_readback_sha256",
        "rollback_targets_sha256",
        "forward_runtime_receipt_sha256",
        "credential_state_digest_after",
        "workflow_credential_objects_digest_after",
        "workflow_revision_digest_after",
    ):
        _require_digest(receipt.get(field), field.upper())
    unsigned = dict(receipt)
    digest = _require_digest(
        unsigned.pop("runtime_plan_receipt_sha256", None),
        "ROLLBACK_RUNTIME_RECEIPT_SHA256",
    )
    if hashlib.sha256(_canonical_bytes(unsigned)).hexdigest() != digest:
        raise CutoverError("ROLLBACK_RUNTIME_RECEIPT_INTEGRITY_INVALID")
    return receipt, hashlib.sha256(raw).hexdigest()


def _validate_rollback_runtime_binding(
    receipt: Mapping[str, Any],
    *,
    export: Mapping[str, Any],
    binding: Mapping[str, str],
    forward_receipt: Mapping[str, Any],
    forward_receipt_sha256: str,
) -> None:
    if (
        receipt.get("project_id") != export["project_id"]
        or receipt.get("export_sha256") != export["export_sha256"]
        or receipt.get("lock_resource")
        != f"{LOCK_RESOURCE_PREFIX}:{export['project_id']}"
        or receipt.get("forward_runtime_receipt_sha256") != forward_receipt_sha256
        or receipt.get("canonical_source_sha256")
        != forward_receipt.get("canonical_source_sha256")
        or receipt.get("target_digest") != forward_receipt.get("target_digest")
        or receipt.get("target_projection_sha256")
        != forward_receipt.get("target_projection_sha256")
        or receipt.get("rollback_targets_sha256")
        != forward_receipt.get("rollback_targets_sha256")
        or receipt.get("actions") != forward_receipt.get("actions")
    ):
        raise CutoverError("ROLLBACK_RUNTIME_RECEIPT_BINDING_INVALID")
    _validate_binding(receipt, binding, "ROLLBACK_RUNTIME_RECEIPT")


def _read_forward_cutover_receipt(
    args: argparse.Namespace,
    *,
    receipt_sha: str,
    source_head: str,
    generator_head: str,
    source_backup_sha256: str,
    binding: Mapping[str, str],
    runtime_receipt: Mapping[str, Any],
    runtime_receipt_sha256: str,
) -> dict[str, Any]:
    path = getattr(args, "forward_receipt", None)
    if path is None:
        raise CutoverError("FORWARD_RECEIPT_REQUIRED")
    _require_protected(path)
    receipt, _ = _read_json(path)
    if (
        receipt.get("schema_version") != "finance-four-table-cutover-receipt-v1"
        or receipt.get("operation") != "FORWARD"
        or receipt.get("migration_receipt_sha256") != receipt_sha
        or receipt.get("source_head") != source_head
        or receipt.get("generator_head") != generator_head
        or receipt.get("source_backup_sha256") != source_backup_sha256
        or receipt.get("old_tables_preserved") is not True
        or receipt.get("runtime_cutover") is not False
        or receipt.get("deletion_authorized") is not False
        or receipt.get("workflow_export_sha256") != runtime_receipt.get("export_sha256")
        or receipt.get("forward_runtime_receipt_sha256") != runtime_receipt_sha256
    ):
        raise CutoverError("FORWARD_RECEIPT_BINDING_MISMATCH")
    _validate_binding(receipt, binding, "FORWARD_RECEIPT")
    integrity = _require_digest(
        receipt.get("cutover_receipt_sha256"), "CUTOVER_RECEIPT_SHA256"
    )
    unsigned = dict(receipt)
    unsigned.pop("cutover_receipt_sha256", None)
    if hashlib.sha256(_canonical_bytes(unsigned)).hexdigest() != integrity:
        raise CutoverError("FORWARD_RECEIPT_INTEGRITY_MISMATCH")
    return receipt


def _validate_forward_runtime_binding(
    receipt: Mapping[str, Any] | None,
    export: Mapping[str, Any],
    binding: Mapping[str, str],
) -> None:
    if receipt is None:
        return
    replay_binding = receipt.get("schema_version") in {
        "finance-four-table-runtime-plan-v2",
        "finance-four-table-runtime-plan-v3",
    } and isinstance(receipt.get("canonical_source_sha256"), str)
    if (
        receipt.get("project_id") != export["project_id"]
        or (
            receipt.get("export_sha256") != export["export_sha256"]
            and not replay_binding
        )
        or receipt.get("lock_resource")
        != f"{LOCK_RESOURCE_PREFIX}:{export['project_id']}"
    ):
        raise CutoverError("FORWARD_RUNTIME_RECEIPT_BINDING_INVALID")
    _validate_binding(receipt, binding, "FORWARD_RUNTIME_RECEIPT")
    expected = {action["reference_id"]: action for action in export["actions"]}
    seen = set()
    for action in receipt["actions"]:
        if not isinstance(action, Mapping):
            raise CutoverError("FORWARD_RUNTIME_ACTION_MISMATCH")
        identifier = action.get("reference_id")
        if (
            not isinstance(identifier, str)
            or identifier in seen
            or identifier not in expected
        ):
            raise CutoverError("FORWARD_RUNTIME_ACTION_MISMATCH")
        seen.add(identifier)
        compared_fields = (
            "workflow_id",
            "node_id",
            "canonical_table_id",
        )
        if (
            not replay_binding
            or receipt.get("export_sha256") == export["export_sha256"]
        ):
            compared_fields = (*compared_fields, "revision_id")
        if (
            any(
                action.get(field) != expected[identifier].get(field)
                for field in compared_fields
            )
            or not isinstance(action.get("post_revision_id"), str)
            or not action["post_revision_id"]
        ):
            raise CutoverError("FORWARD_RUNTIME_ACTION_MISMATCH")


def _protected_resolver_input(
    args: argparse.Namespace, name: str, identity: Mapping[str, Any], *, limit: int
) -> dict[str, Any] | None:
    path = getattr(args, name, None)
    expected = getattr(args, f"{name}_sha256", None)
    pins = identity.get("resolver_inputs", {})
    if not isinstance(pins, Mapping) or set(pins) - {
        "alias_bundle_sha256",
        "verification_artifacts_sha256",
    }:
        raise CutoverError("RESOLVER_INPUT_PINS_INVALID")
    pinned = pins.get(f"{name}_sha256")
    label = name.upper()
    if path is None and expected is None and pinned is None:
        return None
    if path is None or expected is None or pinned is None:
        raise CutoverError(f"{label}_PINNED_INPUT_REQUIRED")
    expected = _require_digest(expected, f"{label}_SHA256")
    if expected != pinned:
        raise CutoverError(f"{label}_ACCEPTED_DIGEST_MISMATCH")
    raw = _protected_bytes(path, label, limit=limit)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise CutoverError(f"{label}_SHA256_MISMATCH")
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (ValueError, UnicodeError) as error:
        raise CutoverError(f"{label}_JSON_INVALID") from error
    if not isinstance(value, dict):
        raise CutoverError(f"{label}_OBJECT_REQUIRED")
    return value


def _migration_runner(
    args: argparse.Namespace,
    module: Any,
    source: Mapping[str, Any],
    source_head: str,
    source_backup_sha: str,
) -> Any:
    identity_path = args.accepted_identity or args.migration_receipt.with_name(
        "finance-four-table-accepted-identity.json"
    )
    _require_protected(identity_path)
    identity, _ = _read_json(identity_path)
    aliases = _protected_resolver_input(
        args, "alias_bundle", identity, limit=16 * 1024 * 1024
    )
    artifacts = _protected_resolver_input(
        args, "verification_artifacts", identity, limit=64 * 1024 * 1024
    )
    try:
        alias_resolver = (
            module.AliasResolver(aliases, expected_source_commit=source_head)
            if aliases is not None
            else None
        )
        verification_resolver = None
        if artifacts is not None:
            if (
                set(artifacts)
                != {
                    "schema_version",
                    "source_commit",
                    "source_backup_sha256",
                    "artifacts",
                }
                or artifacts.get("schema_version")
                != "finance-verification-artifacts-v1"
                or artifacts.get("source_commit") != source_head
                or artifacts.get("source_backup_sha256") != source_backup_sha
                or not isinstance(artifacts.get("artifacts"), list)
                or len(artifacts["artifacts"]) > 4096
            ):
                raise CutoverError("VERIFICATION_ARTIFACTS_SOURCE_BINDING_MISMATCH")
            verification_resolver = module.VerificationResolver()
            for artifact in artifacts["artifacts"]:
                if (
                    not isinstance(artifact, Mapping)
                    or set(artifact) != {"pointer", "content_base64"}
                    or not isinstance(artifact["pointer"], Mapping)
                    or set(artifact["pointer"]) != module.VERIFICATION_POINTER_FIELDS
                    or not isinstance(artifact["content_base64"], str)
                    or len(artifact["content_base64"])
                    > 4 * ((8 * 1024 * 1024 + 2) // 3)
                ):
                    raise CutoverError("VERIFICATION_ARTIFACT_ENTRY_INVALID")
                try:
                    content = base64.b64decode(
                        artifact["content_base64"], validate=True
                    )
                except (ValueError, binascii.Error) as error:
                    raise CutoverError(
                        "VERIFICATION_ARTIFACT_CONTENT_INVALID"
                    ) from error
                if len(content) > 8 * 1024 * 1024:
                    raise CutoverError("VERIFICATION_ARTIFACT_SIZE_EXCEEDED")
                verification_resolver.import_artifact(artifact["pointer"], content)
        rows = _source_rows(source)
        for row in rows.get("finance_actual_verifications", []):
            if verification_resolver is None:
                raise CutoverError("ACTUAL_VERIFICATION_RESOLVER_REQUIRED")
            verification_resolver.readback(
                row, expected_sha256=row.get("verification_artifact_sha256")
            )
        return module.MigrationRunner(
            rows,
            alias_resolver=alias_resolver,
            verification_resolver=verification_resolver,
        )
    except module.MigrationError as error:
        raise CutoverError(str(error)) from error


def _matrix_field_transforms(
    matrix: Mapping[str, Any],
) -> dict[tuple[str, str], dict[str, str | None]]:
    transforms: dict[tuple[str, str], dict[str, str | None]] = {}
    schemas = matrix.get("target_schemas")
    if not isinstance(schemas, Mapping):
        raise CutoverError("TARGET_SCHEMAS_INVALID")
    for target in TARGETS:
        schema = schemas.get(target)
        if not isinstance(schema, Mapping):
            raise CutoverError(f"TARGET_SCHEMA_INVALID:{target}")
        columns = schema.get("columns")
        if not isinstance(columns, Mapping):
            raise CutoverError(f"TARGET_SCHEMA_INVALID:{target}")
        identity_derivations = schema.get("identity_derivations", [])
        if not isinstance(identity_derivations, list):
            raise CutoverError(f"TARGET_SCHEMA_INVALID:{target}")
        derived_fields = {
            (derivation.get("source_table"), target_field)
            for derivation in identity_derivations
            if isinstance(derivation, Mapping)
            and derivation.get("strategy") != "direct"
            for target_field in derivation.get("target_key", [])
            if isinstance(target_field, str)
        }
        for target_field, specification in columns.items():
            bindings = (
                specification.get("source_bindings")
                if isinstance(specification, Mapping)
                else None
            )
            if not isinstance(target_field, str) or not isinstance(bindings, list):
                raise CutoverError(f"TARGET_SCHEMA_INVALID:{target}")
            for binding in bindings:
                if not isinstance(binding, str) or "." not in binding:
                    continue
                source, source_field = binding.split(".", 1)
                if (
                    source not in LEGACY_TABLE_IDS
                    or source_field == target_field
                    or (source, target_field) in derived_fields
                ):
                    continue
                fields = transforms.setdefault((source, target), {})
                previous = fields.setdefault(source_field, target_field)
                if previous != target_field:
                    raise CutoverError(
                        f"MIGRATION_FIELD_TRANSFORM_AMBIGUOUS:{source}:{source_field}"
                    )
        table_mappings = matrix.get("tables")
        if not isinstance(table_mappings, list):
            raise CutoverError("SOURCE_TABLE_MAPPINGS_INVALID")
        target_fields = set(columns)
        for table_mapping in table_mappings:
            if (
                not isinstance(table_mapping, Mapping)
                or table_mapping.get("target_table") != target
                or not isinstance(table_mapping.get("source_table"), str)
                or not isinstance(table_mapping.get("columns"), list)
            ):
                continue
            source = table_mapping["source_table"]
            fields = transforms.setdefault((source, target), {})
            for column in table_mapping["columns"]:
                if not isinstance(column, Mapping):
                    raise CutoverError(f"SOURCE_COLUMN_MAPPING_INVALID:{source}")
                source_field = column.get("source_column")
                if (
                    isinstance(source_field, str)
                    and source_field not in target_fields
                    and column.get("target_table") != target
                ):
                    fields[source_field] = None
    return transforms


def _rewrite_mapped_fields(value: Any, fields: Mapping[str, str | None]) -> Any:
    if isinstance(value, dict):
        mapped: dict[str, Any] = {}
        for key, item in value.items():
            mapped_key = fields.get(key, key)
            if mapped_key is None:
                continue
            mapped_item = _rewrite_mapped_fields(item, fields)
            if mapped_key in mapped:
                if mapped[mapped_key] != mapped_item:
                    raise CutoverError(f"MIGRATION_FIELD_COLLISION:{mapped_key}")
                continue
            mapped[mapped_key] = mapped_item
        return mapped
    if isinstance(value, list):
        return [_rewrite_mapped_fields(item, fields) for item in value]
    if not isinstance(value, str):
        return value
    rewritten: str = value
    mapped_value = fields.get(value, value)
    if mapped_value is not None:
        rewritten = mapped_value
    for source_field, target_field in fields.items():
        if target_field is None:
            continue
        rewritten = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(source_field)}(?![A-Za-z0-9_])",
            target_field,
            rewritten,
        )
    return rewritten


def _rewrite_consumer_fields(
    parameters: Mapping[str, Any], fields: Mapping[str, str | None]
) -> dict[str, Any]:
    rewritten = _rewrite_mapped_fields(
        dict(parameters),
        {field: target for field, target in fields.items() if target is not None},
    )
    if not isinstance(rewritten, dict):
        raise CutoverError("CANONICAL_CONSUMER_PARAMETERS_INVALID")
    code = rewritten.get("jsCode")
    if not isinstance(code, str):
        return rewritten
    removed = [field for field, target in fields.items() if target is None]
    lines = code.splitlines(keepends=True)
    for field in removed:
        kept: list[str] = []
        index = 0
        token = f"row.{field}"
        while index < len(lines):
            line = lines[index]
            if token in line and line.lstrip().startswith("if ("):
                if index + 1 >= len(lines) or not lines[index + 1].lstrip().startswith(
                    "throw new Error("
                ):
                    raise CutoverError(f"CANONICAL_CONSUMER_FIELD_UNMAPPED:{field}")
                index += 2
                continue
            kept.append(line)
            index += 1
        lines = kept
        if token in "".join(lines):
            raise CutoverError(f"CANONICAL_CONSUMER_FIELD_UNMAPPED:{field}")
    rewritten["jsCode"] = "".join(lines)
    return rewritten


def _canonical_source_bundle(
    args: argparse.Namespace,
    source_head: str,
    generator_head: str,
    identity_digest: str,
) -> dict[str, Any]:
    inventory = _reference_inventory()
    references_by_path = {}
    for item in inventory:
        references_by_path.setdefault(item["workflow_path"], []).append(item)
    field_transforms = _matrix_field_transforms(_load_matrix())
    paths = sorted(args.workflow_root.glob("*.json"))
    if len(paths) != 19:
        raise CutoverError("EXACT_19_CANONICAL_WORKFLOWS_REQUIRED")
    corpus = hashlib.sha256()
    files = []
    workflow_ids = set()
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise CutoverError("CANONICAL_SOURCE_REGULAR_FILE_REQUIRED")
        raw = path.read_bytes().replace(b"\r\n", b"\n")
        try:
            workflow = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
        except (ValueError, UnicodeError) as error:
            raise CutoverError("CANONICAL_SOURCE_WORKFLOW_INVALID") from error
        if not isinstance(workflow, Mapping):
            raise CutoverError("CANONICAL_SOURCE_WORKFLOW_INVALID")
        workflow = json.loads(
            json.dumps(workflow, ensure_ascii=False, separators=(",", ":")),
            object_pairs_hook=_reject_duplicate_keys,
        )
        workflow_id = _require_text(workflow.get("id"), "CANONICAL_WORKFLOW_ID")
        if workflow_id in workflow_ids or workflow.get("active") is not False:
            raise CutoverError("CANONICAL_WORKFLOW_IDENTITY_INVALID")
        workflow_ids.add(workflow_id)
        relative = f"integrations/n8n/workflows/{path.name}"
        for item in references_by_path.get(relative, []):
            node = next(
                (
                    candidate
                    for candidate in workflow.get("nodes", [])
                    if isinstance(candidate, Mapping)
                    and candidate.get("name") == item["node_name"]
                ),
                None,
            )
            if not isinstance(node, dict):
                raise CutoverError(
                    f"CANONICAL_SOURCE_NODE_INVALID:{item['reference_id']}"
                )
            parameters = node.setdefault("parameters", {})
            if not isinstance(parameters, dict):
                raise CutoverError(
                    f"CANONICAL_SOURCE_PARAMETERS_INVALID:{item['reference_id']}"
                )
            target = item["canonical_table_name"]
            selector = parameters.get("dataTableId")
            selected = (
                selector.get("value")
                if isinstance(selector, Mapping) and selector.get("__rl") is True
                else selector
            )
            if selected != item["source_table"]:
                raise CutoverError(
                    f"CANONICAL_SOURCE_SELECTOR_INVALID:{item['reference_id']}"
                )
            if target is None:
                if (
                    item["source_table"] in PRESERVED_SOURCE_TABLES
                    or item["source_table"] in PRESERVED_LEGACY_AUDIT_TABLES
                ):
                    # Keep the legacy Data Table node executable while its
                    # audit/history contract has no canonical target yet.
                    continue
                node["type"] = "n8n-nodes-base.code"
                parameters.pop("dataTableId", None)
                continue
            fields = field_transforms.get((item["source_table"], target), {})
            if fields:
                parameters = _rewrite_mapped_fields(parameters, fields)
                if not isinstance(parameters, dict):
                    raise CutoverError(
                        f"CANONICAL_SOURCE_PARAMETERS_INVALID:{item['reference_id']}"
                    )
                node["parameters"] = parameters
                selector = parameters.get("dataTableId")
            if (
                item["source_table"] == "finance_reconciliations"
                and target == "finance_actual_batches"
                and parameters.get("operation") == "upsert"
            ):
                idempotency_value = (
                    "={{ $('Prepare Outbox Intent').first().json.idempotency_key }}"
                )
                columns = parameters.get("columns")
                values = columns.get("value") if isinstance(columns, dict) else None
                if not isinstance(values, dict):
                    raise CutoverError("RECONCILIATION_COLUMNS_INVALID")
                values["idempotency_key"] = idempotency_value
                parameters["filters"] = {
                    "conditions": [
                        {
                            "keyName": "idempotency_key",
                            "condition": "eq",
                            "keyValue": idempotency_value,
                        }
                    ]
                }
            if fields and parameters.get("operation") == "get":
                connections = workflow.get("connections", {})
                outputs = (
                    connections.get(node["name"], {})
                    if isinstance(connections, Mapping)
                    else {}
                )
                for ports in outputs.values() if isinstance(outputs, Mapping) else ():
                    if not isinstance(ports, list):
                        continue
                    for edges in ports:
                        if not isinstance(edges, list):
                            continue
                        for edge in edges:
                            consumer_name = (
                                edge.get("node") if isinstance(edge, Mapping) else None
                            )
                            consumer = next(
                                (
                                    candidate
                                    for candidate in workflow.get("nodes", [])
                                    if isinstance(candidate, dict)
                                    and candidate.get("name") == consumer_name
                                ),
                                None,
                            )
                            if not isinstance(consumer, dict):
                                raise CutoverError(
                                    f"CANONICAL_CONSUMER_NODE_INVALID:{item['reference_id']}"
                                )
                            consumer_parameters = consumer.get("parameters", {})
                            if not isinstance(consumer_parameters, Mapping):
                                raise CutoverError(
                                    f"CANONICAL_CONSUMER_PARAMETERS_INVALID:{item['reference_id']}"
                                )
                            consumer["parameters"] = _rewrite_consumer_fields(
                                consumer_parameters, fields
                            )
            if isinstance(selector, Mapping) and selector.get("__rl") is True:
                selector = dict(selector)
                selector["mode"] = "name"
                selector["value"] = target
                parameters["dataTableId"] = selector
            else:
                parameters["dataTableId"] = target
        content = json.dumps(workflow, ensure_ascii=False, separators=(",", ":"))
        corpus.update(
            relative.encode("utf-8") + b"\0" + content.encode("utf-8") + b"\0"
        )
        files.append({"path": relative, "content": content})
    return {
        "schema_version": "finance-four-table-canonical-source-v1",
        "source_head": source_head,
        "generator_head": generator_head,
        "accepted_identity_sha256": identity_digest,
        "source_corpus_sha256": corpus.hexdigest(),
        "legacy_reference_inventory_sha256": APPROVED_LEGACY_REFERENCE_INVENTORY_SHA256,
        "files": files,
    }


def _canonical_target_value(
    value: Any, column_type: str, *, table: str, column: str
) -> Any:
    if value is None:
        return None
    if column_type == "string":
        if not isinstance(value, str):
            raise CutoverError(f"TARGET_ROW_TYPE_INVALID:{table}:{column}")
        return value
    if column_type == "number":
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise CutoverError(f"TARGET_ROW_TYPE_INVALID:{table}:{column}")
        return value
    if column_type == "boolean":
        if not isinstance(value, bool):
            raise CutoverError(f"TARGET_ROW_TYPE_INVALID:{table}:{column}")
        return value
    if column_type == "date":
        if not isinstance(value, str):
            raise CutoverError(f"TARGET_ROW_TYPE_INVALID:{table}:{column}")
        try:
            parsed = datetime.fromisoformat(
                value[:-1] + "+00:00" if value.endswith("Z") else value
            )
        except ValueError as error:
            raise CutoverError(f"TARGET_ROW_TYPE_INVALID:{table}:{column}") from error
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return (
            parsed.astimezone(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
    raise CutoverError(f"TARGET_COLUMN_TYPE_INVALID:{table}:{column}")


def _normalized_target_rows(
    runner: Any, matrix: Mapping[str, Any], name: str
) -> list[dict[str, Any]]:
    target_schema = matrix["target_schemas"].get(name)
    columns = (
        target_schema.get("columns") if isinstance(target_schema, Mapping) else None
    )
    rows = runner.target_tables.get(name)
    if not isinstance(columns, Mapping):
        raise CutoverError(f"TARGET_SCHEMA_INVALID:{name}")
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise CutoverError(f"TARGET_ROWS_INVALID:{name}")
    column_names = sorted(columns)
    normalized = [
        {
            column: _canonical_target_value(
                row.get(column),
                str(columns[column].get("type", "")).lower()
                if isinstance(columns[column], Mapping)
                else "",
                table=name,
                column=column,
            )
            for column in column_names
        }
        for row in rows
    ]
    return sorted(
        normalized,
        key=lambda row: json.dumps(
            row, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ),
    )


def _canonical_runtime_source_bundle(
    args: argparse.Namespace,
    *,
    source_head: str,
    generator_head: str,
    identity_digest: str,
    source_backup_sha256: str,
    migration_receipt_sha256: str,
    migration_receipt: Mapping[str, Any],
    runner: Any,
    matrix: Mapping[str, Any],
) -> dict[str, Any]:
    bundle = _canonical_source_bundle(
        args, source_head, generator_head, identity_digest
    )
    targets: list[dict[str, Any]] = []
    schema_digests = _target_schema_digests(matrix)
    for name in sorted(TARGETS):
        target_schema = matrix["target_schemas"].get(name)
        columns = (
            target_schema.get("columns") if isinstance(target_schema, Mapping) else None
        )
        logical_key = (
            target_schema.get("logical_key")
            if isinstance(target_schema, Mapping)
            else None
        )
        if (
            not isinstance(columns, Mapping)
            or not isinstance(logical_key, list)
            or not logical_key
            or any(key not in columns for key in logical_key)
        ):
            raise CutoverError(f"TARGET_SCHEMA_INVALID:{name}")
        rows = _normalized_target_rows(runner, matrix, name)
        seen: set[str] = set()
        for row in rows:
            key = json.dumps(
                [_canonical(row[field]) for field in logical_key],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if any(row[field] is None for field in logical_key) or key in seen:
                raise CutoverError(f"TARGET_LOGICAL_KEY_INVALID:{name}")
            seen.add(key)
        targets.append(
            {
                "name": name,
                "schema_sha256": schema_digests[name],
                "columns": [
                    {"name": field, "type": str(spec.get("type", "")).lower()}
                    for field, spec in sorted(columns.items())
                    if isinstance(spec, Mapping)
                ],
                "logical_key": list(logical_key),
                "row_count": len(rows),
                "rows_sha256": _digest_json_without_newline(
                    [
                        json.dumps(
                            row,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                        for row in rows
                    ]
                ),
                "rows": rows,
            }
        )
    projection_sha256 = hashlib.sha256(_canonical_bytes(targets)).hexdigest()
    return {
        **bundle,
        "schema_version": "finance-four-table-canonical-source-v2",
        "source_backup_sha256": source_backup_sha256,
        "migration_receipt_sha256": migration_receipt_sha256,
        "migration_matrix_sha256": hashlib.sha256(MATRIX_PATH.read_bytes()).hexdigest(),
        "target_digest": _require_digest(
            migration_receipt.get("target_digest"), "TARGET_DIGEST"
        ),
        "target_projection_sha256": projection_sha256,
        "targets": targets,
    }


def _check_reference_rewrite(workflow_root: Path | None) -> dict[str, Any]:
    if workflow_root is None:
        return {"checked": False, "verified": False, "legacy_references": []}
    if not workflow_root.is_dir():
        raise CutoverError("WORKFLOW_ROOT_UNAVAILABLE")
    legacy = _legacy_names() - PRESERVED_SOURCE_TABLES - {"finance_execution_failures"}
    known_workflows = {path.name for path in WORKFLOW_ROOT.glob("*.json")}
    references: list[dict[str, str]] = []
    for path in sorted(workflow_root.rglob("*")):
        if (
            not path.is_file()
            or path.suffix.lower() not in {".json", ".js", ".cjs", ".ts", ".txt"}
            or path.name in known_workflows
        ):
            continue
        text = path.read_text(encoding="utf-8")
        for name in sorted(legacy):
            if name in text:
                references.append(
                    {"path": str(path.relative_to(workflow_root)), "table": name}
                )
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
        rows = (
            value.get("rows") if isinstance(value, dict) and "rows" in value else value
        )
        if (
            not isinstance(name, str)
            or not isinstance(rows, list)
            or any(not isinstance(row, dict) for row in rows)
        ):
            raise CutoverError("SOURCE_BACKUP_ROWS_INVALID")
        result[name] = [dict(row) for row in rows]
    return result


def _target_table_receipts(
    runner: Any, matrix: Mapping[str, Any]
) -> list[dict[str, Any]]:
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
        rows = _normalized_target_rows(runner, matrix, name)
        row_strings = [
            json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            for row in rows
        ]
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
    observed = _require_digest(
        state.get("runtime_state_sha256"), "RUNTIME_STATE_SHA256"
    )
    if (
        hashlib.sha256(_canonical_bytes(_runtime_state_without_hash(state))).hexdigest()
        != observed
    ):
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
    rollback_runtime_receipt_sha256: str | None = None,
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
    if (
        workflow_export_sha256 is not None
        and state.get("workflow_export_sha256") != workflow_export_sha256
    ):
        raise CutoverError("RUNTIME_STATE_EXPORT_BINDING_MISMATCH")
    if (
        lock_receipt_sha256 is not None
        and state.get("lock_receipt_sha256") != lock_receipt_sha256
    ):
        raise CutoverError("RUNTIME_STATE_LOCK_BINDING_MISMATCH")
    if (
        rollback_runtime_receipt_sha256 is not None
        and state.get("rollback_runtime_receipt_sha256")
        != rollback_runtime_receipt_sha256
    ):
        raise CutoverError("RUNTIME_STATE_ROLLBACK_RECEIPT_BINDING_MISMATCH")
    if binding is not None:
        _validate_binding(state, binding, "RUNTIME_STATE")


def _parse_readback(
    path: Path, migration_sha256: str, expected_phase: str
) -> dict[str, Any]:
    parser_spec = importlib.util.spec_from_file_location(
        "finance_readback_parser", READBACK_PARSER_PATH
    )
    if parser_spec is None or parser_spec.loader is None:
        raise CutoverError("READBACK_PARSER_UNAVAILABLE")
    parser = importlib.util.module_from_spec(parser_spec)
    parser_spec.loader.exec_module(parser)
    raw = path.read_text(encoding="utf-8")
    try:
        payload = parser.parse_data_table_receipt(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise CutoverError("READBACK_RECEIPT_INVALID") from error
    migration_receipt = payload.get("migration_receipt")
    if (
        not isinstance(migration_receipt, dict)
        or migration_receipt.get("bound") is not True
        or migration_receipt.get("sha256") != migration_sha256
    ):
        raise CutoverError("READBACK_MIGRATION_RECEIPT_MISMATCH")
    observed_phase = payload.get("phase")
    legacy_post = observed_phase is None and expected_phase in {
        "FORWARD_POST",
        "ROLLBACK_POST",
    }
    if observed_phase != expected_phase and not legacy_post:
        raise CutoverError("READBACK_PHASE_MISMATCH")
    if expected_phase == "FORWARD_PRE":
        if payload.get("status") != "FORWARD_PRE_READBACK":
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
    if payload.get("status") != "VERIFIED":
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


def _validate_target_readback(
    payload: Mapping[str, Any], matrix: Mapping[str, Any], *, label: str
) -> dict[str, Any]:
    if not payload.get("verified") or payload.get("finance_tables") != 4:
        raise CutoverError(f"{label}_READBACK_REQUIRED")
    tables = payload.get("tables")
    expected_names = sorted(TARGETS)
    if (
        not isinstance(tables, list)
        or [table.get("name") for table in tables] != expected_names
    ):
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
        "digest_sha256": _require_digest(
            payload.get("digest_sha256"), f"{label}_DIGEST"
        ),
        "finance_tables": 4,
        "total_rows": payload["total_rows"],
        "tables": result_tables,
    }


def _projection_matches(
    tables: Sequence[Mapping[str, Any]],
    expected: Mapping[str, Mapping[str, Any]],
) -> bool:
    return all(
        (wanted := expected.get(table["name"])) is not None
        and all(
            table[field] == wanted[field]
            for field in ("schema_sha256", "row_count", "rows_sha256")
        )
        for table in tables
    )


def _compare_forward_readbacks(
    before: Mapping[str, Any],
    first_after: Mapping[str, Any],
    second_after: Mapping[str, Any],
    matrix: Mapping[str, Any],
    expected_tables: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    observed_before = _validate_target_readback(before, matrix, label="FORWARD_PRE")
    first = _validate_target_readback(first_after, matrix, label="FIRST_POST")
    second = _validate_target_readback(second_after, matrix, label="SECOND_POST")
    if observed_before.get("phase") != "FORWARD_PRE":
        raise CutoverError("FORWARD_PRE_READBACK_REQUIRED")
    if first.get("phase") != "FORWARD_POST" or second.get("phase") != "FORWARD_POST":
        raise CutoverError("FORWARD_POST_READBACK_REQUIRED")
    if first != second:
        raise CutoverError("SECOND_RUNTIME_RUN_NOT_NOOP")
    expected = {table["name"]: table for table in expected_tables}
    empty_prestate = all(table["row_count"] == 0 for table in observed_before["tables"])
    projected_prestate = _projection_matches(observed_before["tables"], expected)
    if not empty_prestate and not projected_prestate:
        raise CutoverError("FORWARD_PRESTATE_NOT_EMPTY_OR_PROJECTED")
    if not _projection_matches(first["tables"], expected):
        raise CutoverError("RUNTIME_PROJECTION_DIGEST_MISMATCH")
    return first


def _compare_rollback_readbacks(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    runtime_prestate: Sequence[Mapping[str, Any]],
    matrix: Mapping[str, Any],
    expected_tables: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    observed_before = _validate_target_readback(before, matrix, label="ROLLBACK_PRE")
    observed_after = _validate_target_readback(after, matrix, label="ROLLBACK_POST")
    if (
        observed_before.get("phase") != "ROLLBACK_PRE"
        or observed_after.get("phase") != "ROLLBACK_POST"
    ):
        raise CutoverError("ROLLBACK_READBACK_PHASE_MISMATCH")
    expected = {table["name"]: table for table in expected_tables}
    if not _projection_matches(observed_before["tables"], expected):
        raise CutoverError("ROLLBACK_PRE_PROJECTION_MISMATCH")
    prestate = {table.get("name"): table for table in runtime_prestate}
    if set(prestate) != set(TARGETS):
        raise CutoverError("ROLLBACK_TARGET_PRESTATE_INVALID")
    for table in observed_after["tables"]:
        wanted = prestate[table["name"]]
        if table["row_count"] != wanted.get("row_count") or table[
            "rows_sha256"
        ] != wanted.get("rows_sha256"):
            raise CutoverError(f"ROLLBACK_TARGET_RESTORATION_MISMATCH:{table['name']}")
    return observed_after


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
            [
                "git",
                "-C",
                str(repository_root),
                "status",
                "--porcelain",
                "--untracked-files=no",
            ],
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
        generator_head = (
            subprocess.run(
                [sys.executable, str(MIGRATION_PATH), "--schema-digest"],
                check=True,
                capture_output=True,
                text=True,
            )
            .stdout.splitlines()[-1]
            .strip()
        )
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
    identity_digest = _require_digest(
        identity.get("identity_sha256"), "IDENTITY_SHA256"
    )
    unsigned_identity = dict(identity)
    unsigned_identity.pop("identity_sha256", None)
    if (
        hashlib.sha256(_canonical_bytes(unsigned_identity)).hexdigest()
        != identity_digest
    ):
        raise CutoverError("ACCEPTED_CHECKOUT_IDENTITY_INTEGRITY_MISMATCH")
    receipt_sha = _require_digest(
        args.migration_receipt_sha256, "MIGRATION_RECEIPT_SHA256"
    )
    source_backup_sha = _require_digest(
        args.source_backup_sha256, "SOURCE_BACKUP_SHA256"
    )
    if identity.get("migration_receipt_sha256") != receipt_sha:
        raise CutoverError("ACCEPTED_MIGRATION_RECEIPT_SHA256_MISMATCH")
    if identity.get("source_backup_sha256") != source_backup_sha:
        raise CutoverError("ACCEPTED_SOURCE_BACKUP_SHA256_MISMATCH")
    if args.operator_ack != expected_ack or args.runtime_action != expected_action:
        raise CutoverError("NAMED_OPERATOR_ACK_REQUIRED")
    _validate_legacy_reference_identity(identity, args)
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
    validate_target_schema: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, str]]:
    live_export_path = getattr(
        args, "live_export", None
    ) or args.migration_receipt.with_name(LIVE_EXPORT_FILENAME)
    lock_receipt_path = getattr(
        args, "lock_receipt", None
    ) or args.migration_receipt.with_name(LOCK_RECEIPT_FILENAME)
    matrix = _load_matrix()
    binding = _binding_inputs(args)
    export = _validate_live_export(
        live_export_path,
        source_head=source_head,
        generator_head=generator_head,
        migration_receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        required_export_digest=binding["required_live_export_digest"],
        matrix=matrix,
        project_id=getattr(args, "project_id", None),
        validate_target_schema=validate_target_schema,
    )
    project_id = export["project_id"]
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
    runtime_receipt_sha: str | None = None,
    rollback_runtime_receipt_sha: str | None = None,
    verify_resolvers: bool = True,
) -> None:
    """Reject a source, receipt, or export changed after preflight."""
    try:
        observed_head = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        observed_generator = (
            subprocess.run(
                [sys.executable, str(MIGRATION_PATH), "--schema-digest"],
                check=True,
                capture_output=True,
                text=True,
            )
            .stdout.splitlines()[-1]
            .strip()
        )
    except (OSError, subprocess.CalledProcessError, IndexError) as error:
        raise CutoverError("CURRENTNESS_CHECK_UNAVAILABLE") from error
    if observed_head != source_head or observed_generator != generator_head:
        raise CutoverError("SOURCE_CURRENTNESS_DRIFT")
    try:
        observed_source_sha = hashlib.sha256(
            args.source_backup.read_bytes()
        ).hexdigest()
        observed_receipt_sha = hashlib.sha256(
            args.migration_receipt.read_bytes()
        ).hexdigest()
    except OSError as error:
        raise CutoverError("CURRENTNESS_INPUT_UNAVAILABLE") from error
    if observed_source_sha != source_backup_sha or observed_receipt_sha != receipt_sha:
        raise CutoverError("RECEIPT_CURRENTNESS_DRIFT")
    if export_sha is not None:
        export_path = getattr(
            args, "live_export", None
        ) or args.migration_receipt.with_name(LIVE_EXPORT_FILENAME)
        export = _read_json(export_path)[0]
        observed_export_sha = export.get("export_sha256")
        if observed_export_sha != export_sha:
            raise CutoverError("LIVE_EXPORT_CURRENTNESS_DRIFT")
    identity_path = args.accepted_identity or args.migration_receipt.with_name(
        "finance-four-table-accepted-identity.json"
    )
    _require_protected(identity_path)
    identity, _ = _read_json(identity_path)
    unsigned_identity = dict(identity)
    observed_identity = unsigned_identity.pop("identity_sha256", None)
    if (
        observed_identity != identity_digest
        or hashlib.sha256(_canonical_bytes(unsigned_identity)).hexdigest()
        != identity_digest
    ):
        raise CutoverError("ACCEPTED_IDENTITY_CURRENTNESS_DRIFT")
    _validate_legacy_reference_identity(identity, args)
    if verify_resolvers:
        _protected_resolver_input(
            args, "alias_bundle", identity, limit=16 * 1024 * 1024
        )
        _protected_resolver_input(
            args, "verification_artifacts", identity, limit=64 * 1024 * 1024
        )
    if runtime_receipt_sha is not None:
        _, observed_runtime_sha = _read_forward_runtime_receipt(args)
        if observed_runtime_sha != runtime_receipt_sha:
            raise CutoverError("FORWARD_RUNTIME_RECEIPT_CURRENTNESS_DRIFT")
    if rollback_runtime_receipt_sha is not None:
        _, observed_rollback_runtime_sha = _read_rollback_runtime_receipt(args)
        if observed_rollback_runtime_sha != rollback_runtime_receipt_sha:
            raise CutoverError("ROLLBACK_RUNTIME_RECEIPT_CURRENTNESS_DRIFT")


def run_forward(args: argparse.Namespace) -> dict[str, Any]:
    source_head, generator_head, receipt_sha, source_backup_sha, identity_digest = (
        _heads(args, REQUIRED_FORWARD_ACK, FORWARD_RUNTIME_ACTION)
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
    runner = _migration_runner(args, module, source, source_head, source_backup_sha)
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
    second_after = _parse_readback(
        args.second_post_readback_raw, receipt_sha, "FORWARD_POST"
    )
    table_receipts = _target_table_receipts(runner, matrix)
    readback = _compare_forward_readbacks(
        before, after, second_after, matrix, table_receipts
    )
    runtime_receipt, runtime_receipt_sha = _read_forward_runtime_receipt(args)
    if (
        runtime_receipt is None
        or runtime_receipt.get("schema_version") != "finance-four-table-runtime-plan-v3"
        or runtime_receipt.get("target_digest") != first["target_digest"]
        or runtime_receipt.get("target_row_counts")
        != {name: len(runner.target_tables[name]) for name in sorted(TARGETS)}
    ):
        raise CutoverError("FORWARD_RUNTIME_TARGET_RECEIPT_REQUIRED")
    runtime_prestate = runtime_receipt.get("target_prestate")
    empty_rows_sha256 = _digest_json_without_newline([])
    if (
        not isinstance(runtime_prestate, list)
        or {table.get("name") for table in runtime_prestate} != set(TARGETS)
        or any(
            table.get("row_count") != 0 or table.get("rows_sha256") != empty_rows_sha256
            for table in runtime_prestate
        )
    ):
        raise CutoverError("FORWARD_RUNTIME_EMPTY_BASELINE_REQUIRED")
    first_run_created = all(
        table.get("row_count") == 0 for table in before.get("tables", [])
    )
    _validate_output_path(args, args.runtime_state, "RUNTIME_STATE_OUTPUT")
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
            "forward_runtime_receipt_sha256": runtime_receipt_sha,
            "target_projection_sha256": runtime_receipt["target_projection_sha256"],
            "rollback_targets_sha256": runtime_receipt["rollback_targets_sha256"],
            "target_rows_applied": True,
            "target_replay_noop": not first_run_created,
            **binding,
        },
    )
    result = _seal(
        {
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
            "first_run_created": first_run_created,
            "readback": readback,
            "pre_readback": before,
            "post_readback": after,
            "second_post_readback": second_after,
            "runtime_execution": True,
            "runtime_action": FORWARD_RUNTIME_ACTION,
            "runtime_state_sha256": runtime_state_sha,
            "forward_runtime_receipt_sha256": runtime_receipt_sha,
            "target_projection_sha256": runtime_receipt["target_projection_sha256"],
            "rollback_targets_sha256": runtime_receipt["rollback_targets_sha256"],
            "target_rows_applied": True,
            "old_tables_preserved": True,
            "runtime_cutover": False,
            "deletion_authorized": False,
            "operator_ack": REQUIRED_FORWARD_ACK,
        }
    )
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
        runtime_receipt_sha=runtime_receipt_sha,
    )
    _validate_output_path(args, args.output, "CUTOVER_RECEIPT_OUTPUT")
    _write_json(args.output, result)
    return result


def run_rollback(args: argparse.Namespace) -> dict[str, Any]:
    source_head, generator_head, receipt_sha, source_backup_sha, identity_digest = (
        _heads(args, REQUIRED_ROLLBACK_ACK, ROLLBACK_RUNTIME_ACTION)
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
    if (
        migration_receipt.get("old_tables_preserved") is not True
        or migration_receipt.get("deletion_authorized") is not False
    ):
        raise CutoverError("ROLLBACK_ONLY_BEFORE_LEGACY_DELETION")
    runtime_receipt, runtime_receipt_sha = _read_forward_runtime_receipt(args)
    if (
        runtime_receipt is None
        or runtime_receipt_sha is None
        or runtime_receipt.get("schema_version") != "finance-four-table-runtime-plan-v3"
    ):
        raise CutoverError("FORWARD_RUNTIME_TARGET_RECEIPT_REQUIRED")
    _read_forward_cutover_receipt(
        args,
        receipt_sha=receipt_sha,
        source_head=source_head,
        generator_head=generator_head,
        source_backup_sha256=observed_source_backup_sha,
        binding=binding,
        runtime_receipt=runtime_receipt,
        runtime_receipt_sha256=runtime_receipt_sha,
    )
    rollback_runtime_receipt, rollback_runtime_receipt_sha = (
        _read_rollback_runtime_receipt(args)
    )
    if rollback_runtime_receipt is None or rollback_runtime_receipt_sha is None:
        raise CutoverError("ROLLBACK_RUNTIME_RECEIPT_REQUIRED")
    _validate_rollback_runtime_binding(
        rollback_runtime_receipt,
        export=export,
        binding=binding,
        forward_receipt=runtime_receipt,
        forward_receipt_sha256=runtime_receipt_sha,
    )
    module = _load_migration_module()
    matrix = _load_matrix()
    runner = _migration_runner(args, module, source, source_head, source_backup_sha)
    if runner.run() != migration_receipt:
        raise CutoverError("MIGRATION_RECEIPT_CONTENT_MISMATCH")
    expected_tables = _target_table_receipts(runner, matrix)
    before = _parse_readback(args.pre_readback_raw, receipt_sha, "ROLLBACK_PRE")
    after = _parse_readback(args.post_readback_raw, receipt_sha, "ROLLBACK_POST")
    readback = _compare_rollback_readbacks(
        before,
        after,
        runtime_receipt["target_prestate"],
        matrix,
        expected_tables,
    )
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
        rollback_runtime_receipt_sha256=rollback_runtime_receipt_sha,
    )
    if (
        runtime_state.get("status") != "RESTORED"
        or runtime_state.get("target_tables_created") is not True
        or runtime_state.get("target_tables_untouched") is not False
        or runtime_state.get("target_rows_restored") is not True
        or runtime_state.get("forward_runtime_receipt_sha256") != runtime_receipt_sha
        or runtime_state.get("restored_source_digest")
        != migration_receipt.get("source_digest")
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
        rollback_runtime_receipt_sha256=rollback_runtime_receipt_sha,
        workflow_export_sha256=export["export_sha256"] if export else None,
        lock_receipt_sha256=lock_sha,
        binding=binding,
    )
    _require_protected(args.source_backup, "PROTECTED_SOURCE_BACKUP")
    try:
        current_source_backup_sha = hashlib.sha256(
            args.source_backup.read_bytes()
        ).hexdigest()
    except OSError as error:
        raise CutoverError("SOURCE_BACKUP_UNAVAILABLE_AFTER_ROLLBACK") from error
    if current_source_backup_sha != observed_source_backup_sha:
        raise CutoverError("SOURCE_BACKUP_CHANGED_DURING_ROLLBACK")
    source_digest = migration_receipt.get("source_digest")
    if (
        runtime_state.get("restore_roundtrip") is not True
        or runtime_state.get("source_digest") != source_digest
        or runtime_state.get("restored_source_digest") != source_digest
        or runtime_state.get("target_tables_untouched") is not False
        or runtime_state.get("target_rows_restored") is not True
    ):
        raise CutoverError("EXACT_ROLLBACK_DIGEST_RESTORATION_REQUIRED")
    result = _seal(
        {
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
            "target_tables_untouched": False,
            "target_rows_restored": True,
            "forward_runtime_receipt_sha256": runtime_receipt_sha,
            "rollback_runtime_receipt_sha256": rollback_runtime_receipt_sha,
            "target_projection_sha256": runtime_receipt["target_projection_sha256"],
            "rollback_targets_sha256": runtime_receipt["rollback_targets_sha256"],
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
        }
    )
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
        runtime_receipt_sha=runtime_receipt_sha,
        rollback_runtime_receipt_sha=rollback_runtime_receipt_sha,
    )
    _validate_output_path(args, args.output, "CUTOVER_RECEIPT_OUTPUT")
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
    rollback_runtime_receipt_sha256: str,
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
        or proof.get("target_tables_untouched") is not False
        or proof.get("target_rows_restored") is not True
        or proof.get("rollback_runtime_receipt_sha256")
        != rollback_runtime_receipt_sha256
    ):
        raise CutoverError("ROLLBACK_RUNTIME_PROOF_BINDING_MISMATCH")
    if (
        workflow_export_sha256 is not None
        and proof.get("workflow_export_sha256") != workflow_export_sha256
    ):
        raise CutoverError("ROLLBACK_RUNTIME_PROOF_EXPORT_MISMATCH")
    if (
        lock_receipt_sha256 is not None
        and proof.get("lock_receipt_sha256") != lock_receipt_sha256
    ):
        raise CutoverError("ROLLBACK_RUNTIME_PROOF_LOCK_MISMATCH")
    if binding is not None:
        _validate_binding(proof, binding, "ROLLBACK_RUNTIME_PROOF")
    source_digest = (
        _load_migration_module().MigrationRunner(_source_rows(source)).backup_digest()
    )
    if (
        proof.get("source_digest") != source_digest
        or proof.get("restored_source_digest") != source_digest
    ):
        raise CutoverError("ROLLBACK_RUNTIME_PROOF_DIGEST_MISMATCH")
    integrity = _require_digest(
        proof.get("runtime_proof_sha256"), "RUNTIME_PROOF_SHA256"
    )
    unsigned = dict(proof)
    unsigned.pop("runtime_proof_sha256", None)
    if hashlib.sha256(_canonical_bytes(unsigned)).hexdigest() != integrity:
        raise CutoverError("ROLLBACK_RUNTIME_PROOF_INTEGRITY_MISMATCH")


def run_rollback_runtime(args: argparse.Namespace) -> dict[str, Any]:
    source_head, generator_head, receipt_sha, source_backup_sha, identity_digest = (
        _heads(args, REQUIRED_ROLLBACK_ACK, ROLLBACK_RUNTIME_ACTION)
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
    source, migration_receipt, _, observed_source_backup_sha = _source_and_receipt(
        args.source_backup, args.migration_receipt, receipt_sha, source_backup_sha
    )
    if (
        migration_receipt.get("old_tables_preserved") is not True
        or migration_receipt.get("deletion_authorized") is not False
    ):
        raise CutoverError("ROLLBACK_ONLY_BEFORE_LEGACY_DELETION")
    module = _load_migration_module()
    runner = _migration_runner(args, module, source, source_head, source_backup_sha)
    if runner.run() != migration_receipt:
        raise CutoverError("MIGRATION_RECEIPT_CONTENT_MISMATCH")
    forward_receipt, forward_receipt_sha = _read_forward_runtime_receipt(args)
    if (
        forward_receipt is None
        or forward_receipt_sha is None
        or forward_receipt.get("schema_version") != "finance-four-table-runtime-plan-v3"
        or forward_receipt.get("target_digest")
        != migration_receipt.get("target_digest")
    ):
        raise CutoverError("FORWARD_RUNTIME_TARGET_RECEIPT_REQUIRED")
    forward_cutover_receipt = _read_forward_cutover_receipt(
        args,
        receipt_sha=receipt_sha,
        source_head=source_head,
        generator_head=generator_head,
        source_backup_sha256=observed_source_backup_sha,
        binding=binding,
        runtime_receipt=forward_receipt,
        runtime_receipt_sha256=forward_receipt_sha,
    )
    rollback_receipt, rollback_receipt_sha = _read_rollback_runtime_receipt(args)
    if rollback_receipt is None or rollback_receipt_sha is None:
        raise CutoverError("ROLLBACK_RUNTIME_RECEIPT_REQUIRED")
    _validate_rollback_runtime_binding(
        rollback_receipt,
        export=export,
        binding=binding,
        forward_receipt=forward_receipt,
        forward_receipt_sha256=forward_receipt_sha,
    )
    source_digest = _require_digest(
        migration_receipt.get("source_digest"), "SOURCE_DIGEST"
    )
    runtime_state, current_state_sha = _read_runtime_state(args.runtime_state)
    if runtime_state.get("operation") == "FORWARD":
        _validate_runtime_state(
            runtime_state,
            operation="FORWARD",
            receipt_sha=receipt_sha,
            source_head=source_head,
            generator_head=generator_head,
            identity_digest=identity_digest,
            source_digest=source_digest,
            source_backup_sha256=observed_source_backup_sha,
            workflow_export_sha256=forward_receipt["export_sha256"],
            binding=binding,
        )
        if (
            runtime_state.get("status") != "MIGRATED"
            or runtime_state.get("target_tables_created") is not True
            or runtime_state.get("target_tables_untouched") is not False
            or runtime_state.get("target_rows_applied") is not True
            or runtime_state.get("forward_runtime_receipt_sha256")
            != forward_receipt_sha
            or forward_cutover_receipt.get("runtime_state_sha256") != current_state_sha
        ):
            raise CutoverError("FORWARD_RUNTIME_STATE_REQUIRED")
        restored = runner.restore_backup()
        if (
            restored.get("restore_roundtrip") is not True
            or runner.backup_digest() != source_digest
            or restored.get("source_digest") != source_digest
        ):
            raise CutoverError("EXACT_ROLLBACK_DIGEST_RESTORATION_REQUIRED")
        previous_state_sha = current_state_sha
        restored_state_sha: str | None = None
    elif runtime_state.get("operation") == "ROLLBACK":
        _validate_runtime_state(
            runtime_state,
            operation="ROLLBACK",
            receipt_sha=receipt_sha,
            source_head=source_head,
            generator_head=generator_head,
            identity_digest=identity_digest,
            source_digest=source_digest,
            source_backup_sha256=observed_source_backup_sha,
            workflow_export_sha256=export["export_sha256"],
            lock_receipt_sha256=lock_sha,
            rollback_runtime_receipt_sha256=rollback_receipt_sha,
            binding=binding,
        )
        if (
            runtime_state.get("status") != "RESTORED"
            or runtime_state.get("target_tables_created") is not True
            or runtime_state.get("target_tables_untouched") is not False
            or runtime_state.get("target_rows_restored") is not True
            or runtime_state.get("forward_runtime_receipt_sha256")
            != forward_receipt_sha
            or runtime_state.get("restored_source_digest") != source_digest
            or runtime_state.get("restore_roundtrip") is not True
        ):
            raise CutoverError("RUNTIME_STATE_RESTORATION_REQUIRED")
        previous_state_sha = _require_digest(
            runtime_state.get("runtime_state_before_sha256"),
            "RUNTIME_STATE_BEFORE_SHA256",
        )
        if previous_state_sha != forward_cutover_receipt.get("runtime_state_sha256"):
            raise CutoverError("RUNTIME_STATE_FORWARD_CHAIN_MISMATCH")
        restored_state_sha = current_state_sha
        restored = {"source_digest": source_digest, "restore_roundtrip": True}
    else:
        raise CutoverError("RUNTIME_STATE_OPERATION_INVALID")
    _require_protected(args.source_backup, "PROTECTED_SOURCE_BACKUP")
    try:
        current_source_backup_sha = hashlib.sha256(
            args.source_backup.read_bytes()
        ).hexdigest()
    except OSError as error:
        raise CutoverError("SOURCE_BACKUP_UNAVAILABLE_AFTER_ROLLBACK") from error
    if current_source_backup_sha != observed_source_backup_sha:
        raise CutoverError("SOURCE_BACKUP_CHANGED_DURING_ROLLBACK")
    _assert_currentness(
        args,
        source_head=source_head,
        generator_head=generator_head,
        receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        export_sha=export["export_sha256"],
        runtime_receipt_sha=forward_receipt_sha,
        rollback_runtime_receipt_sha=rollback_receipt_sha,
    )
    _validate_output_path(args, args.runtime_state, "RUNTIME_STATE_OUTPUT")
    _validate_output_path(args, args.output, "RUNTIME_PROOF_OUTPUT")
    if restored_state_sha is None:
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
                "target_tables_untouched": False,
                "target_rows_restored": True,
                "forward_runtime_receipt_sha256": forward_receipt_sha,
                "rollback_runtime_receipt_sha256": rollback_receipt_sha,
                "target_projection_sha256": forward_receipt["target_projection_sha256"],
                "rollback_targets_sha256": forward_receipt["rollback_targets_sha256"],
                "restored_source_digest": restored["source_digest"],
                "restore_roundtrip": restored["restore_roundtrip"],
                "runtime_state_before_sha256": previous_state_sha,
                "runtime_action": ROLLBACK_RUNTIME_ACTION,
                "operator_ack": REQUIRED_ROLLBACK_ACK,
                "workflow_export_sha256": export["export_sha256"],
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
            "target_tables_untouched": False,
            "target_rows_restored": True,
            "forward_runtime_receipt_sha256": forward_receipt_sha,
            "rollback_runtime_receipt_sha256": rollback_receipt_sha,
            "pre_delete": True,
            "runtime_execution": True,
            "runtime_action": ROLLBACK_RUNTIME_ACTION,
            "operator_ack": REQUIRED_ROLLBACK_ACK,
            "runtime_state_sha256": restored_state_sha,
            "runtime_state_before_sha256": previous_state_sha,
            "runtime_command": "rollback-runtime",
            "workflow_export_sha256": export["export_sha256"],
            "lock_receipt_sha256": lock_sha,
            **binding,
        }
    )
    result["runtime_proof_sha256"] = result.pop("cutover_receipt_sha256")
    _assert_currentness(
        args,
        source_head=source_head,
        generator_head=generator_head,
        receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        export_sha=export["export_sha256"],
        runtime_receipt_sha=forward_receipt_sha,
        rollback_runtime_receipt_sha=rollback_receipt_sha,
    )
    _write_json(args.output, result)
    return result


def validate_inputs(args: argparse.Namespace) -> dict[str, Any]:
    expected_ack, expected_action = (
        (REQUIRED_ROLLBACK_ACK, ROLLBACK_RUNTIME_ACTION)
        if args.operation_kind == "ROLLBACK"
        else (REQUIRED_FORWARD_ACK, FORWARD_RUNTIME_ACTION)
    )
    source_head, generator_head, receipt_sha, source_backup_sha, identity_digest = (
        _heads(args, expected_ack, expected_action)
    )
    source, migration_receipt, observed_receipt_sha, observed_source_backup_sha = (
        _source_and_receipt(
            args.source_backup, args.migration_receipt, receipt_sha, source_backup_sha
        )
    )
    runtime_receipt, runtime_receipt_sha = _read_forward_runtime_receipt(args)
    legacy_rollback = (
        runtime_receipt is not None
        and runtime_receipt["schema_version"] == "finance-four-table-runtime-plan-v1"
    )
    export, _lock_receipt, lock_sha, binding = _bound_live_inputs(
        args,
        source_head=source_head,
        generator_head=generator_head,
        receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        operation=args.operation_kind,
        validate_target_schema=not legacy_rollback,
    )
    _validate_forward_runtime_binding(runtime_receipt, export, binding)
    if args.operation_kind == "ROLLBACK":
        if runtime_receipt is None or runtime_receipt_sha is None:
            raise CutoverError("FORWARD_RUNTIME_RECEIPT_REQUIRED")
        _read_forward_cutover_receipt(
            args,
            receipt_sha=receipt_sha,
            source_head=source_head,
            generator_head=generator_head,
            source_backup_sha256=observed_source_backup_sha,
            binding=binding,
            runtime_receipt=runtime_receipt,
            runtime_receipt_sha256=runtime_receipt_sha,
        )
    if not legacy_rollback:
        module = _load_migration_module()
        if (
            _migration_runner(
                args, module, source, source_head, source_backup_sha
            ).run()
            != migration_receipt
        ):
            raise CutoverError("MIGRATION_RECEIPT_CONTENT_MISMATCH")
        _check_reference_rewrite(args.workflow_root)
    _assert_currentness(
        args,
        source_head=source_head,
        generator_head=generator_head,
        receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        export_sha=export["export_sha256"],
        runtime_receipt_sha=runtime_receipt_sha,
        verify_resolvers=not legacy_rollback,
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
        "workflow_export_sha256": export["export_sha256"],
        "lock_receipt_sha256": lock_sha,
    }


def validate_preconditions(args: argparse.Namespace) -> dict[str, Any]:
    expected_ack, expected_action = (
        (REQUIRED_ROLLBACK_ACK, ROLLBACK_RUNTIME_ACTION)
        if args.operation_kind == "ROLLBACK"
        else (REQUIRED_FORWARD_ACK, FORWARD_RUNTIME_ACTION)
    )
    source_head, generator_head, receipt_sha, source_backup_sha, identity_digest = (
        _heads(args, expected_ack, expected_action)
    )
    if args.live_export is None:
        raise CutoverError("PROTECTED_LIVE_EXPORT_REQUIRED")
    source, migration_receipt, _, _ = _source_and_receipt(
        args.source_backup, args.migration_receipt, receipt_sha, source_backup_sha
    )
    runtime_receipt, runtime_receipt_sha = _read_forward_runtime_receipt(args)
    legacy_rollback = (
        runtime_receipt is not None
        and runtime_receipt["schema_version"] == "finance-four-table-runtime-plan-v1"
    )
    if (
        runtime_receipt is not None
        and args.output.resolve() == args.forward_runtime_receipt.resolve()
    ):
        raise CutoverError("FORWARD_RUNTIME_RECEIPT_OUTPUT_CONFLICT")
    binding = _binding_inputs(args)
    export = _validate_live_export(
        args.live_export,
        source_head=source_head,
        generator_head=generator_head,
        migration_receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        required_export_digest=binding["required_live_export_digest"],
        matrix=_load_matrix(),
        project_id=getattr(args, "project_id", None),
        validate_target_schema=not legacy_rollback,
    )
    _validate_forward_runtime_binding(runtime_receipt, export, binding)
    if args.operation_kind == "ROLLBACK":
        if runtime_receipt is None or runtime_receipt_sha is None:
            raise CutoverError("FORWARD_RUNTIME_RECEIPT_REQUIRED")
        _read_forward_cutover_receipt(
            args,
            receipt_sha=receipt_sha,
            source_head=source_head,
            generator_head=generator_head,
            source_backup_sha256=source_backup_sha,
            binding=binding,
            runtime_receipt=runtime_receipt,
            runtime_receipt_sha256=runtime_receipt_sha,
        )
    canonical_source = None
    if not legacy_rollback:
        module = _load_migration_module()
        migration_runner = _migration_runner(
            args, module, source, source_head, source_backup_sha
        )
        if migration_runner.run() != migration_receipt:
            raise CutoverError("MIGRATION_RECEIPT_CONTENT_MISMATCH")
        _check_reference_rewrite(args.workflow_root)
        if getattr(args, "canonical_source_output", None) is not None:
            inputs = [
                args.source_backup,
                args.migration_receipt,
                args.live_export,
                args.output,
                args.accepted_identity
                or args.migration_receipt.with_name(
                    "finance-four-table-accepted-identity.json"
                ),
                getattr(args, "alias_bundle", None),
                getattr(args, "verification_artifacts", None),
                getattr(args, "forward_runtime_receipt", None),
                getattr(args, "forward_receipt", None),
            ]
            if (
                args.canonical_source_output.is_symlink()
                or args.canonical_source_output.resolve()
                in {path.resolve() for path in inputs if path is not None}
            ):
                raise CutoverError("CANONICAL_SOURCE_OUTPUT_PATH_INVALID")
            canonical_source = _canonical_runtime_source_bundle(
                args,
                source_head=source_head,
                generator_head=generator_head,
                identity_digest=identity_digest,
                source_backup_sha256=source_backup_sha,
                migration_receipt_sha256=receipt_sha,
                migration_receipt=migration_receipt,
                runner=migration_runner,
                matrix=_load_matrix(),
            )
            if (
                args.operation_kind == "ROLLBACK"
                and runtime_receipt is not None
                and runtime_receipt.get("canonical_source_sha256")
                != canonical_source["source_corpus_sha256"]
            ):
                raise CutoverError("FORWARD_RUNTIME_CANONICAL_SOURCE_MISMATCH")
    lock = _lock_receipt(
        export=export,
        migration_receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        project_id=export["project_id"],
        operation="PRECONDITION",
        binding=binding,
    )
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
    if runtime_receipt is not None:
        result["forward_runtime_receipt_schema"] = runtime_receipt["schema_version"]
    _assert_currentness(
        args,
        source_head=source_head,
        generator_head=generator_head,
        receipt_sha=receipt_sha,
        source_backup_sha=source_backup_sha,
        identity_digest=identity_digest,
        export_sha=export["export_sha256"],
        runtime_receipt_sha=runtime_receipt_sha,
        verify_resolvers=not legacy_rollback,
    )
    if canonical_source is not None:
        _validate_output_path(
            args, args.canonical_source_output, "CANONICAL_SOURCE_OUTPUT"
        )
        _write_json(args.canonical_source_output, canonical_source)
    _validate_output_path(args, args.output, "PRECONDITION_OUTPUT")
    _write_json(args.output, lock)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for operation in (
        "forward",
        "rollback",
        "rollback-runtime",
        "validate-inputs",
        "preflight",
    ):
        command = subparsers.add_parser(operation)
        command.add_argument("--source-backup", type=Path, required=True)
        command.add_argument("--migration-receipt", type=Path, required=True)
        command.add_argument("--migration-receipt-sha256", required=True)
        command.add_argument("--source-backup-sha256", required=True)
        command.add_argument("--alias-bundle", type=Path)
        command.add_argument("--alias-bundle-sha256")
        command.add_argument("--verification-artifacts", type=Path)
        command.add_argument("--verification-artifacts-sha256")
        command.add_argument("--operation-nonce")
        command.add_argument("--protected-quiescence-receipt-digest")
        command.add_argument("--required-live-export-digest")
        command.add_argument("--contract-bijection-digest")
        command.add_argument("--repository-root", type=Path, required=True)
        command.add_argument("--project-id")
        command.add_argument("--accepted-identity", type=Path)
        command.add_argument("--operator-ack", required=True)
        command.add_argument("--runtime-action", required=True)
        command.add_argument("--workflow-root", type=Path, required=True)
        command.add_argument(
            "--output", type=Path, required=operation != "validate-inputs"
        )
        command.add_argument("--live-export", type=Path)
        command.add_argument("--lock-receipt", type=Path)
        command.add_argument("--lock-path", type=Path)
        if operation in {
            "forward",
            "rollback",
            "rollback-runtime",
            "validate-inputs",
            "preflight",
        }:
            command.add_argument("--forward-runtime-receipt", type=Path)
        if operation in {"validate-inputs", "preflight"}:
            command.add_argument(
                "--operation-kind", choices=("FORWARD", "ROLLBACK"), default="FORWARD"
            )
        if operation == "preflight":
            command.add_argument("--canonical-source-output", type=Path)
        if operation in {"forward", "rollback"}:
            command.add_argument("--pre-readback-raw", type=Path, required=True)
            command.add_argument("--post-readback-raw", type=Path, required=True)
        if operation == "forward":
            command.add_argument("--second-post-readback-raw", type=Path, required=True)
        if operation in {
            "rollback",
            "rollback-runtime",
            "validate-inputs",
            "preflight",
        }:
            command.add_argument(
                "--forward-receipt",
                type=Path,
                required=operation in {"rollback", "rollback-runtime"},
            )
        if operation == "rollback":
            command.add_argument("--runtime-proof", type=Path, required=True)
        if operation in {"rollback", "rollback-runtime"}:
            command.add_argument("--rollback-runtime-receipt", type=Path, required=True)
        if operation in {"forward", "rollback", "rollback-runtime"}:
            command.add_argument("--runtime-state", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        lock_path = getattr(args, "lock_path", None)
        with (
            _exclusive_writer_lock(lock_path)
            if lock_path is not None
            else contextlib.nullcontext()
        ):
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
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
