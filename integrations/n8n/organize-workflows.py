#!/usr/bin/env python3
"""Validate the approved n8n workflow organization contract.

The module deliberately has no database or network dependency. The SQL
migration is the production adapter; this module provides the canonical map,
deterministic digests, and contract validation used before opening a bounded
database transaction.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

CONTRACT_PATH = Path(__file__).resolve().parent / "workflow-folders.json"
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
TAG_IDS = {row["name"]: row["id"] for row in CONTRACT["tag_definitions"]}
FOLDER_SPECS = tuple(
    {
        "id": row["id"],
        "name": row["name"],
        "parentFolderId": row["parentFolderId"],
        "root": row["root"],
    }
    for row in CONTRACT["folders"]
)
WORKFLOW_MAP = tuple(dict(row) for row in CONTRACT["workflows"])
WORKFLOW_BY_ID = {row["id"]: row for row in WORKFLOW_MAP}
FOLDER_BY_CODE = {row["code"]: row["folder_id"] for row in WORKFLOW_MAP}
TARGET_IDS = frozenset(WORKFLOW_BY_ID)
CANONICAL_REPLACEMENT_ID = "10000000-0000-4000-8000-000000000024"
ORPHAN_WORKFLOW_ID = "10000000-0000-4000-8000-000000000115"
CANONICAL_EXPORT_PATH = (
    Path(__file__).resolve().parent / "workflows" / "22-shared-monthly-statement-cycle.json"
)
CANONICAL_EXPORT_RELATIVE_PATH = (
    "integrations/n8n/workflows/22-shared-monthly-statement-cycle.json"
)
CANONICAL_EXPORT_SHA256 = (
    "5110e10b03750b9ad530f44d8b28c10fa056db31120f18ba379e5d76c2ca8437"
)
# These are the workflow_entity fields that make up the imported workflow body.
# Keep mutable name, runtime/version, and folder columns out of this digest;
# those are normalized or guarded independently by the cutover contract.
CANONICAL_PERSISTED_BODY_MD5 = "ec3e7cc48ce0bd2d080749060d1bfa59"
PERSISTED_BODY_FIELDS = (
    "id",
    "nodes",
    "connections",
    "settings",
    "pinData",
    "meta",
)
STATUS_MARKERS = ("setup required", "spec_only", "spec only", "inactive", "blocked")


def _postgres_jsonb_text(value: Any) -> str:
    """Render JSON using PostgreSQL jsonb's deterministic text representation."""

    if isinstance(value, Mapping):
        items = sorted(
            value.items(),
            key=lambda item: (
                len(str(item[0]).encode("utf-8")),
                str(item[0]).encode("utf-8"),
            ),
        )
        encoded = ", ".join(
            f"{json.dumps(str(key), ensure_ascii=False)}: {_postgres_jsonb_text(child)}"
            for key, child in items
        )
        return "{" + encoded + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_postgres_jsonb_text(child) for child in value) + "]"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def persisted_workflow_body(workflow: Mapping[str, Any]) -> dict[str, Any]:
    """Select the workflow_entity fields covered by the canonical body digest."""

    return {field: copy.deepcopy(workflow.get(field)) for field in PERSISTED_BODY_FIELDS}


def persisted_workflow_body_md5(workflow: Mapping[str, Any]) -> str:
    """Return the digest equivalent to PostgreSQL's jsonb body expression."""

    body = _postgres_jsonb_text(persisted_workflow_body(workflow))
    return hashlib.md5(body.encode("utf-8"), usedforsecurity=False).hexdigest()


def _fail(message: str) -> None:
    raise ValueError(message)


def canonical_workflow_export() -> dict[str, Any]:
    """Load the checked-in 024 export used for orphan replacement."""

    try:
        raw = CANONICAL_EXPORT_PATH.read_bytes()
    except OSError as exc:
        _fail(f"CANONICAL_EXPORT_UNREADABLE:{exc}")
    source_sha256 = hashlib.sha256(raw).hexdigest()
    if source_sha256 != CANONICAL_EXPORT_SHA256:
        _fail("CANONICAL_EXPORT_HASH_MISMATCH")
    try:
        export = json.loads(raw)
    except json.JSONDecodeError as exc:
        _fail(f"CANONICAL_EXPORT_JSON_INVALID:{exc.msg}")
    if not isinstance(export, dict):
        _fail("CANONICAL_EXPORT_NOT_OBJECT")
    if export.get("id") != CANONICAL_REPLACEMENT_ID:
        _fail("CANONICAL_EXPORT_ID_MISMATCH")
    if export.get("name") != "Finance · Shared Monthly Statement Cycle":
        _fail("CANONICAL_EXPORT_NAME_MISMATCH")
    nodes = export.get("nodes")
    if not isinstance(nodes, list) or len(nodes) != 18:
        _fail("CANONICAL_EXPORT_NODE_COUNT_MISMATCH")
    meta = export.get("meta")
    if not isinstance(meta, dict) or meta.get("financeWorkflowCode") != "SHARED_MONTHLY_STATEMENT_CYCLE":
        _fail("CANONICAL_EXPORT_CODE_MISMATCH")
    if persisted_workflow_body_md5(export) != CANONICAL_PERSISTED_BODY_MD5:
        _fail("CANONICAL_EXPORT_BODY_DIGEST_MISMATCH")
    return copy.deepcopy(export)


def validate_contract() -> None:
    """Validate the checked-in organization contract at import time and in tests."""

    if CONTRACT.get("schema_version") != 2:
        _fail("CONTRACT_SCHEMA_VERSION_MISMATCH")
    if len(TAG_IDS) != 4 or set(TAG_IDS) != {
        "finance",
        "setup-required",
        "inactive",
        "active",
    }:
        _fail("TAG_CONTRACT_MISMATCH")
    if len(WORKFLOW_MAP) != 19 or len(WORKFLOW_BY_ID) != 19:
        _fail("WORKFLOW_MAP_COUNT_MISMATCH")
    if CANONICAL_REPLACEMENT_ID not in TARGET_IDS or ORPHAN_WORKFLOW_ID in TARGET_IDS:
        _fail("CANONICAL_REPLACEMENT_ROSTER_MISMATCH")
    canonical_export = canonical_workflow_export()
    canonical_spec = WORKFLOW_BY_ID[CANONICAL_REPLACEMENT_ID]
    if canonical_spec["source"] != CANONICAL_EXPORT_RELATIVE_PATH:
        _fail("CANONICAL_EXPORT_SOURCE_MISMATCH")
    if canonical_export["id"] != canonical_spec["id"]:
        _fail("CANONICAL_EXPORT_MAP_ID_MISMATCH")
    if canonical_export["meta"]["financeWorkflowCode"] != canonical_spec["code"]:
        _fail("CANONICAL_EXPORT_MAP_CODE_MISMATCH")
    if len(FOLDER_SPECS) != 6:
        _fail("FOLDER_SPEC_COUNT_MISMATCH")
    if sum(row["root"] for row in FOLDER_SPECS) != 2:
        _fail("ROOT_FOLDER_COUNT_MISMATCH")
    if sum(not row["root"] for row in FOLDER_SPECS) != 4:
        _fail("CHILD_FOLDER_COUNT_MISMATCH")
    folder_ids = {row["id"] for row in FOLDER_SPECS}
    if len(folder_ids) != 6:
        _fail("FOLDER_ID_MISMATCH")
    for row in FOLDER_SPECS:
        if row["root"] and row["parentFolderId"] is not None:
            _fail("ROOT_PARENT_MISMATCH")
        if not row["root"] and row["parentFolderId"] not in folder_ids:
            _fail("CHILD_PARENT_MISMATCH")
    if set(FOLDER_BY_CODE) != {row["code"] for row in WORKFLOW_MAP}:
        _fail("WORKFLOW_FOLDER_ASSIGNMENT_MISMATCH")
    for row in WORKFLOW_MAP:
        if row["folder_id"] not in folder_ids or not row["target_name"].strip():
            _fail(f"WORKFLOW_TARGET_MISMATCH:{row['id']}")
        lowered = row["target_name"].casefold()
        if any(marker in lowered for marker in STATUS_MARKERS):
            _fail(f"STATUS_IN_TARGET_NAME:{row['id']}")
    if (
        WORKFLOW_BY_ID["10000000-0000-4000-8000-000000000019"]["folder_id"]
        != "f1000000-0000-4000-8000-000000000103"
    ):
        _fail("W19_NOT_FINANCE_SHARED")


def main(argv: list[str] | None = None) -> int:
    del argv
    validate_contract()
    print(json.dumps({"folders": list(FOLDER_SPECS), "workflows": list(WORKFLOW_MAP), "tags": TAG_IDS}, indent=2, sort_keys=True))
    return 0


validate_contract()


if __name__ == "__main__":
    raise SystemExit(main())
