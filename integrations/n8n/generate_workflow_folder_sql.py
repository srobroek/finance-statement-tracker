"""Render workflow SQL and organizer pins from the reviewed current source."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
N8N = ROOT / "integrations" / "n8n"
CONTRACT = N8N / "workflow-folders.json"
PLACEMENT_TEMPLATE = N8N / "workflow-folder-placement.sql.template"
PLACEMENT_OUTPUT = N8N / "workflow-folder-placement.sql"
CUTOVER_TEMPLATE = N8N / "workflow-organization-cutover.sql.template"
CUTOVER_OUTPUT = N8N / "workflow-organization-cutover.sql"
ORGANIZER = N8N / "organize-workflows.py"
CANONICAL_EXPORT_RELATIVE_PATH = (
    "integrations/n8n/workflows/22-shared-monthly-statement-cycle.json"
)
CANONICAL_REPLACEMENT_ID = "10000000-0000-4000-8000-000000000024"
ORPHAN_WORKFLOW_ID = "10000000-0000-4000-8000-000000000115"
PIN_LENGTHS = {"CANONICAL_EXPORT_SHA256": 64, "CANONICAL_PERSISTED_BODY_MD5": 32}

NUMBER_WORDS = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
    20: "twenty",
}


class ContractError(ValueError):
    """Raised when the canonical contract cannot produce a safe SQL script."""


def sql_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_nullable(value: str | None) -> str:
    return "NULL" if value is None else sql_text(value)


def load_contract() -> dict[str, Any]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    folders = contract.get("folders")
    workflows = contract.get("workflows")
    tag_definitions = contract.get("tag_definitions")
    workflow_tags = contract.get("workflow_tags")
    legacy_folder_ids = contract.get("legacy_folder_ids")
    if not all(isinstance(value, list) and value for value in (
        folders, workflows, tag_definitions, workflow_tags, legacy_folder_ids
    )):
        raise ContractError("workflow-folders.json has an incomplete contract")
    folder_ids = {folder.get("id") for folder in folders}
    if len(folder_ids) != len(folders) or None in folder_ids:
        raise ContractError("folder IDs must be unique")
    workflow_ids = {workflow.get("id") for workflow in workflows}
    if len(workflow_ids) != len(workflows) or None in workflow_ids:
        raise ContractError("workflow IDs must be unique")
    tag_ids = {tag.get("id") for tag in tag_definitions}
    tag_names = {tag.get("name") for tag in tag_definitions}
    if len(tag_ids) != len(tag_definitions) or len(tag_names) != len(tag_definitions):
        raise ContractError("tag IDs and names must be unique")
    if not set(workflow_tags) <= tag_names:
        raise ContractError("workflow_tags must reference tag_definitions")
    if any(workflow.get("folder_id") not in folder_ids for workflow in workflows):
        raise ContractError("every workflow must reference a canonical folder")
    if any(folder.get("parentFolderId") not in folder_ids
           for folder in folders if folder.get("parentFolderId") is not None):
        raise ContractError("every folder parent must be canonical")
    if len(folders) != 6 or len(workflows) != 19:
        raise ContractError("canonical placement must define six folders and 19 workflows")
    if sum(bool(folder.get("root")) for folder in folders) != 2:
        raise ContractError("canonical placement must define two root folders")
    return contract


def rows_folder_contract(folders: list[dict[str, Any]]) -> str:
    return ",\n".join(
        "  ({}, {}, {}, {})".format(
            sql_text(folder["id"]),
            sql_text(folder["name"]),
            sql_nullable(folder.get("parentFolderId")),
            "TRUE" if folder.get("root") else "FALSE",
        )
        for folder in folders
    )


def rows_workflow_contract(workflows: list[dict[str, Any]]) -> str:
    return ",\n".join(
        "  ({}, {}, {}, {})".format(
            sql_text(workflow["id"]),
            sql_text(workflow["current_name"]),
            sql_text(workflow["target_name"]),
            sql_text(workflow["folder_id"]),
        )
        for workflow in workflows
    )


def rows_workflow_folder_contract(workflows: list[dict[str, Any]]) -> str:
    return ",\n".join(
        f"  ({sql_text(workflow['id'])}, {sql_text(workflow['folder_id'])})"
        for workflow in workflows
    )


def rows_tag_contract(tags: list[dict[str, str]]) -> str:
    return ",\n".join(
        f"  ({sql_text(tag['id'])}, {sql_text(tag['name'])}, NOW(), NOW())"
        for tag in tags
    )


def rows_tag_tuples(tags: list[dict[str, str]]) -> str:
    return ",\n".join(
        f"      ({sql_text(tag['id'])}, {sql_text(tag['name'])})"
        for tag in tags
    )


def rows_folder_insert(folders: list[dict[str, Any]], project_id_variable: str) -> str:
    return ",\n".join(
        "  ({}, {}, :'{}', {}, NOW(), NOW())".format(
            sql_text(folder["id"]),
            sql_text(folder["name"]),
            project_id_variable,
            sql_nullable(folder.get("parentFolderId")),
        )
        for folder in folders
    )


def tag_by_name(tags: list[dict[str, str]]) -> dict[str, str]:
    return {tag["name"]: tag["id"] for tag in tags}


def tag_edge_guards(
    tags: list[dict[str, str]], workflow_tags: list[str], workflow_count: int
) -> str:
    active_count = 1
    lines = []
    for index, tag in enumerate(tags):
        expected = (
            active_count
            if tag["name"] == "active"
            else workflow_count - active_count
            if tag["name"] == "inactive"
            else workflow_count
            if tag["name"] in workflow_tags
            else 0
        )
        prefix = "  IF" if index == 0 else "     OR"
        lines.append(
            f'{prefix} (SELECT COUNT(*) FROM workflows_tags wt JOIN '
            f'finance_workflow_contract c ON c.workflow_id = wt."workflowId" '
            f'WHERE wt."tagId" = {sql_text(tag["id"])}) <> {expected}'
        )
    return "\n".join(lines)


def template_values(contract: dict[str, Any]) -> dict[str, str]:
    folders = contract["folders"]
    workflows = contract["workflows"]
    tags = contract["tag_definitions"]
    workflow_tags = contract["workflow_tags"]
    tag_ids = tag_by_name(tags)
    workflow_count = len(workflows)
    root_folders = [folder for folder in folders if folder.get("root")]
    child_folders = [folder for folder in folders if not folder.get("root")]
    active_tag_id = tag_ids.get("active")
    inactive_tag_id = tag_ids.get("inactive")
    if active_tag_id is None or inactive_tag_id is None:
        raise ContractError("canonical contract must define active and inactive tags")
    values = {
        "FOLDER_CONTRACT_ROWS": rows_folder_contract(folders),
        "WORKFLOW_CONTRACT_ROWS": rows_workflow_contract(workflows),
        "WORKFLOW_FOLDER_ROWS": rows_workflow_folder_contract(workflows),
        "TAG_CONTRACT_ROWS": rows_tag_contract(tags),
        "TAG_CONTRACT_TUPLES": rows_tag_tuples(tags),
        "TAG_CONTRACT_IDS": ", ".join(sql_text(tag["id"]) for tag in tags),
        "FOLDER_ROOT_ROWS": rows_folder_insert(root_folders, "finance_project_id"),
        "FOLDER_CHILD_ROWS": rows_folder_insert(child_folders, "finance_project_id"),
        "APPLICATION_FOLDER_ROOT_ROWS": rows_folder_insert(
            root_folders, "application_project_id"
        ),
        "APPLICATION_FOLDER_CHILD_ROWS": rows_folder_insert(
            child_folders, "application_project_id"
        ),
        "LEGACY_FOLDER_IDS": ", ".join(sql_text(value) for value in contract["legacy_folder_ids"]),
        "WORKFLOW_TAG_ROWS": ", ".join(
            f"({sql_text(tag_ids[name])})"
            for name in workflow_tags
            if name not in {"active", "inactive"}
        ),
        "ACTIVE_TAG_ID": active_tag_id,
        "INACTIVE_TAG_ID": inactive_tag_id,
        "FOLDER_COUNT": str(len(folders)),
        "FOLDER_COUNT_WORD": NUMBER_WORDS.get(len(folders), str(len(folders))),
        "ROOT_FOLDER_COUNT": str(len(root_folders)),
        "CHILD_FOLDER_COUNT": str(len(child_folders)),
        "WORKFLOW_COUNT": str(workflow_count),
        "TAG_EDGE_READBACK_GUARDS": tag_edge_guards(tags, workflow_tags, workflow_count),
    }
    return values


def organizer_pin_assignments(source: str) -> dict[str, ast.Assign]:
    """Read pinned literals without importing the import-time validating consumer."""
    assignments = {}
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign):
            continue
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        for name in set(names) & PIN_LENGTHS.keys():
            if (
                name in assignments
                or len(node.targets) != 1
                or not isinstance(node.value, ast.Constant)
                or not isinstance(node.value.value, str)
                or re.fullmatch(r"[0-9a-f]{" + str(PIN_LENGTHS[name]) + "}", node.value.value) is None
            ):
                raise ContractError(f"CANONICAL_PIN_DECLARATION_INVALID:{name}")
            assignments[name] = node
    if set(assignments) != PIN_LENGTHS.keys():
        raise ContractError("CANONICAL_PIN_DECLARATIONS_MISSING")
    return assignments


def postgres_jsonb_text(value: Any) -> str:
    """Match the organizer rehearsal and PostgreSQL's persisted-body expression."""
    if isinstance(value, dict):
        keys = sorted(value, key=lambda key: (len(key.encode("utf-8")), key.encode("utf-8")))
        return "{" + ", ".join(
            f"{json.dumps(key, ensure_ascii=False)}: {postgres_jsonb_text(value[key])}"
            for key in keys
        ) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(postgres_jsonb_text(child) for child in value) + "]"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def canonical_source_pins(
    contract: dict[str, Any], expected_source_sha256: str
) -> dict[str, str]:
    if re.fullmatch(r"[0-9a-f]{64}", expected_source_sha256) is None:
        raise ContractError("EXPECTED_CANONICAL_EXPORT_SHA256_INVALID")
    raw = (ROOT / CANONICAL_EXPORT_RELATIVE_PATH).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_source_sha256:
        raise ContractError(
            "CANONICAL_EXPORT_HASH_MISMATCH: refresh requires an externally reviewed "
            "--expected-canonical-export-sha256"
        )
    workflow = json.loads(raw)
    if not isinstance(workflow, dict):
        raise ContractError("CANONICAL_EXPORT_NOT_OBJECT")
    if workflow.get("id") != CANONICAL_REPLACEMENT_ID:
        raise ContractError("CANONICAL_EXPORT_ID_MISMATCH")
    if workflow.get("name") != "Finance · Shared Monthly Statement Cycle":
        raise ContractError("CANONICAL_EXPORT_NAME_MISMATCH")
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list) or len(nodes) != 16:
        raise ContractError("CANONICAL_EXPORT_NODE_COUNT_MISMATCH")
    meta = workflow.get("meta")
    if not isinstance(meta, dict) or meta.get("financeWorkflowCode") != "SHARED_MONTHLY_STATEMENT_CYCLE":
        raise ContractError("CANONICAL_EXPORT_CODE_MISMATCH")
    roster = {row["id"]: row for row in contract["workflows"]}
    if CANONICAL_REPLACEMENT_ID not in roster or ORPHAN_WORKFLOW_ID in roster:
        raise ContractError("CANONICAL_REPLACEMENT_ROSTER_MISMATCH")
    row = roster[CANONICAL_REPLACEMENT_ID]
    if row.get("source") != CANONICAL_EXPORT_RELATIVE_PATH:
        raise ContractError("CANONICAL_EXPORT_SOURCE_MISMATCH")
    if row.get("code") != meta["financeWorkflowCode"]:
        raise ContractError("CANONICAL_EXPORT_MAP_CODE_MISMATCH")
    if (
        row.get("current_name") != workflow["name"]
        or row.get("target_name") != "Shared Monthly Statement Cycle"
    ):
        raise ContractError("CANONICAL_EXPORT_MAP_NAME_MISMATCH")
    body = {
        field: workflow.get(field)
        for field in ("id", "nodes", "connections", "settings", "pinData", "meta")
    }
    return {
        "CANONICAL_EXPORT_SHA256": expected_source_sha256,
        "CANONICAL_PERSISTED_BODY_MD5": hashlib.md5(
            postgres_jsonb_text(body).encode("utf-8"), usedforsecurity=False
        ).hexdigest(),
    }


def render_template(template: Path, values: dict[str, str]) -> str:
    rendered = template.read_text(encoding="utf-8")
    if template == CUTOVER_TEMPLATE:
        for name in PIN_LENGTHS:
            if rendered.count("{{" + name + "}}") != 1:
                raise ContractError(f"CANONICAL_PIN_TEMPLATE_INVALID:{name}")
    for name, value in values.items():
        rendered = rendered.replace("{{" + name + "}}", value)
    if "{{" in rendered:
        raise ContractError("template has unresolved markers")
    return rendered.replace("\r\n", "\n")


def render_outputs(expected_canonical_export_sha256: str | None = None) -> dict[Path, str]:
    contract = load_contract()
    organizer_source = ORGANIZER.read_text(encoding="utf-8")
    assignments = organizer_pin_assignments(organizer_source)
    expected_source = (
        assignments["CANONICAL_EXPORT_SHA256"].value.value
        if expected_canonical_export_sha256 is None
        else expected_canonical_export_sha256
    )
    pins = canonical_source_pins(contract, expected_source)
    if (
        expected_canonical_export_sha256 is None
        and pins["CANONICAL_PERSISTED_BODY_MD5"]
        != assignments["CANONICAL_PERSISTED_BODY_MD5"].value.value
    ):
        raise ContractError("CANONICAL_EXPORT_BODY_DIGEST_MISMATCH")
    rendered_organizer = organizer_source.encode("utf-8")
    source_lines = rendered_organizer.splitlines(keepends=True)
    for name, node in sorted(
        assignments.items(),
        key=lambda item: (item[1].value.lineno, item[1].value.col_offset),
        reverse=True,
    ):
        value = node.value
        start = sum(map(len, source_lines[:value.lineno - 1])) + value.col_offset
        end = sum(map(len, source_lines[:value.end_lineno - 1])) + value.end_col_offset
        rendered_organizer = (
            rendered_organizer[:start] + json.dumps(pins[name]).encode("utf-8")
            + rendered_organizer[end:]
        )
    values = template_values(contract)
    values.update(pins)
    return {
        PLACEMENT_OUTPUT: render_template(PLACEMENT_TEMPLATE, values),
        CUTOVER_OUTPUT: render_template(CUTOVER_TEMPLATE, values),
        ORGANIZER: rendered_organizer.decode("utf-8"),
    }


def write_outputs(rendered_outputs: dict[Path, str]) -> None:
    """Stage the whole output closure and roll back replacements on write failure."""
    with TemporaryDirectory(prefix=".workflow-organization-", dir=N8N) as directory:
        staged = {}
        backups = {}
        for index, (output, rendered) in enumerate(rendered_outputs.items()):
            temporary = Path(directory) / f"{index}.new"
            temporary.write_text(rendered, encoding="utf-8", newline="\n")
            staged[output] = temporary
            if output.exists():
                backup = Path(directory) / f"{index}.old"
                backup.write_bytes(output.read_bytes())
                mode = output.stat().st_mode & 0o7777
                backup.chmod(mode)
                temporary.chmod(mode)
                backups[output] = backup
            else:
                temporary.chmod(0o644)
                backups[output] = None
        replaced = []
        try:
            for output, temporary in staged.items():
                temporary.replace(output)
                replaced.append(output)
        except BaseException:
            for output in reversed(replaced):
                backup = backups[output]
                if backup is None:
                    output.unlink()
                else:
                    backup.replace(output)
            raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="write generated SQL and organizer pins")
    mode.add_argument("--check", action="store_true", help="verify generated SQL and organizer pins")
    parser.add_argument(
        "--expected-canonical-export-sha256",
        help="externally reviewed raw W22 SHA256; omit to enforce the existing pins",
    )
    args = parser.parse_args(argv)
    try:
        rendered_outputs = render_outputs(args.expected_canonical_export_sha256)
    except ContractError as exc:
        raise SystemExit(str(exc)) from exc
    if args.write:
        write_outputs(rendered_outputs)
        return 0
    drifted = [
        output.relative_to(ROOT)
        for output, rendered in rendered_outputs.items()
        if not output.exists() or output.read_text(encoding="utf-8") != rendered
    ]
    if drifted:
        raise SystemExit(
            "generated workflow organization drift: " + ", ".join(str(path) for path in drifted)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
