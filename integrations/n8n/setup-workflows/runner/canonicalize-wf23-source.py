#!/usr/bin/env python3
"""Emit one exact, secret-free canonical WF23 source projection."""

from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import sys
import uuid


SOURCE_SHA256 = "2e26bd188468cf007562d3f4f47670aeb3661fbd7a8e86053a62da2cc845d940"
WORKFLOW_FIELDS = (
    "id",
    "name",
    "description",
    "active",
    "nodes",
    "connections",
    "settings",
    "pinData",
    "meta",
    "nodeGroups",
)


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def workflow_projection(workflow: dict[str, object]) -> dict[str, object]:
    required = set(WORKFLOW_FIELDS) - {"description"}
    if required - set(workflow):
        raise ValueError("WF23_SOURCE_FIELDS_MISSING")
    projection = {
        field: workflow.get(field) if field == "description" else workflow[field]
        for field in WORKFLOW_FIELDS
    }
    projection.update(
        {
            "isArchived": False,
            "staticData": None,
            "sourceWorkflowId": None,
            "triggerCount": 0,
            "activeVersionId": None,
            "parentFolderId": workflow["meta"]["workflowFolder"]["id"],
        }
    )
    return projection


def history_projection(workflow: dict[str, object], version_id: str) -> dict[str, object]:
    if "versionMetadata" in workflow:
        raise ValueError("WF23_SOURCE_VERSION_METADATA_UNEXPECTED")
    canonical_version_id = str(uuid.UUID(version_id))
    return {
        "versionId": canonical_version_id,
        "workflowId": workflow["id"],
        "nodes": workflow["nodes"],
        "connections": workflow["connections"],
        "nodeGroups": workflow["nodeGroups"],
        "authors": "import",
        "name": None,
        "description": None,
        "autosaved": False,
    }


def encode_projection(value: object) -> str:
    return base64.b64encode(canonical_json(value).encode("utf-8")).decode("ascii")


def main() -> int:
    projection_kind = sys.argv[2] if len(sys.argv) >= 3 else None
    if (
        projection_kind not in {"workflow", "history"}
        or (projection_kind == "workflow" and len(sys.argv) != 3)
        or (projection_kind == "history" and len(sys.argv) != 4)
    ):
        print("CANONICAL_WF23_SOURCE_ARGUMENTS_REQUIRED", file=sys.stderr)
        return 2
    source = pathlib.Path(sys.argv[1])
    if not source.is_file() or source.is_symlink():
        print("REGULAR_WF23_SOURCE_REQUIRED", file=sys.stderr)
        return 1
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        print("WF23_SOURCE_SHA256_MISMATCH", file=sys.stderr)
        return 1
    try:
        workflow = json.loads(raw)
        projection = (
            workflow_projection(workflow)
            if projection_kind == "workflow"
            else history_projection(workflow, sys.argv[3])
        )
    except Exception:
        print("WF23_SOURCE_CANONICALIZATION_FAILED", file=sys.stderr)
        return 1
    print(encode_projection(projection))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
