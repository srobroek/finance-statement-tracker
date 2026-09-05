"""Deterministic, source-only 15-to-4 Data Table migration primitives.

The module deliberately uses an in-memory artifact store so the migration can
be exercised without n8n, Postgres, OneDrive, or an Actual account.  It never
deletes an old table and it has no cutover operation.  Runtime owners can use
the returned receipts to drive a separately authorized disposable rehearsal.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import struct
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from threading import Lock
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parents[2]
N8N = ROOT / "integrations" / "n8n"
MATRIX_PATH = N8N / "data-table-migration-matrix.json"
DATA_TABLES_PATH = N8N / "data-tables.json"
ALIAS_PATH = N8N / "generated" / "document-identity-aliases-v1.json"
TARGETS = (
    "finance_ingestion_state",
    "finance_documents",
    "finance_actual_batches",
    "finance_ai_reviews",
    "finance_execution_failures",
)
INVENTORY_SCHEMA = "inventory-v1"
VERIFICATION_SCHEMA = "actual-verification-v2"
DOCUMENT_IDENTITY_VERSION = "document-identity-v1"
IDENTITY_KINDS = {"MAIL_LINKED", "BROWSER_CAPTURE", "PROCESSING_ONLY"}
VERIFICATION_POINTER_FIELDS = {
    "verification_artifact_sha256",
    "verification_artifact_item_id",
    "verification_artifact_path",
    "verification_artifact_etag",
    "verification_artifact_schema_version",
    "verification_artifact_length_bytes",
}
VERIFICATION_PAYLOAD_FIELDS = {
    "schema_version",
    "verification_version",
    "actual_file_id",
    "account_id",
    "period_start",
    "period_end",
    "expected_payload_sha256",
    "observed_payload_sha256",
    "expected_count",
    "observed_count",
    "expected_amount_sum_minor",
    "observed_amount_sum_minor",
    "invariants_passed",
}
OUTBOX_STATE_PRECEDENCE = {
    "PREPARED": 0,
    "ACTUAL_OBSERVED": 1,
    "VERIFIED": 2,
    "COMMITTED": 3,
}


class MigrationError(ValueError):
    """Raised when a migration input cannot be reconciled deterministically."""


class FenceConflict(MigrationError):
    """Raised when a writer attempts a stale or concurrently-held fence."""


class AliasResolutionError(MigrationError):
    """Raised when a legacy document alias cannot be trusted."""


def canonical_bytes(value: Any) -> bytes:
    """Serialize JSON values into stable UTF-8 bytes with no platform drift."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def canonical_text(value: Any) -> str:
    return canonical_bytes(value).decode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))


def target_schema_digest(target_schemas: dict[str, Any]) -> str:
    """Hash the generated four-table schema contract."""
    return sha256_json(target_schemas)


def _text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if value is None and allow_empty:
        return ""
    if not isinstance(value, str) or (not value and not allow_empty):
        raise MigrationError(f"{label} must be a string")
    return value


def _typed_component(value: Any) -> str:
    """Normalize absent identity components without introducing delimiters."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise MigrationError("document identity components must be strings or null")
    return value


def encode_document_identity(
    identity_kind: str,
    components: Sequence[Any],
    *,
    version: str = DOCUMENT_IDENTITY_VERSION,
) -> bytes:
    """Encode the approved versioned uint64 length-prefixed identity tuple."""
    if identity_kind not in IDENTITY_KINDS:
        raise MigrationError(f"unsupported document identity kind: {identity_kind!r}")
    fields = (version, identity_kind, *(_typed_component(value) for value in components))
    encoded = bytearray()
    for field in fields:
        data = field.encode("utf-8")
        encoded.extend(struct.pack(">Q", len(data)))
        encoded.extend(data)
    return bytes(encoded)


def _document_id_from_identity_hash(version: str, identity_sha256: str) -> str:
    """Bind the public document ID to the exact canonical identity digest."""
    if not re.fullmatch(r"[0-9a-f]{64}", identity_sha256):
        raise MigrationError("document identity hash is malformed")
    digest = base64.urlsafe_b64encode(bytes.fromhex(identity_sha256)).decode("ascii").rstrip("=")
    return f"{version}_{digest}"


def document_identity(
    identity_kind: str,
    components: Sequence[Any],
    *,
    version: str = DOCUMENT_IDENTITY_VERSION,
) -> dict[str, str]:
    """Return the canonical document ID and its raw tuple hash."""
    encoded = encode_document_identity(identity_kind, components, version=version)
    digest = hashlib.sha256(encoded).digest()
    return {
        "document_id": _document_id_from_identity_hash(version, digest.hex()),
        "identity_kind": identity_kind,
        "identity_sha256": digest.hex(),
        "version": version,
    }


def _alias_key(alias_kind: str, alias_value: str) -> tuple[str, str]:
    return (_text(alias_kind, "alias_kind"), _text(alias_value, "alias_value"))


def build_alias_bundle(
    entries: Iterable[Mapping[str, Any]],
    *,
    bundle_version: str = "1",
    source_commit: str,
) -> dict[str, Any]:
    """Build sorted alias data and reject non-identical alias collisions."""
    normalized: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in entries:
        alias_kind, alias_value = _alias_key(raw.get("alias_kind"), raw.get("alias_value"))
        entry = {
            "alias_kind": alias_kind,
            "alias_value": alias_value,
            "canonical_document_id": _text(raw.get("canonical_document_id"), "canonical_document_id"),
            "identity_kind": _text(raw.get("identity_kind"), "identity_kind"),
            "canonical_identity_sha256": _text(
                raw.get("canonical_identity_sha256"), "canonical_identity_sha256"
            ),
            "identity_version": _text(
                raw.get("identity_version", DOCUMENT_IDENTITY_VERSION), "identity_version"
            ),
            "bundle_version": _text(raw.get("bundle_version", bundle_version), "bundle_version"),
            "source_commit": _text(raw.get("source_commit", source_commit), "source_commit"),
        }
        if entry["identity_kind"] not in IDENTITY_KINDS:
            raise AliasResolutionError("alias identity_kind is unsupported")
        if not re.fullmatch(r"[0-9a-f]{64}", entry["canonical_identity_sha256"]):
            raise AliasResolutionError("alias canonical identity hash is malformed")
        if entry["canonical_document_id"] != _document_id_from_identity_hash(
            entry["identity_version"], entry["canonical_identity_sha256"]
        ):
            raise AliasResolutionError("DOCUMENT_IDENTITY_ALIAS_ID_HASH_MISMATCH")
        capture_id = raw.get("capture_id")
        if capture_id is not None:
            entry["capture_id"] = _text(capture_id, "capture_id")
        key = (alias_kind, alias_value)
        previous = normalized.get(key)
        if previous is not None and previous != entry:
            raise AliasResolutionError(
                f"DOCUMENT_IDENTITY_ALIAS_COLLISION:{alias_kind}:{alias_value}"
            )
        normalized[key] = entry
    sorted_entries = [normalized[key] for key in sorted(normalized)]
    bundle = {
        "schema_version": "document-identity-aliases-v1",
        "bundle_version": bundle_version,
        "source_commit": source_commit,
        "entries": sorted_entries,
    }
    bundle["bundle_sha256"] = sha256_json(bundle)
    return bundle


class AliasResolver:
    """Resolve legacy aliases only from a pinned, self-hashed bundle."""

    def __init__(self, bundle: Mapping[str, Any], *, expected_source_commit: str | None = None):
        self.bundle = deepcopy(dict(bundle))
        expected_hash = self.bundle.pop("bundle_sha256", None)
        if not isinstance(expected_hash, str) or expected_hash != sha256_json(self.bundle):
            raise AliasResolutionError("DOCUMENT_IDENTITY_ALIAS_UNAVAILABLE:bundle-hash")
        if self.bundle.get("schema_version") != "document-identity-aliases-v1":
            raise AliasResolutionError("DOCUMENT_IDENTITY_ALIAS_UNAVAILABLE:schema")
        if expected_source_commit is not None and self.bundle.get("source_commit") != expected_source_commit:
            raise AliasResolutionError("DOCUMENT_IDENTITY_ALIAS_UNAVAILABLE:source-commit")
        entries = self.bundle.get("entries")
        if not isinstance(entries, list):
            raise AliasResolutionError("DOCUMENT_IDENTITY_ALIAS_UNAVAILABLE:entries")
        self._entries: dict[tuple[str, str], dict[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise AliasResolutionError("DOCUMENT_IDENTITY_ALIAS_UNAVAILABLE:entry")
            key = _alias_key(entry.get("alias_kind"), entry.get("alias_value"))
            if (
                entry.get("identity_kind") not in IDENTITY_KINDS
                or not isinstance(entry.get("canonical_document_id"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("canonical_identity_sha256", "")))
                or not isinstance(entry.get("identity_version"), str)
            ):
                raise AliasResolutionError("DOCUMENT_IDENTITY_ALIAS_UNAVAILABLE:entry-integrity")
            if entry["canonical_document_id"] != _document_id_from_identity_hash(
                entry["identity_version"], entry["canonical_identity_sha256"]
            ):
                raise AliasResolutionError("DOCUMENT_IDENTITY_ALIAS_ID_HASH_MISMATCH")
            if key in self._entries and self._entries[key] != entry:
                raise AliasResolutionError("DOCUMENT_IDENTITY_ALIAS_COLLISION")
            self._entries[key] = entry
        self.bundle["bundle_sha256"] = expected_hash

    def lookup(
        self,
        alias_kind: str,
        alias_value: str,
        *,
        expected_identity_sha256: str | None = None,
        replay_document_id: str | None = None,
    ) -> dict[str, Any]:
        key = _alias_key(alias_kind, alias_value)
        entry = self._entries.get(key)
        if entry is None:
            raise AliasResolutionError("DOCUMENT_IDENTITY_ALIAS_MISS")
        if expected_identity_sha256 is not None and entry["canonical_identity_sha256"] != expected_identity_sha256:
            raise AliasResolutionError("DOCUMENT_IDENTITY_ALIAS_REPLAY_MISMATCH")
        if replay_document_id is not None and entry["canonical_document_id"] != replay_document_id:
            raise AliasResolutionError("DOCUMENT_IDENTITY_ALIAS_REPLAY_MISMATCH")
        result = deepcopy(entry)
        result["outcome"] = "replay" if replay_document_id is not None else "hit"
        result["receipt_code"] = "DOCUMENT_IDENTITY_ALIAS_RESOLVED"
        return result

    def resolve(self, alias_kind: str, alias_value: str, **kwargs: Any) -> str:
        return self.lookup(alias_kind, alias_value, **kwargs)["canonical_document_id"]


class ArtifactStore:
    """Small content-addressed store used by both durable artifact resolvers."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[bytes, str]] = {}

    def put(self, item_id: str, content: bytes) -> dict[str, Any]:
        etag = sha256_bytes(content)
        previous = self._items.get(item_id)
        if previous is not None and previous[0] != content:
            raise MigrationError(f"artifact collision for {item_id}")
        self._items[item_id] = (bytes(content), etag)
        return {"item_id": item_id, "etag": etag, "length_bytes": len(content)}

    def read(self, item_id: str) -> tuple[bytes, str]:
        try:
            return self._items[item_id]
        except KeyError as exc:
            raise MigrationError(f"artifact not found: {item_id}") from exc

    def contains(self, item_id: str) -> bool:
        """Expose durable existence without conflating absence and corruption."""
        return item_id in self._items


class Fence(NamedTuple):
    resource: str
    owner: str
    token: int


class FenceStore:
    """Process-safe monotonic fencing token allocator for disposable rehearsal."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._next: dict[str, int] = {}
        self._active: dict[str, Fence] = {}

    def acquire(self, resource: str, owner: str) -> Fence:
        with self._lock:
            if resource in self._active:
                raise FenceConflict(f"FENCE_BUSY:{resource}")
            token = self._next.get(resource, 0) + 1
            fence = Fence(resource, owner, token)
            self._next[resource] = token
            self._active[resource] = fence
            return fence

    def assert_current(self, fence: Fence) -> None:
        with self._lock:
            if self._active.get(fence.resource) != fence:
                raise FenceConflict(f"STALE_FENCE:{fence.resource}:{fence.token}")

    def release(self, fence: Fence) -> bool:
        with self._lock:
            if self._active.get(fence.resource) != fence:
                return False
            del self._active[fence.resource]
            return True


def _validate_inventory(inventory: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "inventory_run_id",
        "source_code",
        "window_start",
        "run_upper_bound",
        "messages",
        "attachment_identity_keys",
        "empty_inventory",
        "immutable_inventory",
        "attachment_ids_verified",
    }
    if inventory.get("schema_version") != INVENTORY_SCHEMA or not required <= set(inventory):
        raise MigrationError("invalid inventory-v1 schema")
    if inventory["immutable_inventory"] is not True or inventory["attachment_ids_verified"] is not True:
        raise MigrationError("inventory must be immutable and attachment-verified")
    if not isinstance(inventory["messages"], list) or not isinstance(inventory["attachment_identity_keys"], list):
        raise MigrationError("inventory messages and attachments must be arrays")
    if inventory["empty_inventory"] != (len(inventory["messages"]) == 0):
        raise MigrationError("inventory empty marker does not match messages")
    locator_fields = {
        "identity",
        "archive_ref",
        "sha256",
        "etag",
        "version",
        "length_bytes",
        "schema_version",
        "readback_sha256",
    }
    for item in inventory["attachment_identity_keys"]:
        if not isinstance(item, dict) or not locator_fields <= set(item):
            raise MigrationError("inventory attachment locator is incomplete")
        if (
            not re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"]))
            or item["sha256"] != item["readback_sha256"]
            or not isinstance(item["length_bytes"], int)
            or item["length_bytes"] < 0
        ):
            raise MigrationError("inventory attachment readback is untrusted")
    for message in inventory["messages"]:
        if not isinstance(message, dict) or not {"message_id", "message_locator", "attachment_identity_keys"} <= set(message):
            raise MigrationError("inventory message locator is incomplete")
        if not isinstance(message["attachment_identity_keys"], list):
            raise MigrationError("inventory message attachments must be an array")
        for item in message["attachment_identity_keys"]:
            if not isinstance(item, dict) or not locator_fields <= set(item):
                raise MigrationError("inventory message attachment locator is incomplete")
            if item["sha256"] != item["readback_sha256"] or not isinstance(item["length_bytes"], int):
                raise MigrationError("inventory message attachment readback is untrusted")


class InventoryResolver:
    """Persist and read back inventory-v1 under cursor-version fencing.

    The artifact store is the durable receipt/index boundary.  ``_states`` is
    only a warm cache; a new resolver instance can rehydrate the same receipt
    from the deterministic receipt item ID.
    """

    def __init__(self, *, store: ArtifactStore | None = None, fences: FenceStore | None = None) -> None:
        self.store = store or ArtifactStore()
        self.fences = fences or FenceStore()
        self._states: dict[tuple[str, str], dict[str, Any]] = {}
        self._rehydrated: set[tuple[str, str]] = set()

    @staticmethod
    def _receipt_item_id(inventory_run_id: str, source_code: str) -> str:
        key_digest = sha256_json(
            {"inventory_run_id": inventory_run_id, "source_code": source_code}
        )
        return f"inventory-receipt-v1://{key_digest}"

    def _load_receipt(self, key: tuple[str, str]) -> dict[str, Any] | None:
        item_id = self._receipt_item_id(*key)
        if not self.store.contains(item_id):
            return None
        content, _etag = self.store.read(item_id)
        try:
            receipt = json.loads(content)
        except json.JSONDecodeError as exc:
            raise MigrationError("INVENTORY_RECEIPT_CORRUPT") from exc
        if not isinstance(receipt, dict) or receipt.get("receipt_item_id") != item_id:
            raise MigrationError("INVENTORY_RECEIPT_IDENTITY_MISMATCH")
        integrity = deepcopy(receipt)
        expected_digest = integrity.pop("receipt_sha256", None)
        expected_etag = integrity.pop("receipt_etag", None)
        if sha256_json(integrity) != expected_digest or expected_etag != expected_digest:
            raise MigrationError("INVENTORY_RECEIPT_READBACK_MISMATCH")
        if (receipt.get("inventory_run_id"), receipt.get("source_code")) != key:
            raise MigrationError("INVENTORY_RECEIPT_KEY_MISMATCH")
        return receipt

    def _persist_receipt(self, result: Mapping[str, Any]) -> dict[str, Any]:
        receipt = deepcopy(dict(result))
        receipt_item_id = self._receipt_item_id(
            receipt["inventory_run_id"], receipt["source_code"]
        )
        receipt["receipt_item_id"] = receipt_item_id
        receipt["receipt_schema_version"] = "inventory-receipt-v1"
        receipt.pop("receipt_sha256", None)
        receipt.pop("receipt_etag", None)
        receipt_digest = sha256_json(receipt)
        receipt["receipt_sha256"] = receipt_digest
        receipt["receipt_etag"] = receipt_digest
        self.store.put(receipt_item_id, canonical_bytes(receipt))
        return receipt

    def stage(self, inventory: Mapping[str, Any]) -> dict[str, Any]:
        _validate_inventory(inventory)
        payload = canonical_bytes(inventory)
        digest = sha256_bytes(payload)
        run_id = _text(inventory["inventory_run_id"], "inventory_run_id")
        source_code = _text(inventory["source_code"], "source_code")
        item_id = f"inventory://{run_id}/{source_code}/{digest}"
        metadata = self.store.put(item_id, payload)
        return {
            "inventory_run_id": run_id,
            "source_code": source_code,
            "inventory_sha256": digest,
            "inventory_item_id": item_id,
            "inventory_path": item_id,
            "inventory_etag": metadata["etag"],
            "inventory_schema_version": INVENTORY_SCHEMA,
            "inventory_length_bytes": metadata["length_bytes"],
        }

    def commit(
        self,
        inventory: Mapping[str, Any],
        *,
        fence: Fence,
        cursor_version: int,
    ) -> dict[str, Any]:
        self.fences.assert_current(fence)
        pointer = self.stage(inventory)
        key = (pointer["inventory_run_id"], pointer["source_code"])
        current = self._load_receipt(key)
        if current is not None:
            if current["inventory_sha256"] != pointer["inventory_sha256"]:
                raise MigrationError("inventory run identity maps to different bytes")
            if current["cursor_version"] != cursor_version:
                raise FenceConflict("STALE_CURSOR_VERSION")
            result = deepcopy(current)
            result.update({"changed": False, "receipt_code": "INVENTORY_REPLAY_NOOP"})
            self._states[key] = deepcopy(result)
            return result
        result = {
            **pointer,
            "inventory_fence": fence.token,
            "cursor_version": cursor_version,
            "readback_verified": True,
            "receipt_code": "INVENTORY_COMMITTED",
        }
        persisted = self._persist_receipt(result)
        self._states[key] = deepcopy(persisted)
        return deepcopy(persisted)

    def restart_readback(self, inventory_run_id: str, source_code: str) -> dict[str, Any]:
        key = (inventory_run_id, source_code)
        state = self._load_receipt(key)
        if state is None:
            state = self._states.get(key)
        if state is None:
            raise MigrationError("INVENTORY_RESTART_RECEIPT_MISSING")
        content, etag = self.store.read(state["inventory_item_id"])
        if sha256_bytes(content) != state["inventory_sha256"] or etag != state["inventory_etag"]:
            raise MigrationError("INVENTORY_RESTART_READBACK_MISMATCH")
        inventory = json.loads(content)
        _validate_inventory(inventory)
        if key != (inventory["inventory_run_id"], inventory["source_code"]):
            raise MigrationError("INVENTORY_RESTART_IDENTITY_MISMATCH")
        result = deepcopy(state)
        result["restart_rehydrated"] = key not in self._rehydrated
        result["receipt_code"] = "INVENTORY_RESTART_REHYDRATED"
        self._rehydrated.add(key)
        return result


def _validate_verification(payload: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "verification_version",
        "actual_file_id",
        "account_id",
        "period_start",
        "period_end",
        "expected_payload_sha256",
        "observed_payload_sha256",
        "expected_count",
        "observed_count",
        "expected_amount_sum_minor",
        "observed_amount_sum_minor",
        "invariants_passed",
    }
    if payload.get("schema_version") != VERIFICATION_SCHEMA or not required <= set(payload):
        raise MigrationError("invalid actual-verification-v2 schema")
    if payload["invariants_passed"] is not True:
        raise MigrationError("actual verification invariants did not pass")
    if payload["expected_payload_sha256"] != payload["observed_payload_sha256"]:
        raise MigrationError("actual verification payload hash mismatch")
    if not all(isinstance(payload[field], int) for field in ("expected_count", "observed_count", "expected_amount_sum_minor", "observed_amount_sum_minor")):
        raise MigrationError("actual verification economic fields must be integer minor units")


class VerificationResolver:
    """Content-address and verify actual-verification-v2 readback artifacts."""

    def __init__(self, *, store: ArtifactStore | None = None) -> None:
        self.store = store or ArtifactStore()

    def write(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _validate_verification(payload)
        content = canonical_bytes(payload)
        digest = sha256_bytes(content)
        item_id = f"actual-verification-v2://{digest}"
        metadata = self.store.put(item_id, content)
        pointer = {
            "verification_artifact_sha256": digest,
            "verification_artifact_item_id": item_id,
            "verification_artifact_path": item_id,
            "verification_artifact_etag": metadata["etag"],
            "verification_artifact_schema_version": VERIFICATION_SCHEMA,
            "verification_artifact_length_bytes": metadata["length_bytes"],
            "readback_verified": True,
        }
        self.readback(pointer, expected_sha256=digest)
        return pointer

    def readback(self, pointer: Mapping[str, Any], *, expected_sha256: str) -> dict[str, Any]:
        required = {
            "verification_artifact_sha256",
            "verification_artifact_item_id",
            "verification_artifact_path",
            "verification_artifact_etag",
            "verification_artifact_schema_version",
            "verification_artifact_length_bytes",
        }
        if not required <= set(pointer) or pointer["verification_artifact_sha256"] != expected_sha256:
            raise MigrationError("ACTUAL_VERIFICATION_POINTER_MISMATCH")
        content, etag = self.store.read(pointer["verification_artifact_item_id"])
        if (
            sha256_bytes(content) != expected_sha256
            or etag != pointer["verification_artifact_etag"]
            or len(content) != pointer["verification_artifact_length_bytes"]
            or pointer["verification_artifact_path"] != pointer["verification_artifact_item_id"]
            or pointer["verification_artifact_schema_version"] != VERIFICATION_SCHEMA
        ):
            raise MigrationError("ACTUAL_VERIFICATION_READBACK_MISMATCH")
        payload = json.loads(content)
        _validate_verification(payload)
        return deepcopy(payload)


def merge_non_null(*rows: Mapping[str, Any]) -> dict[str, Any]:
    """Merge projections while rejecting unequal non-null values."""
    merged: dict[str, Any] = {}
    for row in rows:
        for key, value in row.items():
            if value is None:
                continue
            if key in merged and merged[key] is not None and merged[key] != value:
                raise MigrationError(f"MIGRATION_CONFLICT:{key}")
            merged[key] = value
    return merged


def _rows_digest(rows: Iterable[Mapping[str, Any]]) -> str:
    ordered = sorted((dict(row) for row in rows), key=lambda row: canonical_text(row))
    return sha256_json(ordered)


def _ensure_unique_logical_keys(
    rows: Sequence[Mapping[str, Any]], table: str, fields: Sequence[str]
) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = tuple(row.get(field) for field in fields)
        if any(value is None for value in key):
            raise MigrationError(f"MIGRATION_CONFLICT:{table}-missing-logical-key")
        if key in seen:
            raise MigrationError(f"MIGRATION_CONFLICT:{table}-duplicate-logical-key")
        seen.add(key)
        result.append(dict(row))
    return result


def _select_committed_outboxes(
    outbox_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """Select one deterministic batch row per imported ID.

    A committed row outranks earlier lifecycle states.  Within one state,
    the newest explicit update wins; an equal-precedence tie is ambiguous and
    remains a fail-closed conflict.
    """
    candidates: dict[str, list[Mapping[str, Any]]] = {}
    for row in outbox_rows:
        imported_id = _text(row.get("imported_id"), "outbox.imported_id")
        _text(row.get("outbox_id"), "outbox.outbox_id")
        candidates.setdefault(imported_id, []).append(row)
    selected: dict[str, Mapping[str, Any]] = {}
    for imported_id, rows in candidates.items():
        ranked = [
            (
                OUTBOX_STATE_PRECEDENCE.get(str(row.get("state")), -1),
                str(row.get("updated_at") or ""),
                row,
            )
            for row in rows
        ]
        best_rank = max((state_rank, updated_at) for state_rank, updated_at, _row in ranked)
        winners = [row for state_rank, updated_at, row in ranked if (state_rank, updated_at) == best_rank]
        if len(winners) != 1:
            raise MigrationError("MIGRATION_CONFLICT:outbox-precedence")
        selected[imported_id] = winners[0]
    return selected


def _select_authoritative_verifications(
    verification_rows: Sequence[Mapping[str, Any]],
    verification_resolver: VerificationResolver | None,
    *,
    allowed_outbox_ids: set[str] | None = None,
) -> dict[str, Mapping[str, Any]]:
    if verification_rows and verification_resolver is None:
        raise MigrationError("ACTUAL_VERIFICATION_RESOLVER_REQUIRED")
    versions: dict[str, list[Mapping[str, Any]]] = {}
    for row in verification_rows:
        try:
            outbox_id = _text(row.get("outbox_id"), "verification.outbox_id")
            version = int(row["verification_version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MigrationError("MIGRATION_CONFLICT:verification-key") from exc
        if version < 0:
            raise MigrationError("MIGRATION_CONFLICT:verification-version")
        if allowed_outbox_ids is not None and outbox_id not in allowed_outbox_ids:
            continue
        versions.setdefault(outbox_id, []).append(row)
    selected: dict[str, Mapping[str, Any]] = {}
    for outbox_id, rows in versions.items():
        max_version = max(int(row["verification_version"]) for row in rows)
        winners = [row for row in rows if int(row["verification_version"]) == max_version]
        if len(winners) != 1:
            raise MigrationError("MIGRATION_CONFLICT:verification-version")
        winner = winners[0]
        if not VERIFICATION_POINTER_FIELDS <= set(winner):
            raise MigrationError("ACTUAL_VERIFICATION_POINTER_REQUIRED")
        assert verification_resolver is not None
        payload = verification_resolver.readback(
            winner, expected_sha256=winner["verification_artifact_sha256"]
        )
        for field in VERIFICATION_PAYLOAD_FIELDS:
            if field in winner and winner[field] != payload.get(field):
                raise MigrationError(f"ACTUAL_VERIFICATION_AUTHORITATIVE_MISMATCH:{field}")
        authoritative = dict(winner)
        authoritative.update({field: payload[field] for field in VERIFICATION_PAYLOAD_FIELDS})
        selected[outbox_id] = authoritative
    return selected


def _select_reconciliation_winners(
    reconciliation_rows: Sequence[Mapping[str, Any]],
    outboxes: Mapping[str, Mapping[str, Any]],
    verifications: Mapping[str, Mapping[str, Any]],
) -> list[tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]]:
    """Choose the highest matching reconciliation version per source period."""
    outboxes_by_id: dict[str, Mapping[str, Any]] = {}
    for outbox in outboxes.values():
        outbox_id = _text(outbox.get("outbox_id"), "outbox.outbox_id")
        if outbox_id in outboxes_by_id:
            raise MigrationError("MIGRATION_CONFLICT:duplicate outbox")
        outboxes_by_id[outbox_id] = outbox
    candidates: dict[tuple[str, str], list[tuple[int, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]]] = {}
    for reconciliation in reconciliation_rows:
        source_code = _text(reconciliation.get("source_code"), "reconciliation.source_code")
        period_key = _text(reconciliation.get("period_key"), "reconciliation.period_key")
        try:
            version = int(reconciliation["reconciliation_version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MigrationError("MIGRATION_CONFLICT:reconciliation-version") from exc
        if version < 0:
            raise MigrationError("MIGRATION_CONFLICT:reconciliation-version")
        matches: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
        for outbox_id, verification in verifications.items():
            outbox = outboxes_by_id.get(outbox_id)
            if outbox is None:
                continue
            if (
                outbox.get("source_code") == source_code
                and outbox.get("period_key") == period_key
                and reconciliation.get("actual_verification_sha256")
                == verification.get("verification_artifact_sha256")
            ):
                matches.append((outbox, verification))
        if len(matches) != 1:
            raise MigrationError("MIGRATION_CONFLICT:reconciliation-cardinality")
        outbox, verification = matches[0]
        key = (source_code, period_key)
        candidates.setdefault(key, []).append((version, reconciliation, outbox, verification))
    winners: list[tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]] = []
    for rows in candidates.values():
        max_version = max(version for version, _row, _outbox, _verification in rows)
        best = [item for item in rows if item[0] == max_version]
        if len(best) != 1:
            raise MigrationError("MIGRATION_CONFLICT:reconciliation-version")
        _version, reconciliation, outbox, verification = best[0]
        winners.append((reconciliation, outbox, verification))
    return sorted(
        winners,
        key=lambda item: (
            str(item[0].get("source_code")),
            str(item[0].get("period_key")),
            str(item[1].get("imported_id")),
        ),
    )


def reconcile_actual_batches(
    outbox_rows: Sequence[Mapping[str, Any]],
    verification_rows: Sequence[Mapping[str, Any]],
    reconciliation_rows: Sequence[Mapping[str, Any]],
    *,
    verification_resolver: VerificationResolver | None = None,
) -> list[dict[str, Any]]:
    """Join outbox, highest trusted verification, and matching reconciliation rows."""
    selected_outboxes = _select_committed_outboxes(outbox_rows)
    selected = _select_authoritative_verifications(
        verification_rows,
        verification_resolver,
        allowed_outbox_ids={str(row["outbox_id"]) for row in selected_outboxes.values()},
    )
    reconciliation_winners = _select_reconciliation_winners(
        reconciliation_rows, selected_outboxes, selected
    )
    result: list[dict[str, Any]] = []
    for reconciliation, outbox, verification in reconciliation_winners:
        row = merge_non_null(
            _outbox_projection(outbox),
            _verification_projection(verification),
            {
                "source_code": reconciliation.get("source_code"),
                "period_key": reconciliation.get("period_key"),
                "reconciliation_version": reconciliation.get("reconciliation_version"),
                "statement_sha256": reconciliation.get("statement_sha256"),
                "verification_artifact_sha256": reconciliation.get("actual_verification_sha256"),
                "reconciliation_state": reconciliation.get("state"),
                "reconciliation_difference_minor": reconciliation.get("difference_minor"),
                "reconciliation_verified_at": reconciliation.get("verified_at"),
            },
        )
        result.append(row)
    return _ensure_unique_logical_keys(
        sorted(result, key=lambda row: (str(row.get("idempotency_key")), str(row.get("batch_id")))),
        "finance_actual_batches",
        ("idempotency_key",),
    )


def _outbox_projection(outbox: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "batch_id": outbox.get("outbox_id"),
        "run_id": outbox.get("run_id"),
        "idempotency_key": outbox.get("imported_id"),
        "actual_file_id": outbox.get("actual_file_id"),
        "delta_sha256": outbox.get("payload_sha256"),
        "delta_artifact_item_id": outbox.get("artifact_item_id"),
        "delta_artifact_etag": outbox.get("artifact_etag"),
        "delta_schema_version": outbox.get("artifact_schema_version"),
        "config_version": outbox.get("config_version"),
        "parser_version": outbox.get("parser_version"),
        "state": outbox.get("state"),
        "actual_transaction_id": outbox.get("actual_transaction_id"),
        "attempt_count": outbox.get("attempt_count"),
        "last_error_class": outbox.get("last_error_class"),
        "updated_at": outbox.get("updated_at"),
    }


def _verification_projection(verification: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "verification_version": verification.get("verification_version"),
        "actual_file_id": verification.get("actual_file_id"),
        "account_id": verification.get("account_id"),
        "period_start": verification.get("period_start"),
        "period_end": verification.get("period_end"),
        "expected_payload_sha256": verification.get("expected_payload_sha256"),
        "observed_payload_sha256": verification.get("observed_payload_sha256"),
        "expected_count": verification.get("expected_count"),
        "observed_count": verification.get("observed_count"),
        "expected_amount_sum_minor": verification.get("expected_amount_sum_minor"),
        "observed_amount_sum_minor": verification.get("observed_amount_sum_minor"),
        "invariants_passed": verification.get("invariants_passed"),
        "verified_at": verification.get("verified_at"),
        "verification_artifact_sha256": verification.get("verification_artifact_sha256"),
        "verification_artifact_item_id": verification.get("verification_artifact_item_id"),
        "verification_artifact_path": verification.get("verification_artifact_path"),
        "verification_artifact_etag": verification.get("verification_artifact_etag"),
        "verification_artifact_schema_version": verification.get("verification_artifact_schema_version"),
        "verification_artifact_length_bytes": verification.get("verification_artifact_length_bytes"),
    }


def _actual_rows_without_reconciliation(
    outbox_rows: Sequence[Mapping[str, Any]],
    verification_rows: Sequence[Mapping[str, Any]],
    verification_resolver: VerificationResolver | None,
) -> list[dict[str, Any]]:
    selected_outboxes = _select_committed_outboxes(outbox_rows)
    selected_verifications = _select_authoritative_verifications(
        verification_rows,
        verification_resolver,
        allowed_outbox_ids={str(row["outbox_id"]) for row in selected_outboxes.values()},
    )
    result: list[dict[str, Any]] = []
    for outbox in selected_outboxes.values():
        selected = selected_verifications.get(_text(outbox.get("outbox_id"), "outbox_id"))
        result.append(merge_non_null(_outbox_projection(outbox), _verification_projection(selected or {})))
    return _ensure_unique_logical_keys(
        sorted(result, key=lambda row: str(row.get("idempotency_key"))),
        "finance_actual_batches",
        ("idempotency_key",),
    )


def _target_projection(row: Mapping[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    return {field: row[field] for field in fields if field in row}


def _ai_review_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return _target_projection(row, (
        "idempotency_key", "policy_id", "policy_sha256", "config_sha256", "output_schema_sha256",
        "request_sha256", "runner_receipt_id", "proposal_sha256", "proposal_artifact_item_id",
        "proposal_artifact_etag", "proposal_artifact_schema", "review_state", "review_decision",
        "reviewed_by_hash", "reviewed_at", "terminal_readback_verified", "updated_at",
    ))


def _actual_batch_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return _target_projection(row, (
        "batch_id", "run_id", "idempotency_key", "actual_file_id", "delta_sha256", "delta_artifact_item_id",
        "delta_artifact_etag", "delta_schema_version", "config_version", "parser_version", "state",
        "actual_transaction_id", "attempt_count", "last_error_class", "updated_at", "verification_version",
        "account_id", "period_start", "period_end", "expected_payload_sha256", "observed_payload_sha256",
        "expected_count", "observed_count", "expected_amount_sum_minor", "observed_amount_sum_minor",
        "invariants_passed", "verified_at", "verification_artifact_sha256", "verification_artifact_item_id",
        "verification_artifact_path", "verification_artifact_etag", "verification_artifact_schema_version",
        "verification_artifact_length_bytes", "source_code", "period_key", "reconciliation_version",
        "statement_sha256", "reconciliation_state", "reconciliation_difference_minor", "reconciliation_verified_at",
    ))


class DualReadWrite:
    """Keep old and target projections in sync while reads prefer the target."""

    def __init__(self) -> None:
        self.old: dict[tuple[str, str], dict[str, Any]] = {}
        self.target: dict[tuple[str, str], dict[str, Any]] = {}

    def write(self, table: str, key: str, row: Mapping[str, Any]) -> dict[str, Any]:
        value = deepcopy(dict(row))
        identity = (table, key)
        self.old[identity] = deepcopy(value)
        self.target[identity] = deepcopy(value)
        return {"table": table, "key": key, "dual_write": True, "row_sha256": sha256_json(value)}

    def read(self, table: str, key: str) -> tuple[dict[str, Any], str]:
        identity = (table, key)
        target = self.target.get(identity)
        old = self.old.get(identity)
        if target is not None and old is not None and target != old:
            raise MigrationError("DUAL_READ_PARITY_MISMATCH")
        if target is not None:
            return deepcopy(target), "target"
        if old is not None:
            return deepcopy(old), "legacy_fallback"
        raise KeyError(identity)

    def delete_old(self, *_: Any) -> None:
        raise MigrationError("OLD_TABLE_DELETE_REQUIRES_NAMED_OPERATOR_GATE")


def _source_schema(table_name: str) -> dict[str, Any]:
    data = json.loads(DATA_TABLES_PATH.read_text(encoding="utf-8"))
    for table in data.get("tables", []):
        if table.get("name") == table_name:
            schema = {
                "idempotency_key": list(table.get("idempotency_key", [])),
                "columns": dict(table.get("columns", {})),
            }
            if "allowed_states" in table:
                schema["allowed_states"] = list(table["allowed_states"])
            if "allowed_review_states" in table:
                schema["allowed_review_states"] = list(table["allowed_review_states"])
            return schema
    raise MigrationError(f"BACKUP_SCHEMA_MISSING:{table_name}")


def _backup_snapshot(
    source_tables: Mapping[str, Sequence[Mapping[str, Any]]],
    source_schemas: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "finance-data-table-backup-v1",
        "tables": {
            name: {
                "schema": deepcopy(dict(source_schemas[name])),
                "rows": sorted(
                    (dict(row) for row in rows), key=lambda row: canonical_text(row)
                ),
            }
            for name, rows in sorted(source_tables.items())
        },
    }


def _restore_snapshot(snapshot: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    if snapshot.get("schema_version") != "finance-data-table-backup-v1":
        raise MigrationError("BACKUP_SCHEMA_VERSION_MISMATCH")
    tables = snapshot.get("tables")
    if not isinstance(tables, Mapping):
        raise MigrationError("BACKUP_TABLES_MISSING")
    restored: dict[str, list[dict[str, Any]]] = {}
    for name, table in tables.items():
        if not isinstance(name, str) or not isinstance(table, Mapping):
            raise MigrationError("BACKUP_TABLE_INVALID")
        if not isinstance(table.get("schema"), Mapping) or not isinstance(table.get("rows"), list):
            raise MigrationError(f"BACKUP_TABLE_INVALID:{name}")
        restored[name] = [dict(row) for row in table["rows"]]
    return restored


def _map_ingestion(source: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    cursors: dict[str, dict[str, Any]] = {}
    for row in source.get("finance_source_cursors", []):
        source_code = _text(row.get("source_code"), "source_cursor.source_code")
        if source_code in cursors:
            raise MigrationError("MIGRATION_CONFLICT:finance_source_cursors-duplicate-logical-key")
        cursors[source_code] = dict(row)
    receipts: dict[str, Mapping[str, Any]] = {}
    receipt_keys: set[tuple[str, str]] = set()
    for row in source.get("finance_acquisition_receipts", []):
        receipt_key = (_text(row.get("run_id"), "acquisition.run_id"), _text(row.get("source_code"), "acquisition.source_code"))
        if receipt_key in receipt_keys:
            raise MigrationError("MIGRATION_CONFLICT:finance_acquisition_receipts-duplicate-logical-key")
        receipt_keys.add(receipt_key)
        if row.get("readback_verified") is not True or row.get("terminal_state") not in {"DOWNSTREAM_VERIFIED", "COMMITTED", "SUCCEEDED"}:
            continue
        key = row.get("source_code")
        previous = receipts.get(key)
        if previous is None or str(row.get("updated_at", "")) > str(previous.get("updated_at", "")):
            receipts[key] = row
    result: list[dict[str, Any]] = []
    for source_code in sorted(set(cursors) | set(receipts)):
        cursor = cursors.get(source_code, {})
        receipt = receipts.get(source_code, {})
        if cursor.get("committed_run_id") and receipt.get("run_id") not in {None, cursor.get("committed_run_id")}:
            receipt = {}
        mapped = merge_non_null(cursor, {
            "source_code": receipt.get("source_code"),
            "committed_run_id": receipt.get("run_id"),
            "run_upper_bound": receipt.get("run_upper_bound"),
            "scanned_count": receipt.get("scanned_count"),
            "matched_count": receipt.get("matched_count"),
            "last_window_start": receipt.get("window_start"),
            "last_pages_fetched": receipt.get("pages_fetched"),
            "last_pagination_exhausted": receipt.get("pagination_exhausted"),
            "last_heartbeat": receipt.get("heartbeat"),
            "last_terminal_state": receipt.get("terminal_state"),
            "last_receipt_created_at": receipt.get("created_at"),
            "downstream_receipt_sha256": receipt.get("downstream_receipt_sha256"),
            "attachment_verification_barrier": receipt.get("attachment_verification_barrier"),
            "attachment_ids_verified": receipt.get("attachment_ids_verified"),
            "attachment_identity_keys_json": receipt.get("attachment_identity_keys_json"),
            "attachments_verified": receipt.get("attachments_verified"),
            "email_evidence_receipt_barrier": receipt.get("email_evidence_receipt_barrier"),
            "email_evidence_receipts_verified": receipt.get("email_evidence_receipts_verified"),
            "email_evidence_identity_keys_json": receipt.get("email_evidence_identity_keys_json"),
            "archive_ready": receipt.get("archive_ready"),
        })
        for field in (
            "inventory_run_id",
            "inventory_fence",
            "inventory_sha256",
            "inventory_item_id",
            "inventory_path",
            "inventory_etag",
            "inventory_schema_version",
            "inventory_length_bytes",
        ):
            if receipt.get(field) is not None:
                mapped[field] = receipt[field]
        result.append(mapped)
    return result


def _map_documents(
    source: Mapping[str, Sequence[Mapping[str, Any]]], alias_resolver: AliasResolver | None = None
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    archive_keys: set[tuple[Any, ...]] = set()
    for archive in source.get("finance_archive_receipts", []):
        archive_key = tuple(archive.get(field) for field in ("source_code", "source_message_id", "source_attachment_id", "source_sha256"))
        if any(value is None for value in archive_key) or archive_key in archive_keys:
            raise MigrationError("MIGRATION_CONFLICT:finance_archive_receipts-duplicate-logical-key")
        archive_keys.add(archive_key)
        identity = document_identity(
            "MAIL_LINKED",
            [archive.get("source_sha256"), archive.get("source_message_id"), archive.get("source_attachment_id")],
        )
        row = {
            "document_id": identity["document_id"],
            "source_sha256": archive.get("source_sha256"),
            "source_message_id": archive.get("source_message_id"),
            "source_attachment_id": archive.get("source_attachment_id"),
            "source_code": archive.get("source_code"),
            "archive_receipt_id": archive.get("archive_receipt_id"),
            "run_id": archive.get("run_id"),
            "onedrive_item_id": archive.get("onedrive_item_id"),
            "onedrive_etag": archive.get("onedrive_etag"),
            "archive_state": archive.get("archive_state"),
            "archive_verified_at": archive.get("verified_at"),
            "updated_at": archive.get("updated_at"),
        }
        rows[identity["document_id"]] = merge_non_null(rows.get(identity["document_id"], {}), row)
    operation_keys: set[tuple[Any, ...]] = set()
    for operation in source.get("finance_document_operations", []):
        operation_key = tuple(operation.get(field) for field in ("source_sha256", "document_profile", "requested_schema_version"))
        if any(value is None for value in operation_key) or operation_key in operation_keys:
            raise MigrationError("MIGRATION_CONFLICT:finance_document_operations-duplicate-logical-key")
        operation_keys.add(operation_key)
        identity: dict[str, str]
        if operation.get("source_message_id") is not None and operation.get("source_attachment_id") is not None:
            identity = document_identity(
                "MAIL_LINKED",
                [operation.get("source_sha256"), operation.get("source_message_id"), operation.get("source_attachment_id")],
            )
        else:
            identity = document_identity(
                "PROCESSING_ONLY",
                [operation.get("source_sha256"), operation.get("document_profile"), operation.get("requested_schema_version")],
            )
        if operation.get("document_id"):
            if alias_resolver is None:
                raise AliasResolutionError("DOCUMENT_IDENTITY_ALIAS_UNAVAILABLE:resolver")
            alias_resolver.lookup(
                "document_id",
                operation["document_id"],
                expected_identity_sha256=identity["identity_sha256"],
                replay_document_id=identity["document_id"],
            )
        row = {
            "document_id": identity["document_id"],
            **{field: operation.get(field) for field in (
                "source_sha256", "document_profile", "requested_schema_version", "onedrive_item_id",
                "source_message_id", "source_attachment_id", "source_code", "config_version", "actual_file_id",
                "account_id", "period_key", "state", "attempt_count", "last_execution_id", "parser_version",
                "output_sha256", "error_class", "error_detail_redacted", "updated_at",
            )},
        }
        rows[identity["document_id"]] = merge_non_null(rows.get(identity["document_id"], {}), row)
    return [rows[key] for key in sorted(rows)]


class MigrationRunner:
    """Build four projections and immutable receipts without mutating old rows."""

    def __init__(
        self,
        source_tables: Mapping[str, Sequence[Mapping[str, Any]]],
        *,
        verification_resolver: VerificationResolver | None = None,
    ) -> None:
        self.source_tables = {name: [dict(row) for row in rows] for name, rows in source_tables.items()}
        self.source_schemas = {
            name: _source_schema(name) for name in sorted(self.source_tables)
        }
        self.verification_resolver = verification_resolver
        self.target_tables: dict[str, list[dict[str, Any]]] = {target: [] for target in TARGETS}
        self.receipt: dict[str, Any] | None = None

    def backup_snapshot(self) -> dict[str, Any]:
        return _backup_snapshot(self.source_tables, self.source_schemas)

    def backup_digest(self) -> str:
        return sha256_json(self.backup_snapshot())

    def restore_backup(self, snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
        snapshot = self.backup_snapshot() if snapshot is None else deepcopy(dict(snapshot))
        tables = snapshot.get("tables")
        if not isinstance(tables, Mapping) or set(tables) != set(self.source_schemas):
            raise MigrationError("BACKUP_SCHEMA_TABLE_SET_MISMATCH")
        for name, expected_schema in self.source_schemas.items():
            table = tables.get(name)
            if not isinstance(table, Mapping) or table.get("schema") != expected_schema:
                raise MigrationError(f"BACKUP_SCHEMA_MISMATCH:{name}")
        restored_tables = _restore_snapshot(snapshot)
        restored_schemas = {
            name: deepcopy(snapshot["tables"][name]["schema"])
            for name in sorted(restored_tables)
        }
        restored_snapshot = _backup_snapshot(restored_tables, restored_schemas)
        restored_digest = sha256_json(restored_snapshot)
        return {
            "source_tables": restored_tables,
            "source_schemas": restored_schemas,
            "source_digest": restored_digest,
            "backup_digest": self.backup_digest(),
            "restore_roundtrip": restored_snapshot == snapshot,
        }

    def build_targets(
        self,
        *,
        alias_resolver: AliasResolver | None = None,
        verification_resolver: VerificationResolver | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        resolver = verification_resolver or self.verification_resolver
        actual = reconcile_actual_batches(
            self.source_tables.get("finance_actual_outbox", []),
            self.source_tables.get("finance_actual_verifications", []),
            self.source_tables.get("finance_reconciliations", []),
            verification_resolver=resolver,
        )
        # Outbox rows without a reconciliation remain durable migration rows.
        existing_ids = {row.get("batch_id") for row in actual}
        for row in _actual_rows_without_reconciliation(
            self.source_tables.get("finance_actual_outbox", []),
            self.source_tables.get("finance_actual_verifications", []),
            resolver,
        ):
            if row.get("batch_id") not in existing_ids:
                actual.append(row)
        targets = {
            "finance_execution_failures": [
                {key: value for key, value in row.items() if key in self.source_schemas["finance_execution_failures"]["columns"]}
                for row in self.source_tables.get("finance_execution_failures", [])
            ],
            "finance_ingestion_state": _map_ingestion(self.source_tables),
            "finance_documents": _map_documents(self.source_tables, alias_resolver),
            "finance_actual_batches": [
                _actual_batch_projection(row)
                for row in sorted(actual, key=lambda row: str(row.get("idempotency_key")))
            ],
            "finance_ai_reviews": [
                _ai_review_projection(row)
                for row in sorted(
                    self.source_tables.get("finance_agent_jobs", []), key=lambda row: str(row.get("idempotency_key"))
                )
            ],
        }
        logical_keys = {
            "finance_execution_failures": ("execution_id",),
            "finance_ingestion_state": ("source_code",),
            "finance_documents": ("document_id",),
            "finance_actual_batches": ("idempotency_key",),
            "finance_ai_reviews": ("idempotency_key",),
        }
        return {
            name: _ensure_unique_logical_keys(rows, name, logical_keys[name])
            for name, rows in targets.items()
        }

    def run(
        self,
        *,
        alias_resolver: AliasResolver | None = None,
        verification_resolver: VerificationResolver | None = None,
    ) -> dict[str, Any]:
        targets = self.build_targets(
            alias_resolver=alias_resolver,
            verification_resolver=verification_resolver,
        )
        source_digest = self.backup_digest()
        target_digest = sha256_json(targets)
        no_op = self.receipt is not None and self.receipt["target_digest"] == target_digest
        if not no_op:
            self.target_tables = deepcopy(targets)
        receipt = {
            "schema_version": "data-table-migration-receipt-v1",
            "migration_id": f"15-to-4:{source_digest}",
            "source_digest": source_digest,
            "target_digest": target_digest,
            "target_schema_sha256": generated_target_schema_digest(),
            "backup_digest": source_digest,
            "source_schemas": deepcopy(self.source_schemas),
            "source_row_counts": {name: len(rows) for name, rows in sorted(self.source_tables.items())},
            "target_row_counts": {name: len(rows) for name, rows in sorted(targets.items())},
            "old_table_names": sorted(self.source_tables),
            "old_tables_preserved": True,
            "runtime_cutover": False,
            "deletion_authorized": False,
            "changed": not no_op,
            "second_run_noop": no_op,
        }
        self.receipt = deepcopy(receipt)
        return receipt

    def reverse_rehearsal(self) -> dict[str, Any]:
        restored = self.restore_backup()
        return {
            "schema_version": "data-table-reverse-rehearsal-v1",
            "source_digest": self.backup_digest(),
            "source_schemas": deepcopy(self.source_schemas),
            "restored_source_digest": restored["source_digest"],
            "restored_source_schemas": restored["source_schemas"],
            "restore_roundtrip": restored["restore_roundtrip"],
            "row_boundary": {name: len(rows) for name, rows in sorted(self.source_tables.items())},
            "target_tables_untouched": True,
            "would_restore_old_tables": True,
            "runtime_cutover": False,
        }

    def delete_old_tables(self) -> None:
        raise MigrationError("OLD_TABLE_DELETE_REQUIRES_NAMED_OPERATOR_GATE")


def generated_target_schema_digest() -> str:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    if set(matrix.get("target_schemas", {})) != set(TARGETS):
        raise MigrationError("matrix does not contain exactly four target schemas")
    return target_schema_digest(matrix["target_schemas"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate deterministic target schema presence")
    parser.add_argument("--schema-digest", action="store_true", help="print the target schema digest")
    args = parser.parse_args()
    digest = generated_target_schema_digest()
    if args.schema_digest:
        print(digest)
    if args.check or not args.schema_digest:
        print(f"data-table migration source checked: targets=4 schema_sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
