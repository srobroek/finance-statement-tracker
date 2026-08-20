"""Stage finance-owned inputs for the generic n8n application interface.

The platform receives one self-contained, read-only application manifest.  This
adapter copies the finance inputs into a staging root so the platform validator
never needs to follow paths outside the supplied application boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path


APPLICATION_ID = "finance-statement-tracker"
WORKFLOW_COUNT = 21
TABLE_COUNT = 15
FIXTURE_WORKFLOW_COUNT = 18
MCP_ROUTE = "/mcp/finance-operations-v1"
BOOTSTRAP_WORKFLOW_ID = "10000000-0000-4000-8000-000000000019"
SOURCE_FILES = {
    "folders": Path("integrations/n8n/workflow-folders.json"),
    "folder_sql": Path("integrations/n8n/workflow-folder-placement.sql"),
    "tables": Path("integrations/n8n/data-tables.json"),
    "fixtures": Path("integrations/n8n/disposable/fixture-manifest.json"),
    "onedrive_setup": Path(
        "integrations/n8n/setup-workflows/22-onedrive-finance-evidence-root-setup.json"
    ),
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_regular(source: Path, destination: Path) -> None:
    if not source.is_file() or source.is_symlink():
        raise ValueError(f"finance application input must be a regular file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _validate_commit(source_commit: str) -> None:
    if not isinstance(source_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("application source commit must be a lowercase 40-character SHA")


def stage_application(source_root: Path, destination: Path, source_commit: str) -> Path:
    """Copy finance inputs and emit a generic n8n manifest at ``destination``.

    The generated manifest deliberately contains only generic interface fields;
    finance-specific counts and table names are represented as application input
    values rather than platform assumptions.
    """

    source_root = Path(source_root).resolve()
    destination = Path(destination).resolve()
    _validate_commit(source_commit)
    finance_root = source_root / "integrations" / "n8n"
    workflow_root = finance_root / "workflows"
    workflows = sorted(workflow_root.glob("*.json"))
    if len(workflows) != WORKFLOW_COUNT or any(path.is_symlink() for path in workflows):
        raise ValueError("finance workflow corpus does not match the application contract")

    registry = _load(finance_root / "pipeline-registry.json")
    registry_files = sorted(row["file"] for row in registry["workflows"])
    if registry_files != [path.name for path in workflows]:
        raise ValueError("finance workflow registry does not match the workflow corpus")
    tables = _load(finance_root / "data-tables.json")["tables"]
    if len(tables) != TABLE_COUNT or len({row["name"] for row in tables}) != TABLE_COUNT:
        raise ValueError("finance Data Table schema does not match the application contract")
    fixture_manifest = _load(finance_root / "disposable" / "fixture-manifest.json")
    fixture_workflows = fixture_manifest["workflows"]
    if len(fixture_workflows) != FIXTURE_WORKFLOW_COUNT:
        raise ValueError("finance fixture workflow corpus does not match the application contract")
    for fixture in fixture_workflows:
        filename = fixture.get("file")
        if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".json"):
            raise ValueError("finance fixture workflow filename is invalid")
        source = finance_root / "disposable" / "generated" / filename
        _copy_regular(source, destination / "fixtures" / "generated" / filename)
        if not isinstance(fixture.get("sha256"), str) or _sha256(source) != fixture["sha256"]:
            raise ValueError(f"finance fixture workflow hash mismatch: {filename}")
    for filename, expected_hash in fixture_manifest["source_workflow_sha256"].items():
        source = workflow_root / filename
        if not source.is_file() or source.is_symlink() or _sha256(source) != expected_hash:
            raise ValueError(f"finance source workflow hash mismatch: {filename}")

    for workflow in workflows:
        _copy_regular(workflow, destination / "workflows" / workflow.name)
    for key, relative_path in SOURCE_FILES.items():
        _copy_regular(source_root / relative_path, destination / {
            "folders": "workflow-folders.json",
            "folder_sql": "workflow-folder-placement.sql",
            "tables": "bootstrap/data-tables.json",
            "fixtures": "fixtures/fixture-manifest.json",
            "onedrive_setup": "fixtures/onedrive-root-setup.json",
        }[key])

    (destination / "bootstrap" / "seed.sql").write_text(
        "-- Finance Data Tables are created by the supplied bootstrap workflow.\n",
        encoding="utf-8",
    )
    workflow_19 = _load(workflow_root / "19-platform-data-table-bootstrap.json")
    workflow_15 = _load(workflow_root / "15-finance-mcp-facade.json")
    trigger = next(node for node in workflow_15["nodes"] if "mcpTrigger" in node["type"])
    if trigger["parameters"]["path"] != MCP_ROUTE.removeprefix("/mcp/"):
        raise ValueError("finance MCP route is not the application interface route")

    manifest = {
        "schema_version": 1,
        "application": {"id": APPLICATION_ID, "source_commit": source_commit},
        "workflows": {
            "directory": "workflows",
            "files": [path.name for path in workflows],
            "inactive": True,
            "published": False,
        },
        "folders": {"manifest": "workflow-folders.json", "sql": "workflow-folder-placement.sql"},
        "bootstrap": {
            "directory": "bootstrap",
            "sql": "seed.sql",
            "workflow_id": workflow_19["id"],
            "tables": [{"name": row["name"]} for row in tables],
        },
        "fixtures": {"directory": "fixtures", "manifest": "fixtures/fixture-manifest.json"},
        "route": {
            "path": MCP_ROUTE,
            "edge_auth": "CLOUDFLARE_ACCESS_SERVICE_AUTH",
            "origin_auth": "APPLICATION_SUPPLIED_BEARER",
            "enabled": False,
        },
    }
    if manifest["bootstrap"]["workflow_id"] != BOOTSTRAP_WORKFLOW_ID:
        raise ValueError("finance bootstrap workflow identity changed")
    manifest_path = destination / "application-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path
