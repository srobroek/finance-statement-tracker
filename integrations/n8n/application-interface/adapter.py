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
WORKFLOW_COUNT = 19
FIXTURE_WORKFLOW_COUNT = 18
MCP_ROUTE = "/mcp/finance-operations-v1"
BOOTSTRAP_WORKFLOW_ID = "10000000-0000-4000-8000-000000000019"
CANONICAL_FORBIDDEN_FIELDS = ["id", "value", "token", "secret", "password", "client_secret"]
PLACEHOLDER_FIELDS = {"name", "binding", "type"}
SOURCE_FILES = {
    "folders": Path("integrations/n8n/workflow-folders.json"),
    "folder_sql": Path("integrations/n8n/workflow-folder-placement.sql"),
    "tables": Path("integrations/n8n/data-tables.json"),
    "fixtures": Path("integrations/n8n/disposable/fixture-manifest.json"),
    "onedrive_setup": Path(
        "integrations/n8n/setup-workflows/22-onedrive-finance-evidence-root-setup.json"
    ),
    "credential_bindings": Path("integrations/n8n/credential-bindings.json"),
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


def _target_tables(finance_root: Path) -> list[dict[str, str]]:
    """Project the current Data Table targets from the migration matrix.

    ``data-tables.json`` remains the source/legacy inventory.  The application
    bootstrap contract instead follows the matrix targets consumed by workflow
    19, so this adapter must not maintain a second authored table list.
    """

    matrix = _load(finance_root / "data-table-migration-matrix.json")
    targets = matrix.get("targets")
    target_schemas = matrix.get("target_schemas")
    if (
        not isinstance(targets, list)
        or not targets
        or any(not isinstance(name, str) or not name for name in targets)
        or len(set(targets)) != len(targets)
        or not isinstance(target_schemas, dict)
        or set(target_schemas) != set(targets)
    ):
        raise ValueError("finance Data Table migration matrix target contract is invalid")

    tables = matrix.get("tables")
    if not isinstance(tables, list):
        raise TypeError("finance Data Table migration matrix tables are invalid")
    for table in tables:
        if not isinstance(table, dict):
            raise TypeError("finance Data Table migration matrix table row is invalid")
        target = table.get("target_table")
        if target is not None and target not in targets:
            raise ValueError("finance Data Table migration matrix has an unknown target")

    return [{"name": name} for name in targets]


def _credential_manifest(finance_root: Path) -> dict[str, object]:
    """Project the checked-in credential identities into the staged manifest."""

    source_manifest = _load(finance_root / "application-manifest.json")
    credentials = source_manifest.get("credentials")
    contract_path = finance_root / "credential-bindings.json"
    if not isinstance(credentials, dict):
        raise ValueError("finance credential manifest is invalid")
    if set(credentials) != {"placeholders", "binding_contract", "values_included", "forbidden_fields"}:
        raise ValueError("finance credential manifest is invalid")
    if credentials.get("forbidden_fields") != CANONICAL_FORBIDDEN_FIELDS:
        raise ValueError("finance credential forbidden fields are invalid")
    placeholders = credentials.get("placeholders")
    if not isinstance(placeholders, list) or not placeholders:
        raise ValueError("finance credential binding identities are invalid")
    for row in placeholders:
        if (
            not isinstance(row, dict)
            or set(row) != PLACEHOLDER_FIELDS
            or not isinstance(row["name"], str)
            or not row["name"]
            or not isinstance(row["binding"], str)
            or not re.fullmatch(r"BIND_[A-Z0-9_]+", row["binding"])
            or not isinstance(row["type"], str)
            or not row["type"]
        ):
            raise ValueError("finance credential placeholder is invalid")
    declared_types = {row["binding"]: row["type"] for row in placeholders}
    if len(declared_types) != len(placeholders):
        raise ValueError("finance credential bindings are duplicated")
    binding_contract = credentials.get("binding_contract")
    if not isinstance(binding_contract, dict) or set(binding_contract) != {"path", "sha256"}:
        raise ValueError("finance credential binding declaration is invalid")
    if binding_contract["path"] != "integrations/n8n/credential-bindings.json":
        raise ValueError("finance credential binding declaration path changed")
    if binding_contract["sha256"] != _sha256(contract_path):
        raise ValueError("finance credential binding declaration is stale")
    contract = _load(contract_path)
    contract_bindings = contract.get("bindings") if isinstance(contract, dict) else None
    if not isinstance(contract_bindings, list) or not isinstance(placeholders, list):
        raise ValueError("finance credential binding identities are invalid")
    contract_types: dict[str, str] = {}
    for row in contract_bindings:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("placeholder"), str)
            or not isinstance(row.get("credential_type"), str)
        ):
            raise ValueError("finance credential binding identities are invalid")
        contract_types[row["placeholder"]] = row["credential_type"]
    if len(contract_types) != len(contract_bindings):
        raise ValueError("finance credential bindings are duplicated")
    if contract_types != declared_types or len(contract_types) != len(placeholders):
        raise ValueError("finance credential binding identities are out of sync")
    if credentials.get("values_included") is not False:
        raise ValueError("finance credential values are forbidden")
    return {
        "placeholders": placeholders,
        "binding_contract": {
            "path": "credential-bindings.json",
            "sha256": _sha256(contract_path),
        },
        "values_included": False,
        "forbidden_fields": CANONICAL_FORBIDDEN_FIELDS,
    }


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
    source_tables = _load(finance_root / "data-tables.json").get("tables")
    source_names = [row.get("name") for row in source_tables or [] if isinstance(row, dict)]
    if (
        not isinstance(source_tables, list)
        or len(source_names) != len(source_tables)
        or len(set(source_names)) != len(source_names)
    ):
        raise ValueError("finance Data Table schema does not match the application contract")
    tables = _target_tables(finance_root)
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

    credentials = _credential_manifest(finance_root)

    for workflow in workflows:
        _copy_regular(workflow, destination / "workflows" / workflow.name)
    for key, relative_path in SOURCE_FILES.items():
        _copy_regular(source_root / relative_path, destination / {
            "folders": "workflow-folders.json",
            "folder_sql": "workflow-folder-placement.sql",
            "tables": "bootstrap/data-tables.json",
            "fixtures": "fixtures/fixture-manifest.json",
            "onedrive_setup": "fixtures/onedrive-root-setup.json",
            "credential_bindings": "credential-bindings.json",
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
        "credentials": credentials,
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
