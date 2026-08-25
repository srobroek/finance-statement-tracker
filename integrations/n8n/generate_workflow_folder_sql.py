"""Render the workflow placement SQL from the canonical folder contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
N8N = ROOT / "integrations" / "n8n"
CONTRACT = N8N / "workflow-folders.json"
PLACEMENT_TEMPLATE = N8N / "workflow-folder-placement.sql.template"
PLACEMENT_OUTPUT = N8N / "workflow-folder-placement.sql"
CUTOVER_TEMPLATE = N8N / "workflow-organization-cutover.sql.template"
CUTOVER_OUTPUT = N8N / "workflow-organization-cutover.sql"

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


def render_template(template: Path, values: dict[str, str]) -> str:
    rendered = template.read_text(encoding="utf-8")
    for name, value in values.items():
        rendered = rendered.replace("{{" + name + "}}", value)
    if "{{" in rendered:
        raise ContractError("template has unresolved markers")
    return rendered.replace("\r\n", "\n")


def render_outputs() -> dict[Path, str]:
    values = template_values(load_contract())
    return {
        PLACEMENT_OUTPUT: render_template(PLACEMENT_TEMPLATE, values),
        CUTOVER_OUTPUT: render_template(CUTOVER_TEMPLATE, values),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="write generated SQL")
    mode.add_argument("--check", action="store_true", help="verify generated SQL")
    args = parser.parse_args()
    rendered_outputs = render_outputs()
    if args.write:
        for output, rendered in rendered_outputs.items():
            output.write_text(rendered, encoding="utf-8", newline="\n")
        return 0
    drifted = [
        output.relative_to(ROOT)
        for output, rendered in rendered_outputs.items()
        if not output.exists() or output.read_text(encoding="utf-8") != rendered
    ]
    if drifted:
        raise SystemExit(
            "generated workflow SQL drift: " + ", ".join(str(path) for path in drifted)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
