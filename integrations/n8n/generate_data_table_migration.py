"""Deterministic, source-only 15-to-4 Data Table migration primitives.

The module deliberately uses an in-memory artifact store so the migration can
be exercised without n8n, Postgres, OneDrive, or an Actual account.  It never
deletes an old table and it has no cutover operation.  Runtime owners can use
the returned receipts to drive a separately authorized disposable rehearsal.
"""

from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import struct
from threading import Lock
from typing import Any, Iterable, Mapping, NamedTuple, Sequence


ROOT = Path(__file__).resolve().parents[2]
N8N = ROOT / "integrations" / "n8n"
MATRIX_PATH = N8N / "data-table-migration-matrix.json"
ALIAS_PATH = N8N / "generated" / "document-identity-aliases-v1.json"
TARGETS = (
    "finance_ingestion_state",
    "finance_documents",
    "finance_actual_batches",
    "finance_ai_reviews",
)
INVENTORY_SCHEMA = "inventory-v1"
VERIFICATION_SCHEMA = "actual-verification-v2"
DOCUMENT_IDENTITY_VERSION = "document-identity-v1"
IDENTITY_KINDS = {"MAIL_LINKED", "BROWSER_CAPTURE", "PROCESSING_ONLY"}


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


def document_identity(
    identity_kind: str,
    components: Sequence[Any],
    *,
    version: str = DOCUMENT_IDENTITY_VERSION,
) -> dict[str, str]:
    """Return the canonical document ID and its raw tuple hash."""
    encoded = encode_document_identity(identity_kind, components, version=version)
    digest = hashlib.sha256(encoded).digest()
    encoded_digest = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return {
        "document_id": f"{version}_{encoded_digest}",
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
            "bundle_version": _text(raw.get("bundle_version", bundle_version), "bundle_version"),
            "source_commit": _text(raw.get("source_commit", source_commit), "source_commit"),
        }
        if entry["identity_kind"] not in IDENTITY_KINDS:
            raise AliasResolutionError("alias identity_kind is unsupported")
        if not re.fullmatch(r"[0-9a-f]{64}", entry["canonical_identity_sha256"]):
            raise AliasResolutionError("alias canonical identity hash is malformed")
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
            ):
                raise AliasResolutionError("DOCUMENT_IDENTITY_ALIAS_UNAVAILABLE:entry-integrity")
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
    """Persist and read back inventory-v1 under cursor-version fencing."""

    def __init__(self, *, store: ArtifactStore | None = None, fences: FenceStore | None = None) -> None:
        self.store = store or ArtifactStore()
        self.fences = fences or FenceStore()
        self._states: dict[tuple[str, str], dict[str, Any]] = {}
        self._rehydrated: set[tuple[str, str]] = set()

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
        current = self._states.get(key)
        if current is not None:
            if current["inventory_sha256"] != pointer["inventory_sha256"]:
                raise MigrationError("inventory run identity maps to different bytes")
            if current["cursor_version"] != cursor_version:
                raise FenceConflict("STALE_CURSOR_VERSION")
            result = deepcopy(current)
            result.update({"changed": False, "receipt_code": "INVENTORY_REPLAY_NOOP"})
            return result
        result = {
            **pointer,
            "inventory_fence": fence.token,
            "cursor_version": cursor_version,
            "readback_verified": True,
            "receipt_code": "INVENTORY_COMMITTED",
        }
        self._states[key] = deepcopy(result)
        return deepcopy(result)

    def restart_readback(self, inventory_run_id: str, source_code: str) -> dict[str, Any]:
        key = (inventory_run_id, source_code)
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


def reconcile_actual_batches(
    outbox_rows: Sequence[Mapping[str, Any]],
    verification_rows: Sequence[Mapping[str, Any]],
    reconciliation_rows: Sequence[Mapping[str, Any]],
    *,
    verification_resolver: VerificationResolver | None = None,
) -> list[dict[str, Any]]:
    """Join outbox, highest trusted verification, and matching reconciliation rows."""
    outbox_by_id: dict[str, Mapping[str, Any]] = {}
    for row in outbox_rows:
        key = _text(row.get("outbox_id"), "outbox_id")
        if key in outbox_by_id and outbox_by_id[key] != row:
            raise MigrationError("MIGRATION_CONFLICT:duplicate outbox")
        outbox_by_id[key] = row
    versions: dict[str, list[Mapping[str, Any]]] = {}
    for row in verification_rows:
        versions.setdefault(_text(row.get("outbox_id"), "verification.outbox_id"), []).append(row)
    selected: dict[str, Mapping[str, Any]] = {}
    for outbox_id, rows in versions.items():
        max_version = max(int(row["verification_version"]) for row in rows)
        winners = [row for row in rows if int(row["verification_version"]) == max_version]
        if len(winners) != 1:
            raise MigrationError("MIGRATION_CONFLICT:verification-version")
        selected[outbox_id] = winners[0]
        if verification_resolver is not None and winners[0].get("verification_artifact_sha256"):
            verification_resolver.readback(
                winners[0], expected_sha256=winners[0]["verification_artifact_sha256"]
            )
    result: list[dict[str, Any]] = []
    for reconciliation in reconciliation_rows:
        matches: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
        for outbox_id, verification in selected.items():
            outbox = outbox_by_id.get(outbox_id)
            if outbox is None:
                continue
            if (
                outbox.get("source_code") == reconciliation.get("source_code")
                and outbox.get("period_key") == reconciliation.get("period_key")
                and reconciliation.get("actual_verification_sha256")
                == verification.get("observed_payload_sha256")
            ):
                matches.append((outbox, verification))
        if len(matches) != 1:
            raise MigrationError("MIGRATION_CONFLICT:reconciliation-cardinality")
        outbox, verification = matches[0]
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
    return sorted(result, key=lambda row: (str(row.get("idempotency_key")), str(row.get("batch_id"))))


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
) -> list[dict[str, Any]]:
    versions: dict[str, list[Mapping[str, Any]]] = {}
    for verification in verification_rows:
        versions.setdefault(_text(verification.get("outbox_id"), "verification.outbox_id"), []).append(verification)
    result: list[dict[str, Any]] = []
    for outbox in outbox_rows:
        candidates = versions.get(_text(outbox.get("outbox_id"), "outbox_id"), [])
        selected = None
        if candidates:
            max_version = max(int(row["verification_version"]) for row in candidates)
            winners = [row for row in candidates if int(row["verification_version"]) == max_version]
            if len(winners) != 1:
                raise MigrationError("MIGRATION_CONFLICT:verification-version")
            selected = winners[0]
        result.append(merge_non_null(_outbox_projection(outbox), _verification_projection(selected or {})))
    return sorted(result, key=lambda row: str(row.get("idempotency_key")))


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


def _map_ingestion(source: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    cursors = {row.get("source_code"): dict(row) for row in source.get("finance_source_cursors", [])}
    receipts: dict[str, Mapping[str, Any]] = {}
    for row in source.get("finance_acquisition_receipts", []):
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
    for archive in source.get("finance_archive_receipts", []):
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
    for operation in source.get("finance_document_operations", []):
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
        if operation.get("document_id") and alias_resolver is not None:
            resolved = alias_resolver.resolve("document_id", operation["document_id"])
            if resolved != identity["document_id"] and operation.get("source_message_id") is not None:
                raise AliasResolutionError("DOCUMENT_IDENTITY_ALIAS_REPLAY_MISMATCH")
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

    def __init__(self, source_tables: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
        self.source_tables = {name: [dict(row) for row in rows] for name, rows in source_tables.items()}
        self.target_tables: dict[str, list[dict[str, Any]]] = {target: [] for target in TARGETS}
        self.receipt: dict[str, Any] | None = None

    def backup_digest(self) -> str:
        return sha256_json(
            {name: sorted((dict(row) for row in rows), key=lambda row: canonical_text(row)) for name, rows in sorted(self.source_tables.items())}
        )

    def build_targets(self, *, alias_resolver: AliasResolver | None = None) -> dict[str, list[dict[str, Any]]]:
        actual = reconcile_actual_batches(
            self.source_tables.get("finance_actual_outbox", []),
            self.source_tables.get("finance_actual_verifications", []),
            self.source_tables.get("finance_reconciliations", []),
        )
        # Outbox rows without a reconciliation remain durable migration rows.
        existing_ids = {row.get("batch_id") for row in actual}
        for row in _actual_rows_without_reconciliation(
            self.source_tables.get("finance_actual_outbox", []),
            self.source_tables.get("finance_actual_verifications", []),
        ):
            if row.get("batch_id") not in existing_ids:
                actual.append(row)
        return {
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

    def run(self, *, alias_resolver: AliasResolver | None = None) -> dict[str, Any]:
        targets = self.build_targets(alias_resolver=alias_resolver)
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
        return {
            "schema_version": "data-table-reverse-rehearsal-v1",
            "source_digest": self.backup_digest(),
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
    return sha256_json(matrix["target_schemas"])


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
