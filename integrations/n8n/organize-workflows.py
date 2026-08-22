#!/usr/bin/env python3
"""Plan and verify the approved n8n workflow organization.

The module deliberately has no database or network dependency.  The SQL
migration is the production adapter; this module provides the canonical map,
deterministic digests, and a side-effect-free rehearsal used by tests and
operators before opening a bounded database transaction.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

FINANCE_PROJECT = "Finance"
GLOBAL_PROJECT = "Global"

TAG_IDS = {
    "finance": "fin0000000000001",
    "setup-required": "fin0000000000002",
    "inactive": "fin0000000000003",
    "active": "fin0000000000004",
}

FOLDER_SPECS = (
    {
        "id": "f1000000-0000-4000-8000-000000000100",
        "name": "Finance",
        "parentFolderId": None,
        "root": True,
    },
    {
        "id": "f1000000-0000-4000-8000-000000000190",
        "name": "Global",
        "parentFolderId": None,
        "root": True,
    },
    {
        "id": "f1000000-0000-4000-8000-000000000101",
        "name": "Account Reconciliation",
        "parentFolderId": "f1000000-0000-4000-8000-000000000100",
        "root": False,
    },
    {
        "id": "f1000000-0000-4000-8000-000000000102",
        "name": "Cashback Sweep",
        "parentFolderId": "f1000000-0000-4000-8000-000000000100",
        "root": False,
    },
    {
        "id": "f1000000-0000-4000-8000-000000000103",
        "name": "Shared",
        "parentFolderId": "f1000000-0000-4000-8000-000000000100",
        "root": False,
    },
    {
        "id": "f1000000-0000-4000-8000-000000000191",
        "name": "Shared",
        "parentFolderId": "f1000000-0000-4000-8000-000000000190",
        "root": False,
    },
)

LEGACY_FOLDER_IDS = frozenset(
    {
        "f1000000-0000-4000-8000-000000000001",
        "f1000000-0000-4000-8000-000000000002",
        "f1000000-0000-4000-8000-000000000003",
        "f1000000-0000-4000-8000-000000000004",
        "f1000000-0000-4000-8000-000000000005",
        "f1000000-0000-4000-8000-000000000006",
        "f1000000-0000-4000-8000-000000000007",
        "f1000000-0000-4000-8000-000000000090",
    }
)


def _workflow(
    number: str,
    code: str,
    current_name: str,
    target_name: str,
    folder_id: str,
    source: str,
) -> dict[str, str]:
    return {
        "id": f"10000000-0000-4000-8000-{number}",
        "code": code,
        "current_name": current_name,
        "target_name": target_name,
        "folder_id": folder_id,
        "source": source,
    }


WORKFLOW_MAP = (
    _workflow(
        "000000000001",
        "OUTLOOK_FINANCE_ACQUISITION",
        "Finance · Acquire Outlook Documents · Setup Required",
        "Acquire Outlook Documents",
        "f1000000-0000-4000-8000-000000000101",
        "integrations/n8n/workflows/01-outlook-finance-acquisition.json",
    ),
    _workflow(
        "000000000002",
        "RAKBANK_LIVE_CASHBACK",
        "Finance · RAKBANK Live Cashback · Setup Required",
        "RAKBANK Live Cashback",
        "f1000000-0000-4000-8000-000000000102",
        "integrations/n8n/workflows/02-rakbank-live-cashback.json",
    ),
    _workflow(
        "000000000003",
        "SHARED_STATEMENT_PIPELINE",
        "Finance · Shared Deterministic Statement Pipeline · Setup Required",
        "Shared Deterministic Statement Pipeline",
        "f1000000-0000-4000-8000-000000000101",
        "integrations/n8n/workflows/03-shared-statement-pipeline.json",
    ),
    _workflow(
        "000000000004",
        "EI_MONTHLY_STATEMENT",
        "Finance · EI Statement Cycle Poll · Setup Required",
        "EI Statement Cycle Poll",
        "f1000000-0000-4000-8000-000000000101",
        "integrations/n8n/workflows/04-ei-monthly-statement.json",
    ),
    _workflow(
        "000000000005",
        "WIO_MONTHLY_STATEMENT",
        "Finance · Wio Statement Cycle Poll · Setup Required",
        "Wio Statement Cycle Poll",
        "f1000000-0000-4000-8000-000000000101",
        "integrations/n8n/workflows/05-wio-monthly-statement.json",
    ),
    _workflow(
        "000000000006",
        "RAK_MONTHLY_STATEMENT",
        "Finance · RAK Monthly Statement · Setup Required",
        "RAK Monthly Statement",
        "f1000000-0000-4000-8000-000000000101",
        "integrations/n8n/workflows/06-rak-monthly-statement.json",
    ),
    _workflow(
        "000000000007",
        "SC_MONTHLY_STATEMENT",
        "Finance · SC Monthly Statement · Setup Required",
        "SC Monthly Statement",
        "f1000000-0000-4000-8000-000000000101",
        "integrations/n8n/workflows/07-sc-monthly-statement.json",
    ),
    _workflow(
        "000000000008",
        "SC_LIVE_CASHBACK",
        "Finance · SC Live Cashback · Setup Required",
        "SC Live Cashback",
        "f1000000-0000-4000-8000-000000000102",
        "integrations/n8n/workflows/08-sc-live-cashback.json",
    ),
    _workflow(
        "000000000009",
        "AI_PROPOSAL",
        "Finance · Subscription Agent Proposal · Setup Required",
        "Subscription Agent Proposal",
        "f1000000-0000-4000-8000-000000000103",
        "integrations/n8n/workflows/09-ai-proposal.json",
    ),
    _workflow(
        "000000000010",
        "FINANCE_OPERATIONS_STATUS",
        "Finance · Operations Status and Audited MCP Dispatch · Setup Required",
        "Operations Status and Audited MCP Dispatch",
        "f1000000-0000-4000-8000-000000000103",
        "integrations/n8n/workflows/10-finance-operations-status.json",
    ),
    _workflow(
        "000000000011",
        "INTERACTIVE_ARTIFACT_HANDOFF",
        "Finance · Interactive Artifact Handoff · Setup Required",
        "Interactive Artifact Handoff",
        "f1000000-0000-4000-8000-000000000101",
        "integrations/n8n/workflows/11-interactive-artifact-handoff.json",
    ),
    _workflow(
        "000000000012",
        "OUTLOOK_MESSAGE_SWEEP",
        "Finance · Sweep Outlook Messages · Setup Required",
        "Sweep Outlook Messages",
        "f1000000-0000-4000-8000-000000000101",
        "integrations/n8n/workflows/12-outlook-message-sweep.json",
    ),
    _workflow(
        "000000000013",
        "DOCUMENT_EXTRACTION_REQUEST",
        "Finance · Request Document Extraction · Setup Required",
        "Request Document Extraction",
        "f1000000-0000-4000-8000-000000000101",
        "integrations/n8n/workflows/13-document-extraction-request.json",
    ),
    _workflow(
        "000000000014",
        "LOCAL_PDF_EXTRACTION",
        "Finance · Local PDF Extraction Ladder · Setup Required",
        "Local PDF Extraction Ladder",
        "f1000000-0000-4000-8000-000000000101",
        "integrations/n8n/workflows/14-local-pdf-extraction.json",
    ),
    _workflow(
        "000000000015",
        "FINANCE_MCP_FACADE",
        "Finance · Bounded MCP Facade · Setup Required",
        "Bounded MCP Facade",
        "f1000000-0000-4000-8000-000000000103",
        "integrations/n8n/workflows/15-finance-mcp-facade.json",
    ),
    _workflow(
        "000000000016",
        "OPERATIONS_ERROR_HANDLER",
        "Finance · Redacted Operations Error Handler · Setup Required",
        "Redacted Operations Error Handler",
        "f1000000-0000-4000-8000-000000000103",
        "integrations/n8n/workflows/16-operations-error-handler.json",
    ),
    _workflow(
        "000000000017",
        "ACTUAL_OUTBOX_RECOVERY",
        "Finance · Actual Outbox Recovery · Setup Required",
        "Actual Outbox Recovery",
        "f1000000-0000-4000-8000-000000000103",
        "integrations/n8n/workflows/17-actual-outbox-recovery.json",
    ),
    _workflow(
        "000000000018",
        "FINANCE_WRITER_LEASE",
        "Finance · Fenced Actual Writer Lease · Setup Required",
        "Fenced Actual Writer Lease",
        "f1000000-0000-4000-8000-000000000103",
        "integrations/n8n/workflows/18-finance-writer-lease.json",
    ),
    _workflow(
        "000000000019",
        "PLATFORM_DATA_TABLE_BOOTSTRAP",
        "Finance · Platform Data Table Bootstrap · Setup Required",
        "Platform Data Table Bootstrap",
        "f1000000-0000-4000-8000-000000000103",
        "integrations/n8n/workflows/19-platform-data-table-bootstrap.json",
    ),
    _workflow(
        "000000000020",
        "ACTUAL_OUTBOX_APPLY",
        "Finance · Apply Prepared Actual Outbox · Setup Required",
        "Apply Prepared Actual Outbox",
        "f1000000-0000-4000-8000-000000000103",
        "integrations/n8n/workflows/20-actual-outbox-apply.json",
    ),
    _workflow(
        "000000000021",
        "SUBSCRIPTION_AGENT_ADAPTER",
        "Finance · Subscription Agent Adapter · Setup Required",
        "Subscription Agent Adapter",
        "f1000000-0000-4000-8000-000000000103",
        "integrations/n8n/workflows/21-subscription-agent-adapter.json",
    ),
    _workflow(
        "000000000024",
        "SHARED_MONTHLY_STATEMENT_CYCLE",
        "Finance · Shared Monthly Statement Cycle",
        "Shared Monthly Statement Cycle",
        "f1000000-0000-4000-8000-000000000103",
        "integrations/n8n/workflows/22-shared-monthly-statement-cycle.json",
    ),
)

WORKFLOW_BY_ID = {row["id"]: row for row in WORKFLOW_MAP}
TARGET_FOLDER_BY_ID = {row["id"]: row for row in FOLDER_SPECS}
TARGET_IDS = frozenset(WORKFLOW_BY_ID)
CANONICAL_REPLACEMENT_ID = "10000000-0000-4000-8000-000000000024"
ORPHAN_WORKFLOW_ID = "10000000-0000-4000-8000-000000000115"
ORPHAN_WORKFLOW_NAME = "Finance · Bounded MCP Facade"
CANONICAL_EXPORT_PATH = (
    Path(__file__).resolve().parent / "workflows" / "22-shared-monthly-statement-cycle.json"
)
CANONICAL_EXPORT_RELATIVE_PATH = (
    "integrations/n8n/workflows/22-shared-monthly-statement-cycle.json"
)
CANONICAL_EXPORT_SHA256 = (
    "2fd8629d0396b2715ec2c4ac3c0b66264f980f51982ad67bc87fb020bdd5fdb2"
)
# These are the workflow_entity fields that make up the imported workflow body.
# Keep mutable name, runtime/version, and folder columns out of this digest;
# those are normalized or guarded independently by the cutover contract.
CANONICAL_PERSISTED_BODY_MD5 = "be22ef98b1a3a9aaea79a24673e85a57"
PERSISTED_BODY_FIELDS = (
    "id",
    "nodes",
    "connections",
    "settings",
    "pinData",
    "meta",
)
LEGACY_IDS = frozenset((TARGET_IDS - {CANONICAL_REPLACEMENT_ID}) | {ORPHAN_WORKFLOW_ID})
STATUS_MARKERS = ("setup required", "spec_only", "spec only", "inactive", "blocked")
W15_ID = "10000000-0000-4000-8000-000000000015"
W15_ACTIVE_VERSION = "1bd2090e-13e8-4427-bfe7-630c11bf0da5"


def canonical_json(value: Any) -> str:
    """Return stable JSON suitable for a digest or a redacted plan receipt."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sorted_rows(
    rows: Iterable[Mapping[str, Any]], key: str = "id"
) -> list[dict[str, Any]]:
    return [dict(row) for row in sorted(rows, key=lambda row: str(row.get(key, "")))]


def _md5(value: Any) -> str:
    return hashlib.md5(
        canonical_json(value).encode("utf-8"), usedforsecurity=False
    ).hexdigest()


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


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


def _workflow_tags(state: Mapping[str, Any]) -> list[dict[str, str]]:
    edges = state.get("workflow_tags", [])
    if isinstance(edges, Mapping):
        edges = [
            {"workflowId": workflow_id, "tagId": tag_id}
            for workflow_id, tag_ids in edges.items()
            for tag_id in tag_ids
        ]
    return sorted(
        (
            {"workflowId": str(edge["workflowId"]), "tagId": str(edge["tagId"])}
            for edge in edges
        ),
        key=lambda edge: (edge["workflowId"], edge["tagId"]),
    )


def snapshot_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Copy the exact state for rollback without serializing it to the receipt."""

    return copy.deepcopy(dict(state))


def snapshot_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return non-secret counts, tuples, placements, and deterministic digests."""

    workflows = _sorted_rows(state.get("workflows", []))
    folders = _sorted_rows(state.get("folders", []))
    tags = _sorted_rows(state.get("tags", []))
    edges = _workflow_tags(state)
    logical_rows = [
        {
            "id": row.get("id"),
            "name": row.get("name"),
            "parentFolderId": row.get("parentFolderId"),
            "active": row.get("active"),
            "activeVersionId": row.get("activeVersionId"),
        }
        for row in workflows
    ]
    full_state = {
        "workflows": workflows,
        "folders": folders,
        "tags": tags,
        "workflow_tags": edges,
    }
    return {
        "workflow_count": len(workflows),
        "active_count": sum(row.get("active") is True for row in workflows),
        "published_count": sum(
            row.get("activeVersionId") not in (None, "") for row in workflows
        ),
        "workflow_ids": [row.get("id") for row in workflows],
        "version_tuples": [
            [row.get("id"), row.get("active"), row.get("activeVersionId")]
            for row in workflows
        ],
        "placements": [
            [row.get("id"), row.get("parentFolderId"), row.get("name")]
            for row in workflows
        ],
        "inactive_edge_count": sum(
            edge["tagId"] == TAG_IDS["inactive"] for edge in edges
        ),
        "active_edge_count": sum(edge["tagId"] == TAG_IDS["active"] for edge in edges),
        "logical_md5": _md5(logical_rows),
        "logical_sha256": _sha256(logical_rows),
        "full_row_md5": _md5(full_state),
        "full_row_sha256": _sha256(full_state),
    }


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
    if not isinstance(nodes, list) or len(nodes) != 16:
        _fail("CANONICAL_EXPORT_NODE_COUNT_MISMATCH")
    meta = export.get("meta")
    if not isinstance(meta, dict) or meta.get("financeWorkflowCode") != "SHARED_MONTHLY_STATEMENT_CYCLE":
        _fail("CANONICAL_EXPORT_CODE_MISMATCH")
    if persisted_workflow_body_md5(export) != CANONICAL_PERSISTED_BODY_MD5:
        _fail("CANONICAL_EXPORT_BODY_DIGEST_MISMATCH")
    return copy.deepcopy(export)


def validate_contract() -> None:
    """Validate the checked-in organization contract at import time and in tests."""

    if len(WORKFLOW_MAP) != 22 or len(WORKFLOW_BY_ID) != 22:
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


def _validate_state_shape(state: Mapping[str, Any]) -> str:
    workflows = list(state.get("workflows", []))
    ids = [str(row.get("id")) for row in workflows]
    if len(ids) != len(set(ids)):
        _fail("LIVE_WORKFLOW_SET_MISMATCH")
    roster = frozenset(ids)
    if roster == TARGET_IDS:
        roster_kind = "canonical"
    elif roster == LEGACY_IDS:
        roster_kind = "orphaned"
    else:
        _fail("LIVE_WORKFLOW_SET_MISMATCH")
    for row in workflows:
        spec = WORKFLOW_BY_ID.get(row["id"])
        allowed_names = (
            (
                spec["current_name"],
                spec["current_name"].removesuffix(" · Setup Required"),
                spec["target_name"],
            )
            if spec is not None
            else (
                ORPHAN_WORKFLOW_NAME,
                "Finance · Bounded MCP Facade · Setup Required",
                "Bounded MCP Facade",
            )
        )
        if row.get("name") not in allowed_names:
            _fail(f"UNEXPECTED_WORKFLOW_NAME:{row['id']}")
        if not isinstance(row.get("active"), bool):
            _fail(f"ACTIVE_STATE_NOT_BOOLEAN:{row['id']}")
        if row["id"] != "10000000-0000-4000-8000-000000000015" and row.get("active"):
            _fail("UNEXPECTED_ACTIVE_WORKFLOW")
    active = [row for row in workflows if row.get("active") is True]
    published = [
        row for row in workflows if row.get("activeVersionId") not in (None, "")
    ]
    if {row["id"] for row in active} != {W15_ID} or len(active) != 1:
        _fail("ACTIVE_WORKFLOW_TUPLE_MISMATCH")
    if {row["id"] for row in published} != {W15_ID} or len(published) != 1:
        _fail("PUBLISHED_WORKFLOW_TUPLE_MISMATCH")
    w15 = next(row for row in workflows if row["id"] == W15_ID)
    if w15.get("activeVersionId") != W15_ACTIVE_VERSION:
        _fail("W15_ACTIVE_VERSION_MISMATCH")
    edges = _workflow_tags(state)
    if any(
        edge["tagId"] == TAG_IDS["active"] and edge["workflowId"] != W15_ID
        for edge in edges
    ):
        _fail("ACTIVE_TAG_ASSIGNED_TO_NON_W15")
    return roster_kind


def _ensure_target_folders(state: dict[str, Any], project_id: str | None) -> None:
    folders = state.setdefault("folders", [])
    by_id = {str(row.get("id")): row for row in folders}
    for spec in FOLDER_SPECS:
        existing = by_id.get(spec["id"])
        if existing is not None:
            if project_id is not None and existing.get("projectId") not in (
                None,
                project_id,
            ):
                _fail(f"FOLDER_PROJECT_MISMATCH:{spec['id']}")
            if existing.get("name") not in (None, spec["name"]):
                _fail(f"FOLDER_NAME_CONFLICT:{spec['id']}")
            if existing.get("parentFolderId") not in (None, spec["parentFolderId"]):
                _fail(f"FOLDER_PARENT_CONFLICT:{spec['id']}")
            existing.update(
                {"name": spec["name"], "parentFolderId": spec["parentFolderId"]}
            )
            continue
        row = {
            "id": spec["id"],
            "name": spec["name"],
            "parentFolderId": spec["parentFolderId"],
        }
        if project_id is not None:
            row["projectId"] = project_id
        folders.append(row)


def _ensure_tags(state: dict[str, Any]) -> None:
    tags = state.setdefault("tags", [])
    by_id = {str(row.get("id")): row for row in tags}
    for name, tag_id in TAG_IDS.items():
        existing = by_id.get(tag_id)
        if existing is not None:
            if existing.get("name") not in (None, name):
                _fail(f"TAG_NAME_CONFLICT:{tag_id}")
            existing["name"] = name
        else:
            tags.append({"id": tag_id, "name": name})


def apply_plan(state: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the map to an in-memory copy, preserving version and active state."""

    validate_contract()
    roster_kind = _validate_state_shape(state)
    result = snapshot_state(state)
    project_id = result.get("projectId")
    _ensure_target_folders(result, project_id)
    workflows = {row["id"]: row for row in result["workflows"]}
    if roster_kind == "orphaned":
        # A live deployment can contain the old disposable duplicate while the
        # canonical export is imported in the same bounded cutover.  Do not
        # relabel the orphan: bind the checked-in export and retain only the
        # runtime fields that are not part of its workflow body.
        orphan = workflows.pop(ORPHAN_WORKFLOW_ID)
        runtime_fields = {
            key: copy.deepcopy(orphan[key])
            for key in (
                "active",
                "activeVersionId",
                "projectId",
                "createdAt",
                "updatedAt",
            )
            if key in orphan
        }
        canonical = canonical_workflow_export()
        orphan.clear()
        orphan.update(canonical)
        orphan.update(runtime_fields)
        orphan["id"] = CANONICAL_REPLACEMENT_ID
        workflows[CANONICAL_REPLACEMENT_ID] = orphan
    for workflow_id, spec in WORKFLOW_BY_ID.items():
        row = workflows[workflow_id]
        row["name"] = spec["target_name"]
        row["parentFolderId"] = spec["folder_id"]
    # The old migration created these eight flat folders.  Remove only those
    # known IDs after the workflows have moved; unrelated project folders stay.
    referenced = {row.get("parentFolderId") for row in result["workflows"]}
    result["folders"] = [
        row
        for row in result["folders"]
        if row.get("id") not in LEGACY_FOLDER_IDS or row.get("id") in referenced
    ]
    _ensure_tags(result)
    edges = _workflow_tags(result)
    edges = [
        {
            **edge,
            "workflowId": CANONICAL_REPLACEMENT_ID
            if edge["workflowId"] == ORPHAN_WORKFLOW_ID
            else edge["workflowId"],
        }
        for edge in edges
        if edge["workflowId"] != ORPHAN_WORKFLOW_ID
        or roster_kind == "orphaned"
    ]
    w15 = W15_ID
    inactive = TAG_IDS["inactive"]
    active = TAG_IDS["active"]
    edges = [
        edge
        for edge in edges
        if not (edge["workflowId"] == w15 and edge["tagId"] == inactive)
    ]
    if {"workflowId": w15, "tagId": active} not in edges:
        edges.append({"workflowId": w15, "tagId": active})
    required_edges = [
        {"workflowId": spec["id"], "tagId": TAG_IDS[tag]}
        for spec in WORKFLOW_MAP
        for tag in ("finance", "setup-required")
    ] + [
        {"workflowId": spec["id"], "tagId": inactive}
        for spec in WORKFLOW_MAP
        if spec["id"] != w15
    ]
    for edge in required_edges:
        if edge not in edges:
            edges.append(edge)
    result["workflow_tags"] = edges
    return result


def plan_organization(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return a redacted rehearsal, including exact rollback state in memory."""

    before_state = snapshot_state(state)
    roster_kind = _validate_state_shape(before_state)
    after_state = apply_plan(state)
    before = snapshot_summary(before_state)
    after = snapshot_summary(after_state)
    orphan = next(
        (
            row
            for row in before_state.get("workflows", [])
            if row.get("id") == ORPHAN_WORKFLOW_ID
        ),
        None,
    )
    retirement = {
        "mode": "backup_then_replace",
        "legacy_workflow_id": ORPHAN_WORKFLOW_ID,
        "replacement_workflow_id": CANONICAL_REPLACEMENT_ID,
        "canonical_source_path": CANONICAL_EXPORT_RELATIVE_PATH,
        "canonical_source_sha256": CANONICAL_EXPORT_SHA256,
        "canonical_body_md5": CANONICAL_PERSISTED_BODY_MD5,
        "canonical_node_count": len(canonical_workflow_export()["nodes"]),
        "canonical_source_bound": True,
        "legacy_present": orphan is not None,
        "backup_captured": orphan is not None,
        "backup_sha256": _sha256(orphan) if orphan is not None else None,
        "rollback_supported": True,
        "rollback_restores_exact_prestate": True,
    }
    return {
        "contract_version": 3,
        "changed": before != after,
        "before": before,
        "after": after,
        "rollback_state": before_state,
        "after_state": after_state,
        "idempotent": snapshot_summary(apply_plan(after_state)) == after,
        "production_mutation": False,
        "retirement": retirement,
        "prestate_roster": roster_kind,
    }


def rollback_state(
    state: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> dict[str, Any]:
    """Restore the exact captured state, including opaque workflow columns."""

    del state
    return snapshot_state(snapshot)


def _public_report(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: plan[key]
        for key in (
            "contract_version",
            "changed",
            "before",
            "after",
            "idempotent",
            "production_mutation",
            "retirement",
            "prestate_roster",
        )
    }


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state", type=Path, help="JSON rehearsal state; omitted to print the contract"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the in-memory result to --output (never a database)",
    )
    parser.add_argument(
        "--output", type=Path, help="JSON output path for --apply; stdout otherwise"
    )
    args = parser.parse_args(argv)
    validate_contract()
    if args.state is None:
        print(
            json.dumps(
                {
                    "folders": list(FOLDER_SPECS),
                    "workflows": list(WORKFLOW_MAP),
                    "tags": TAG_IDS,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    plan = plan_organization(_load_json(args.state))
    if args.apply:
        payload = plan["after_state"]
        encoded = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
    else:
        encoded = (
            json.dumps(
                _public_report(plan), ensure_ascii=False, indent=2, sort_keys=True
            )
            + "\n"
        )
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


validate_contract()


if __name__ == "__main__":
    raise SystemExit(main())
