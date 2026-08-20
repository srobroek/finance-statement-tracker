"""Validate ownership and safety of n8n Set/Edit Fields parameter nodes."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = Path(__file__).with_name("workflow-parameter-ownership.json")
SCHEMA_PATH = Path(__file__).with_name("workflow-parameter-ownership.schema.json")
EXPRESSION_PREFIX = "={{"
CALLER_EXPRESSION = re.compile(r"(?:\$json|\$input|\$fromAI\b)")
MIN_SHARED_LITERAL_LENGTH = 4


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _selector_values(document: Any, selector: str) -> list[Any]:
    """Resolve a small deterministic dot selector with ``*`` fan-out."""

    values: list[Any] = [document]
    for part in selector.split("."):
        next_values: list[Any] = []
        for value in values:
            if part == "*":
                if isinstance(value, dict):
                    next_values.extend(value.values())
                elif isinstance(value, list):
                    next_values.extend(value)
                continue
            if isinstance(value, dict) and part in value:
                next_values.append(value[part])
        values = next_values
    return values


def _scalar_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if len(value) >= MIN_SHARED_LITERAL_LENGTH:
            yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _scalar_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _scalar_strings(child)


def _mapping_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str):
                yield key
            yield from _mapping_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _mapping_keys(child)


def _is_expression(value: Any) -> bool:
    return isinstance(value, str) and value.lstrip().startswith(EXPRESSION_PREFIX)


def _is_protected_field(field: str, protected_names: set[str]) -> bool:
    parts = set(re.split(r"[^a-z0-9]+", field.casefold()))
    return field.casefold() in protected_names or bool(parts & protected_names)


def _finding(
    code: str,
    *,
    workflow: str = "",
    node: str = "",
    field: str = "",
    detail: str = "",
) -> dict[str, str]:
    return {
        "code": code,
        "workflow": workflow,
        "node": node,
        "field": field,
        "detail": detail,
    }


def _field_type(assignment: dict[str, Any]) -> str | None:
    value = assignment.get("value")
    if _is_expression(value):
        assignment_type = assignment.get("type")
        return assignment_type if isinstance(assignment_type, str) else None
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return None


def _parameter_assignments(node: dict[str, Any]) -> list[dict[str, Any]]:
    parameters = node.get("parameters")
    if not isinstance(parameters, dict):
        return []
    assignments = parameters.get("assignments")
    if isinstance(assignments, dict) and isinstance(assignments.get("assignments"), list):
        return [row for row in assignments["assignments"] if isinstance(row, dict)]
    values = parameters.get("values")
    if isinstance(values, list):
        return [row for row in values if isinstance(row, dict)]
    if isinstance(values, dict):
        return [
            {"name": name, "type": _field_type({"value": value}), "value": value}
            for name, value in values.items()
        ]
    return []


def _workflow_documents(
    root: Path, contract: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    paths: set[Path] = set()
    for pattern in contract["workflow_globs"]:
        paths.update(root.glob(pattern))
    documents: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(paths):
        if path.is_file() and path.suffix == ".json":
            relative = path.relative_to(root).as_posix()
            document = load_json(path)
            if isinstance(document, dict) and isinstance(document.get("nodes"), list):
                documents.append((Path(relative).name, document))
    return documents


def _global_values(root: Path, contract: dict[str, Any]) -> tuple[set[str], list[dict[str, str]]]:
    values: set[str] = set()
    findings: list[dict[str, str]] = []
    for source in contract["global_contract_sources"]:
        path = root / source["path"]
        try:
            document = load_json(path)
        except (OSError, json.JSONDecodeError) as error:
            findings.append(
                _finding(
                    "GLOBAL_SOURCE_UNREADABLE",
                    detail=f"{source['path']}: {error.__class__.__name__}",
                )
            )
            continue
        for selector in source["selectors"]:
            for selected in _selector_values(document, selector):
                values.update(_scalar_strings(selected))
    return values, findings


def _validate_schema(root: Path, contract: dict[str, Any]) -> list[dict[str, str]]:
    try:
        schema = load_json(root / SCHEMA_PATH.relative_to(ROOT))
    except (OSError, json.JSONDecodeError) as error:
        return [_finding("OWNERSHIP_SCHEMA_UNREADABLE", detail=error.__class__.__name__)]
    errors = sorted(Draft202012Validator(schema).iter_errors(contract), key=str)
    if not errors:
        return []
    return [_finding("OWNERSHIP_SCHEMA_INVALID", detail="; ".join(error.message for error in errors))]


def scan(
    *,
    root: Path = ROOT,
    contract: dict[str, Any] | None = None,
    documents: list[tuple[str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Return a deterministic inventory and fail-closed ownership findings."""

    ownership = contract if contract is not None else load_json(root / CONTRACT_PATH.relative_to(ROOT))
    findings = _validate_schema(root, ownership)
    global_values, source_findings = _global_values(root, ownership)
    findings.extend(source_findings)
    docs = documents if documents is not None else _workflow_documents(root, ownership)
    discovered_names = {name for name, _ in docs}
    expected_names = set(ownership["workflows"])
    for missing in sorted(expected_names - discovered_names):
        findings.append(_finding("WORKFLOW_FILE_MISSING", workflow=missing, detail="allowlist entry has no export"))

    parameter_nodes: list[dict[str, Any]] = []
    local_literals: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    parameter_types = set(ownership["parameter_node_types"])
    forbidden = ownership["forbidden"]
    key_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in forbidden["credential_key_patterns"]]
    value_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in forbidden["secret_value_patterns"]]
    protected_names = {name.casefold() for name in forbidden["protected_field_names"]}

    for workflow, document in sorted(docs, key=lambda row: row[0]):
        if not isinstance(document, dict) or not isinstance(document.get("nodes"), list):
            continue
        workflow_spec = ownership["workflows"].get(workflow, {})
        node_specs = workflow_spec.get("nodes", {})
        seen_nodes: set[str] = set()
        for node in document["nodes"]:
            if not isinstance(node, dict) or node.get("type") not in parameter_types:
                continue
            node_name = str(node.get("name", ""))
            seen_nodes.add(node_name)
            spec = node_specs.get(node_name)
            assignments = _parameter_assignments(node)
            fields = spec.get("fields", {}) if isinstance(spec, dict) else {}
            inventory = {
                "workflow": workflow,
                "node": node_name,
                "type": node.get("type", ""),
                "fields": [],
            }
            parameter_nodes.append(inventory)
            if node.get("credentials"):
                findings.append(_finding("CREDENTIAL_BINDING_ON_PARAMETER_NODE", workflow=workflow, node=node_name, detail="parameter nodes cannot own n8n credential bindings"))
            if spec is None:
                findings.append(_finding("PARAMETER_NODE_UNALLOWLISTED", workflow=workflow, node=node_name, detail="Set/Edit Fields node is not in the ownership contract"))
            assignment_names: set[str] = set()
            for assignment in assignments:
                field = assignment.get("name")
                if not isinstance(field, str) or not field:
                    findings.append(_finding("PARAMETER_FIELD_NAME_INVALID", workflow=workflow, node=node_name, detail="assignment name is missing"))
                    continue
                assignment_names.add(field)
                field_spec = fields.get(field)
                category = field_spec.get("category") if isinstance(field_spec, dict) else ""
                inventory["fields"].append({"name": field, "category": category, "type": assignment.get("type", "")})
                value = assignment.get("value")
                nested_keys = list(_mapping_keys(value))
                if any(pattern.search(candidate) for pattern in key_patterns for candidate in [field, *nested_keys]):
                    findings.append(_finding("CREDENTIAL_OR_SECRET_FIELD", workflow=workflow, node=node_name, field=field, detail="credential or secret-shaped field name"))
                if any(pattern.search(candidate) for pattern in value_patterns for candidate in _scalar_strings(value)):
                    findings.append(_finding("SECRET_VALUE_IN_PARAMETER", workflow=workflow, node=node_name, field=field, detail="secret-shaped literal"))
                if field_spec is None:
                    findings.append(_finding("PARAMETER_FIELD_UNALLOWLISTED", workflow=workflow, node=node_name, field=field, detail="field is not in the ownership contract"))
                    continue
                expected_type = field_spec["type"]
                actual_type = _field_type(assignment)
                if actual_type != expected_type:
                    findings.append(_finding("PARAMETER_TYPE_MISMATCH", workflow=workflow, node=node_name, field=field, detail=f"expected {expected_type}, observed {actual_type or 'unknown'}"))
                expression = _is_expression(value)
                if expression and not field_spec.get("expression_allowed", False):
                    findings.append(_finding("UNDECLARED_INPUT_EXPRESSION", workflow=workflow, node=node_name, field=field, detail="expression is not allowlisted for this field"))
                if category == "global_generated_contract":
                    source = field_spec.get("source")
                    if expression:
                        findings.append(_finding("GLOBAL_CALLER_EXPRESSION", workflow=workflow, node=node_name, field=field, detail="global contract values must be fixed generated literals"))
                    elif not isinstance(source, dict):
                        findings.append(_finding("GLOBAL_SOURCE_MISSING", workflow=workflow, node=node_name, field=field, detail="global field has no source selector"))
                    else:
                        source_path = root / source["path"]
                        try:
                            source_document = load_json(source_path)
                            allowed = _selector_values(source_document, source["selector"])
                        except (OSError, json.JSONDecodeError) as error:
                            allowed = []
                            findings.append(_finding("GLOBAL_SOURCE_UNREADABLE", workflow=workflow, node=node_name, field=field, detail=f"{source['path']}: {error.__class__.__name__}"))
                        if value not in allowed:
                            findings.append(_finding("GLOBAL_VALUE_MISMATCH", workflow=workflow, node=node_name, field=field, detail=f"value is not present at {source['path']}::{source['selector']}"))
                if category == "workflow_local_input" and expression and _is_protected_field(field, protected_names) and isinstance(value, str) and CALLER_EXPRESSION.search(value):
                    findings.append(_finding("PROTECTED_CALLER_INPUT", workflow=workflow, node=node_name, field=field, detail="caller expression targets a protected field"))
                if category in {"workflow_local_input", "workflow_local_constant"} and isinstance(value, str) and not expression and len(value) >= MIN_SHARED_LITERAL_LENGTH:
                    if value in global_values:
                        findings.append(_finding("SHARED_LITERAL_COPIED", workflow=workflow, node=node_name, field=field, detail="literal belongs to a generated global contract; resolve it through the contract"))
                    local_literals[value].append({
                        "workflow": workflow,
                        "node": node_name,
                        "field": field,
                        "duplicate_literal_allowed": bool(field_spec.get("duplicate_literal_allowed", False)),
                    })
            if spec is not None:
                for missing_field in sorted(set(fields) - assignment_names):
                    findings.append(_finding("PARAMETER_FIELD_MISSING", workflow=workflow, node=node_name, field=missing_field, detail="allowlisted field is absent from export"))
        for missing_node in sorted(set(node_specs) - seen_nodes):
            findings.append(_finding("PARAMETER_NODE_MISSING", workflow=workflow, node=missing_node, detail="allowlisted node is absent from export"))

    duplicate_literals: list[dict[str, Any]] = []
    for literal, locations in sorted(local_literals.items()):
        if len(locations) > 1:
            allowed = all(location["duplicate_literal_allowed"] for location in locations)
            duplicate_literals.append({"literal": literal, "allowed": allowed, "locations": locations})
            if allowed:
                continue
            detail = json.dumps(locations, sort_keys=True, separators=(",", ":"))
            for location in locations:
                findings.append(_finding("SHARED_LITERAL_COPIED", field=location["field"], workflow=location["workflow"], node=location["node"], detail=f"literal is repeated in workflow-local fields: {detail}"))

    for inventory in parameter_nodes:
        inventory["fields"].sort(key=lambda row: row["name"])
    findings.sort(key=lambda row: (row["code"], row["workflow"], row["node"], row["field"], row["detail"]))
    parameter_nodes.sort(key=lambda row: (row["workflow"], row["node"]))
    return {
        "schema_version": 1,
        "status": "PASS" if not findings else "FAIL",
        "parameter_nodes": parameter_nodes,
        "counts": {
            "workflows": len(docs),
            "parameter_nodes": len(parameter_nodes),
            "findings": len(findings),
        },
        "duplicate_literals": duplicate_literals,
        "findings": findings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="return non-zero when ownership findings exist")
    args = parser.parse_args(argv)
    report = scan()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
