#!/usr/bin/env python3
"""Classify workflow Data Table references before a live four-table cutover.

This is deliberately a static feasibility check.  It prevents a migration
runner from treating a selector rewrite as a semantic workflow migration.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "integrations" / "n8n" / "data-table-migration-matrix.json"


def _column_map(table: dict[str, Any]) -> dict[str, str | None]:
    return {
        column["source_column"]: column.get("target_field")
        for column in table.get("columns", [])
        if column.get("target_artifact") is None
    }


def analyze(matrix: dict[str, Any]) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    for table in matrix.get("tables", []):
        source = table["source_table"]
        target = table.get("target_table")
        columns = _column_map(table)
        identity = next(
            (
                item
                for item in matrix.get("target_schemas", {}).get(target, {}).get("identity_derivations", [])
                if item.get("source_table") == source
            ),
            None,
        )
        for reference in table.get("node_references", []):
            # Provisioning creates are already idempotent side-by-side actions;
            # they are not runtime selectors that a cutover would rewrite.
            if reference.get("operation") == "create":
                continue
            reasons: list[str] = []
            if target is None:
                reasons.append("NO_CANONICAL_TABLE")
            mapped_fields = sorted(set(reference.get("write_columns", [])) | set(reference.get("filter_keys", [])))
            missing = [field for field in mapped_fields if not columns.get(field)]
            renamed = {
                field: columns[field]
                for field in mapped_fields
                if columns.get(field) and columns[field] != field
            }
            if missing:
                reasons.append("FIELD_HAS_NO_CANONICAL_COLUMN")
            if renamed:
                reasons.append("FIELD_RENAME_REQUIRES_WORKFLOW_ADAPTER")
            if reference.get("operation") == "get" and any(
                target_field and source_field != target_field
                for source_field, target_field in columns.items()
            ):
                reasons.append("READ_SHAPE_REQUIRES_WORKFLOW_ADAPTER")
            if identity and identity.get("strategy") != "direct":
                reasons.append("IDENTITY_DERIVATION_REQUIRES_WORKFLOW_ADAPTER")
            if identity and reference.get("operation") in {"insert", "upsert", "update"}:
                target_key = set(identity.get("target_key", []))
                produced = {columns.get(field) for field in reference.get("write_columns", [])}
                if not target_key <= produced:
                    reasons.append("TARGET_LOGICAL_KEY_NOT_WRITTEN")
            actions.append(
                {
                    "source_table": source,
                    "target_table": target,
                    "workflow": reference["file"],
                    "node": reference["node"],
                    "operation": reference["operation"],
                    "selector_only_safe": not reasons,
                    "field_renames": renamed,
                    "unmapped_fields": missing,
                    "blockers": sorted(set(reasons)),
                }
            )
    unsafe = [action for action in actions if not action["selector_only_safe"]]
    return {
        "schema_version": "finance-data-table-cutover-feasibility-v1",
        "cutover_ready": not unsafe,
        "reference_count": len(actions),
        "selector_only_safe_count": len(actions) - len(unsafe),
        "semantic_adapter_required_count": len(unsafe),
        "actions": actions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=MATRIX)
    parser.add_argument("--assert-selector-only-safe", action="store_true")
    args = parser.parse_args()
    report = analyze(json.loads(args.matrix.read_text(encoding="utf-8")))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if args.assert_selector_only_safe and not report["cutover_ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
