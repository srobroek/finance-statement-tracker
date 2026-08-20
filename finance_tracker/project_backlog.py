"""Deterministically merge transcript requirements with implementation evidence."""

from __future__ import annotations

import json
import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT_PATH = ROOT / "docs" / "project-audit" / "transcript-requirements-ledger-2026-08-19.json"
IMPLEMENTATION_PATH = ROOT / "docs" / "project-audit" / "implementation-status.json"
BACKLOG_PATH = ROOT / "config" / "project-backlog.json"
BACKLOG_DOC_PATH = ROOT / "docs" / "project-backlog.md"
PROGRESS_PATH = ROOT / "config" / "project-backlog-progress.json"

VALID_STATUSES = {
    "NOT_STARTED",
    "PARTIAL",
    "IMPLEMENTED_UNVERIFIED",
    "BLOCKED",
    "VERIFIED",
    "SUPERSEDED",
}

SUPERSEDED_EVIDENCE_MARKERS = (
    "35/36 passing",
    "350 passed, 1 failed",
    "14/16 passing",
    "14 passed, 2 failed",
    "DIRTY_N8N_REFACTOR",
    "FAILING_WORKTREE",
    "CURRENT_TEST_SUITE_FAILURES",
    "CURRENT_TEST_FAILURES",
    "CURRENT_TEST_FAILURE",
    "CURRENT_N8N_TEST_FAILURE",
    "UNCOMMITTED_REFACTOR",
    "The refactor is uncommitted",
    "Stabilize generated/bootstrap contracts",
    "Repair provider-envelope contract/test parity",
    "current contract bytes fail their local suite",
)

PREFIX_METADATA = {
    "N8N": ("n8n platform and workflows", "n8n_workflows"),
    "DOC": ("Outlook, OneDrive and document evidence", "document_evidence"),
    "AGENT": ("AI providers and agent execution", "agent_runtime"),
    "ACTUAL": ("Actual accounts, ledger, rules, budgets and reports", "actual_finance"),
    "CASHBACK": ("cashback companion", "cashback_companion"),
    "BROWSER": ("browser acquisition", "browser_acquisition"),
    "AUTO": ("scheduling and task lifecycle", "automation_cutover"),
    "PLATFORM": ("platform, security and deployment", "platform_operations"),
    "DOCS": ("documentation and public reusability", "documentation_acceptance"),
}

ACCEPTANCE_VALIDATORS = {
    "N8N": "n8n contract tests plus exact disposable execution receipt review",
    "DOC": "evidence tests plus immutable archive and readback receipt review",
    "AGENT": "runner contract tests plus isolated structured-output receipt review",
    "ACTUAL": "full Python suite plus disposable Actual and authenticated UI/API readback",
    "CASHBACK": "cashback regression suite plus live companion reconciliation receipt review",
    "BROWSER": "capture-schema tests plus immutable source and guarded-ingestion receipt review",
    "AUTO": "automation lifecycle tests plus target scheduler and legacy-task readback",
    "PLATFORM": "platform tests plus image, deploy, restart, security, and restore receipt review",
    "DOCS": "backlog generator check plus independent acceptance-evidence audit",
}

IMPLEMENTATION_LINKS = {
    "architecture.actual-authoritative-ledger": ["ACTUAL-001", "N8N-004", "CASHBACK-001"],
    "accounts.fab-non-credit": ["ACTUAL-002", "ACTUAL-003", "BROWSER-002"],
    "accounts.sarwa": ["ACTUAL-002", "ACTUAL-003", "ACTUAL-017", "BROWSER-003"],
    "accounts.adcb-closed-zero": ["ACTUAL-021"],
    "transactions.note-contract-v2": ["ACTUAL-011", "DOC-004"],
    "transactions.refund-reward-transfer-semantics": ["ACTUAL-010", "CASHBACK-014"],
    "transactions.classification-review-coverage": [
        "ACTUAL-007", "ACTUAL-008", "ACTUAL-009", "ACTUAL-012", "ACTUAL-020"
    ],
    "orchestration.n8n-greenfield": [
        "N8N-001", "N8N-002", "N8N-003", "N8N-004", "N8N-005", "N8N-006",
        "N8N-007", "N8N-008", "N8N-009", "AUTO-001", "AUTO-002"
    ],
    "orchestration.n8n-application-hierarchy": ["N8N-010"],
    "orchestration.n8n-data-table-minimization": ["N8N-011"],
    "orchestration.codex-agent-handoff": [
        "AGENT-001", "AGENT-002", "AGENT-003", "AGENT-004", "AGENT-005"
    ],
    "ingestion.browser": [
        "ACTUAL-004", "BROWSER-001", "BROWSER-002", "BROWSER-003", "BROWSER-004", "DOC-006"
    ],
    "ingestion.documents-and-evidence": [
        "DOC-001", "DOC-002", "DOC-003", "DOC-004", "DOC-005", "DOC-006", "DOC-007"
    ],
    "cashback.production": [
        "CASHBACK-001", "CASHBACK-002", "CASHBACK-003", "CASHBACK-004",
        "CASHBACK-005", "CASHBACK-006", "CASHBACK-007", "CASHBACK-008",
        "CASHBACK-009", "CASHBACK-010", "CASHBACK-011", "CASHBACK-012",
        "CASHBACK-013", "CASHBACK-014"
    ],
    "automation.schedules-and-task-archival": ["AUTO-001", "AUTO-002", "AUTO-003", "N8N-001"],
    "security.credentials": ["PLATFORM-003", "DOC-002", "AGENT-003"],
    "deployment.ci-containers-cloudflare": [
        "PLATFORM-001", "PLATFORM-002", "PLATFORM-004", "PLATFORM-005", "PLATFORM-006"
    ],
    "actual.budgets-schedules-dashboards-owners": [
        "ACTUAL-005", "ACTUAL-006", "ACTUAL-007", "ACTUAL-009", "ACTUAL-012",
        "ACTUAL-013", "ACTUAL-014", "ACTUAL-015", "ACTUAL-016", "ACTUAL-017",
        "ACTUAL-018", "ACTUAL-020"
    ],
    "verification.full-corpus-and-production-promotion": [
        "ACTUAL-003", "ACTUAL-019", "N8N-005", "N8N-006", "PLATFORM-006", "DOCS-002"
    ],
}

STATUS_OVERRIDES = {
    "ACTUAL-003": "BLOCKED",
    "ACTUAL-021": "BLOCKED",
    "ACTUAL-019": "BLOCKED",
    "AGENT-003": "PARTIAL",
    "N8N-001": "PARTIAL",
    "N8N-002": "PARTIAL",
    "N8N-003": "PARTIAL",
    "N8N-006": "PARTIAL",
    "N8N-008": "IMPLEMENTED_UNVERIFIED",
    "ACTUAL-002": "PARTIAL",
    "ACTUAL-010": "IMPLEMENTED_UNVERIFIED",
    "ACTUAL-011": "IMPLEMENTED_UNVERIFIED",
    "BROWSER-002": "IMPLEMENTED_UNVERIFIED",
    "PLATFORM-005": "IMPLEMENTED_UNVERIFIED",
    "CASHBACK-007": "IMPLEMENTED_UNVERIFIED",
    "CASHBACK-008": "IMPLEMENTED_UNVERIFIED",
    "CASHBACK-011": "IMPLEMENTED_UNVERIFIED",
    "DOCS-002": "IMPLEMENTED_UNVERIFIED",
}

LATEST_OVERRIDES = {
    "N8N-002": {
        "contradictions": [
            "Manual node-layout optimization was removed from scope; using n8n Tidy Workflow is acceptable.",
            "Readable node names, code, notes, section labels and folders remain required even though manual positioning does not."
        ],
        "next_action": (
            "Finish semantic wiring and readable names/notes/folders, use Tidy Workflow instead of hand-positioning, "
            "and verify the canvas is understandable after import."
        ),
    },
    "N8N-008": {
        "contradictions": [
            "For Execute Sub-workflow nodes, the selector should use 'From list' when the target workflow is available; fixed ID/manual selector is fallback only."
        ],
        "next_action": (
            "In disposable n8n, prove exact custom/community node registration and select subworkflows From list where available."
        ),
    },
    "AGENT-005": {
        "contradictions": [
            "The current pinned community-agent candidates are n8n-nodes-prodex@0.5.1 and "
            "@ggomez91npm/n8n-nodes-claude-code@0.8.0; both remain unapproved for production until disposable proof."
        ],
        "next_action": (
            "Build the exact pinned image, run registration/security/structured-output fixtures for ProDex 0.5.1 and "
            "ggomez Claude 0.8.0, and retain them only if the disposable receipts pass."
        ),
    },
    "AGENT-001": {
        "next_action": (
            "Execute the deterministic-first n8n path and prove AI is invoked only for explicitly unresolved fields."
        ),
    },
    "AGENT-002": {
        "next_action": (
            "Prove server-owned normal and exception model routing, including the gated high-reasoning path, in disposable n8n."
        ),
    },
    "AGENT-004": {
        "next_action": (
            "Run protected-field and automatic-write attacks and prove every AI result remains a review-only proposal."
        ),
    },
    "N8N-007": {
        "next_action": (
            "Run the disposable operations and error-workflow negative matrix and retain redacted durable receipts."
        ),
    },
    "N8N-009": {
        "next_action": (
            "Run allowed and malicious MCP facade requests with instance MCP disabled and prove zero unauthorized writes."
        ),
    },
    "N8N-010": {
        "next_action": (
            "After the functional MVP double replay and activation gates, implement the reviewed six-folder Finance/Global "
            "hierarchy and Canvas contract, prove exact group coverage and zero redundant sticky notes in repository and "
            "browser readback, and preserve unrelated applications."
        ),
    },
    "N8N-011": {
        "next_action": (
            "Review the exact 15-to-4 table-column map, both generated caller-immutable Edit Fields resolvers, observability owners, "
            "provider-circuit removal, WF20 verification-artifact atomicity, migration and rollback decisions."
        ),
    },
    "N8N-012": {
        "next_action": (
            "Execute the exact WF23 cleanup gate, retain the external content-addressed cleanup receipt, and prove the second run is a no-op."
        ),
    },
    "AUTO-002": {
        "next_action": (
            "After OAuth binding, execute bounded expected-cycle polling and retain cursor, no-statement, and statement-found receipts."
        ),
    },
    "DOCS-002": {
        "next_action": (
            "Review this generated backlog as the orchestration source, then require --check and focused tests on every backlog change."
        ),
    },
    "ACTUAL-003": {
        "next_action": (
            "Bind fresh FAB, Sarwa and FX evidence to one Actual sync identity, then prove API and authenticated UI account sets, signed balances and net worth are identical."
        ),
    },
    "ACTUAL-006": {
        "next_action": (
            "Compile the current canonical rules into the ownership manifest, require an empty overlap report, and run Actual/canonical evaluator parity fixtures."
        ),
    },
    "N8N-005": {
        "next_action": (
            "Run concurrent and kill-at-boundary disposable tests for the fenced Actual outbox and prove one row, one verified commit and one cursor advance."
        ),
    },
    "AUTO-001": {
        "next_action": (
            "After each n8n issuer workflow passes fixture, shadow and guarded readback, atomically cut over its exact Asia/Dubai schedule and disable the matching Codex task."
        ),
    },
}

QUEUE_FRONT = [
    "N8N-012",
    "N8N-003",
    "DOC-001",
    "N8N-005",
    "N8N-011",
    "N8N-006",
    "AGENT-003",
    "N8N-001",
    "N8N-002",
    "N8N-008",
    "DOCS-002",
    "PLATFORM-003",
    "PLATFORM-006",
    "BROWSER-002",
    "BROWSER-003",
    "ACTUAL-021",
    "ACTUAL-003",
    "ACTUAL-010",
    "ACTUAL-011",
    "ACTUAL-006",
    "DOC-002",
    "DOC-007",
    "ACTUAL-019",
    "PLATFORM-004",
    "AUTO-001",
    "N8N-010",
    "AUTO-003",
]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _parameter_strings(value: Any, path: str = "parameters") -> list[tuple[str, str]]:
    """Return stable JSON-style parameter paths and their string values."""
    if isinstance(value, dict):
        rows: list[tuple[str, str]] = []
        for key, child in value.items():
            rows.extend(_parameter_strings(child, f"{path}.{key}"))
        return rows
    if isinstance(value, list):
        rows = []
        for index, child in enumerate(value):
            rows.extend(_parameter_strings(child, f"{path}.{index}"))
        return rows
    return [(path, value)] if isinstance(value, str) else []


def _binding_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def derive_n8n_data_table_column_access(
    workflows_dir: Path,
    contract: dict[str, Any],
) -> dict[tuple[str, str], list[dict[str, str]]]:
    """Mechanically derive exact schema, filter, value and consumer bindings.

    A row write exists only when the column is explicitly present in
    ``parameters.columns.value``. Reads are filter-key bindings or an explicit
    downstream parameter/code reference to a get-node result. Schema creation
    is recorded independently for each declared column.
    """
    table_columns = {
        table["name"]: list(table["columns"])
        for table in contract["tables"]
    }
    access: dict[tuple[str, str], list[dict[str, str]]] = {
        (table, column): []
        for table, columns in table_columns.items()
        for column in columns
    }

    def add(
        *,
        workflow_path: str,
        workflow_code: str,
        table: str,
        column: str,
        data_node: dict[str, Any],
        operation: str,
        access_kind: str,
        binding_kind: str,
        binding_node: dict[str, Any],
        binding_path: str,
        binding_value: Any,
    ) -> None:
        key = (table, column)
        if key not in access:
            return
        entry = {
            "workflow_path": workflow_path,
            "workflow_code": workflow_code,
            "data_table_node_id": str(data_node["id"]),
            "data_table_node_name": str(data_node["name"]),
            "operation": operation,
            "access_kind": access_kind,
            "binding_kind": binding_kind,
            "binding_node_id": str(binding_node["id"]),
            "binding_node_name": str(binding_node["name"]),
            "binding_path": binding_path,
            "binding_sha256": _binding_sha256(binding_value),
        }
        if entry not in access[key]:
            access[key].append(entry)

    for workflow_file in sorted(workflows_dir.glob("[0-9][0-9]-*.json")):
        workflow = json.loads(workflow_file.read_text(encoding="utf-8"))
        workflow_code = f"WF{workflow_file.name[:2]}"
        workflow_path = workflow_file.resolve().relative_to(ROOT).as_posix()
        nodes = workflow.get("nodes", [])
        nodes_by_name = {node["name"]: node for node in nodes}
        connections = workflow.get("connections", {})

        for node in nodes:
            if "dataTable" not in node.get("type", ""):
                continue
            parameters = node.get("parameters", {})
            operation = str(parameters.get("operation", ""))
            if parameters.get("resource") == "table":
                table = str(parameters.get("tableName", ""))
            else:
                data_table_id = parameters.get("dataTableId", {})
                table = str(data_table_id.get("value", "")) if isinstance(data_table_id, dict) else ""
            if table not in table_columns:
                continue

            for index, definition in enumerate(parameters.get("columns", {}).get("column", [])):
                column = definition.get("name")
                if column in table_columns[table]:
                    add(
                        workflow_path=workflow_path,
                        workflow_code=workflow_code,
                        table=table,
                        column=column,
                        data_node=node,
                        operation=operation,
                        access_kind="schema",
                        binding_kind="schema_definition",
                        binding_node=node,
                        binding_path=f"parameters.columns.column.{index}",
                        binding_value=definition,
                    )

            conditions = parameters.get("filters", {}).get("conditions", [])
            for index, condition in enumerate(conditions):
                column = condition.get("keyName")
                if column in table_columns[table]:
                    add(
                        workflow_path=workflow_path,
                        workflow_code=workflow_code,
                        table=table,
                        column=column,
                        data_node=node,
                        operation=operation,
                        access_kind="read",
                        binding_kind="filter",
                        binding_node=node,
                        binding_path=f"parameters.filters.conditions.{index}",
                        binding_value=condition,
                    )

            mapped_values = parameters.get("columns", {}).get("value", {})
            for column, binding_value in mapped_values.items():
                if column in table_columns[table]:
                    add(
                        workflow_path=workflow_path,
                        workflow_code=workflow_code,
                        table=table,
                        column=column,
                        data_node=node,
                        operation=operation,
                        access_kind="write",
                        binding_kind="column_value",
                        binding_node=node,
                        binding_path=f"parameters.columns.value.{column}",
                        binding_value=binding_value,
                    )

            if operation != "get":
                continue

            direct_names = {
                target["node"]
                for output in connections.get(node["name"], {}).get("main", [])
                for target in output
            }
            for consumer in nodes:
                parameter_values = _parameter_strings(consumer.get("parameters", {}))
                is_direct = consumer["name"] in direct_names
                for binding_path, binding_value in parameter_values:
                    names_data_node = node["name"] in binding_value
                    if not is_direct and not names_data_node:
                        continue
                    json_aliases = set(re.findall(
                        r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*\$json\b",
                        binding_value,
                    ))
                    value_sources = [r"\$json", *(re.escape(alias) for alias in json_aliases)]
                    source_pattern = "(?:" + "|".join(value_sources) + ")"
                    spread_all = is_direct and bool(re.search(
                        rf"\.\.\.{source_pattern}\b",
                        binding_value,
                    ))
                    consumes_all_inputs = is_direct and "$input.all()" in binding_value
                    for column in table_columns[table]:
                        property_reference = re.search(
                            rf"{source_pattern}(?:\?\.|\.|\[['\"]){re.escape(column)}(?:['\"]\])?(?=[^A-Za-z0-9_]|$)",
                            binding_value,
                        )
                        if (names_data_node or consumes_all_inputs) and not property_reference:
                            property_reference = re.search(
                                rf"(?:\?\.|\.|\[['\"]){re.escape(column)}(?:['\"]\])?(?=[^A-Za-z0-9_]|$)",
                                binding_value,
                            )
                        if not property_reference and not spread_all:
                            continue
                        add(
                            workflow_path=workflow_path,
                            workflow_code=workflow_code,
                            table=table,
                            column=column,
                            data_node=node,
                            operation=operation,
                            access_kind="read",
                            binding_kind=(
                                "downstream_spread" if spread_all and not property_reference
                                else "downstream_expression"
                            ),
                            binding_node=consumer,
                            binding_path=binding_path,
                            binding_value=binding_value,
                        )

    for rows in access.values():
        rows.sort(key=lambda row: (
            row["workflow_path"], row["data_table_node_id"], row["binding_node_id"],
            row["binding_path"], row["binding_kind"],
        ))
    return access


def _superseded_evidence_hits(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for field in (
        "implementation_state", "verification_state", "tests", "live_readback",
        "remaining_work", "blocked_by", "blockers_add", "next_action",
    ):
        value = row.get(field, [])
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value is not None:
            values.append(str(value))
    text = "\n".join(values)
    return [marker for marker in SUPERSEDED_EVIDENCE_MARKERS if marker in text]


def validate_progress_overlay(
    overlay: dict[str, Any], known_task_ids: set[str],
) -> list[str]:
    """Validate mutable progress without trusting it as acceptance evidence."""
    errors: list[str] = []
    if overlay.get("schema_version") != 1:
        errors.append("progress schema_version must be 1")
    commit = str(overlay.get("evidence_commit", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        errors.append("progress evidence_commit must be a full lowercase Git SHA")
    commits = overlay.get("evidence_commits", [])
    if not isinstance(commits, list) or not commits:
        errors.append("progress evidence_commits must be a non-empty array")
    elif any(not re.fullmatch(r"[0-9a-f]{40}", str(item)) for item in commits):
        errors.append("progress evidence_commits must contain full lowercase Git SHAs")
    elif len(commits) != len(set(commits)):
        errors.append("progress evidence_commits must be unique")
    elif commit not in commits:
        errors.append("progress evidence_commit must be present in evidence_commits")
    notes = overlay.get("snapshot_notes", [])
    if not isinstance(notes, list) or not notes:
        errors.append("progress snapshot_notes must be a non-empty array")
    elif any(not isinstance(item, str) or not item for item in notes):
        errors.append("progress snapshot_notes must contain non-empty strings")
    resolved = overlay.get("resolved_blockers", [])
    if not isinstance(resolved, list) or not resolved:
        errors.append("progress resolved_blockers must be a non-empty array")
    elif any(not isinstance(item, str) or not item for item in resolved):
        errors.append("progress resolved_blockers must contain non-empty strings")
    markers = overlay.get("superseded_text_markers", [])
    if not isinstance(markers, list) or not markers:
        errors.append("progress superseded_text_markers must be a non-empty array")
    elif any(not isinstance(item, str) or not item for item in markers):
        errors.append("progress superseded_text_markers must contain non-empty strings")
    updates = overlay.get("updates")
    if not isinstance(updates, list) or not updates:
        return errors + ["progress updates must be a non-empty array"]
    ids = [row.get("id") for row in updates]
    if len(ids) != len(set(ids)):
        errors.append("progress task IDs must be unique")
    unknown = set(ids) - known_task_ids
    if unknown:
        errors.append(f"progress references unknown tasks: {sorted(unknown)}")
    allowed = {
        "id", "status", "implementation_state", "verification_state",
        "evidence_paths", "tests", "live_readback", "remaining_work",
        "blockers_remove", "blockers_add", "last_verified", "next_action",
        "acceptance_criteria_met", "verification_receipt_sha256",
    }
    for row in updates:
        task_id = row.get("id", "<missing>")
        stale = _superseded_evidence_hits(row)
        if stale:
            errors.append(f"{task_id}: superseded evidence markers {stale}")
        extra = set(row) - allowed
        if extra:
            errors.append(f"{task_id}: unsupported progress fields {sorted(extra)}")
        if row.get("status") not in VALID_STATUSES - {"SUPERSEDED"}:
            errors.append(f"{task_id}: invalid progress status {row.get('status')}")
        for field in ("evidence_paths", "tests", "blockers_remove", "blockers_add"):
            value = row.get(field, [])
            if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
                errors.append(f"{task_id}: {field} must contain non-empty strings")
            elif len(value) != len(set(value)):
                errors.append(f"{task_id}: {field} must contain unique values")
        if row.get("status") == "VERIFIED":
            receipt = str(row.get("verification_receipt_sha256", ""))
            if (
                row.get("acceptance_criteria_met") is not True
                or not re.fullmatch(r"[0-9a-f]{64}", receipt)
                or not row.get("evidence_paths")
                or not row.get("tests")
                or not row.get("live_readback")
                or not row.get("last_verified")
                or row.get("verification_state") != "RUNTIME_VERIFIED"
                or row.get("blockers_add")
            ):
                errors.append(
                    f"{task_id}: VERIFIED progress requires acceptance, receipt hash, "
                    "runtime readback, tests, evidence, date, and no added blockers"
                )
    return errors


def apply_progress_overlay(backlog: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge reviewed progress onto generated tasks while preserving source inputs."""
    task_by_id = {task["id"]: task for task in backlog["tasks"]}
    errors = validate_progress_overlay(overlay, set(task_by_id))
    if errors:
        raise ValueError("; ".join(errors))
    resolved = set(overlay["resolved_blockers"])
    for task in backlog["tasks"]:
        task["blockers"] = [item for item in task["blockers"] if item not in resolved]
        stale = _superseded_evidence_hits(task)
        if stale:
            raise ValueError(f"{task['id']}: superseded evidence markers {stale}")
        if task["status"] not in {"VERIFIED", "SUPERSEDED"} and not task["blockers"]:
            task["blockers"] = ["ACCEPTANCE_EVIDENCE_REQUIRED"]
    for update in overlay["updates"]:
        task = task_by_id[update["id"]]
        remove = set(update.get("blockers_remove", []))
        missing = remove - set(task["blockers"])
        if missing:
            raise ValueError(f"{task['id']}: cannot remove absent blockers {sorted(missing)}")
        task["blockers"] = _unique([
            blocker for blocker in task["blockers"] if blocker not in remove
        ] + update.get("blockers_add", []))
        for field in (
            "status", "implementation_state", "verification_state", "live_readback",
            "remaining_work", "last_verified", "next_action",
        ):
            if field in update:
                task[field] = update[field]
        task["evidence_paths"] = _unique(task["evidence_paths"] + update.get("evidence_paths", []))
        task["tests"] = _unique(task["tests"] + update.get("tests", []))
        if task["status"] not in {"VERIFIED", "SUPERSEDED"} and not task["blockers"]:
            raise ValueError(f"{task['id']}: unverified progress must retain a blocker")

    backlog["source_artifacts"] = _unique(backlog["source_artifacts"] + [
        "config/project-backlog-progress.json",
    ])
    backlog["progress_overlay"] = {
        "path": "config/project-backlog-progress.json",
        "recorded_at": overlay["recorded_at"],
        "evidence_commit": overlay["evidence_commit"],
        "evidence_commits": overlay["evidence_commits"],
        "snapshot_notes": overlay["snapshot_notes"],
        "resolved_blockers": overlay["resolved_blockers"],
        "superseded_text_markers": overlay["superseded_text_markers"],
        "update_count": len(overlay["updates"]),
    }
    backlog["summary"]["verified_count"] = sum(
        task["status"] == "VERIFIED" for task in backlog["tasks"]
    )
    backlog["summary"]["by_status"] = dict(sorted(
        Counter(task["status"] for task in backlog["tasks"]).items()
    ))
    queue_by_id = {row["id"]: row for row in backlog["ordered_executable_queue"]}
    for task in backlog["tasks"]:
        if task["id"] in queue_by_id:
            queue_by_id[task["id"]].update({
                "status": task["status"],
                "next_action": task["next_action"],
                "blocked_by": task["blockers"],
                "owner_workstream": task["owner_workstream"],
            })
    return backlog


def _implementation_index(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    known_implementation_ids = {item["id"] for item in items}
    unknown = set(IMPLEMENTATION_LINKS) - known_implementation_ids
    if unknown:
        raise ValueError(f"implementation mapping references missing items: {sorted(unknown)}")
    for item in items:
        for task_id in IMPLEMENTATION_LINKS.get(item["id"], []):
            index.setdefault(task_id, []).append(item)
    return index


def _combined_implementation_state(items: list[dict[str, Any]]) -> str:
    if not items:
        return "NOT_ASSESSED"
    states = {item["implemented_state"] for item in items}
    if len(states) == 1:
        return next(iter(states))
    if states <= {"IMPLEMENTED_NOT_DEPLOYED", "IMPLEMENTED"}:
        return "IMPLEMENTED_NOT_DEPLOYED"
    return "PARTIAL"


def _combined_verification_state(items: list[dict[str, Any]]) -> str:
    if not items:
        return "NOT_VERIFIED"
    states = sorted({item["verification_state"] for item in items})
    return states[0] if len(states) == 1 else "MIXED: " + ", ".join(states)


def _status(source_status: str, items: list[dict[str, Any]], task_id: str) -> str:
    if source_status == "SUPERSEDED":
        return "SUPERSEDED"
    if task_id in STATUS_OVERRIDES:
        return STATUS_OVERRIDES[task_id]
    if any(item["verification_state"] == "BLOCKED" for item in items):
        return "BLOCKED"
    if items and all(item["implemented_state"] == "IMPLEMENTED_NOT_DEPLOYED" for item in items):
        return "IMPLEMENTED_UNVERIFIED"
    if source_status in {"USER_CONFIRMED_ACCEPTANCE", "ASSISTANT_CLAIM_ONLY"}:
        return "IMPLEMENTED_UNVERIFIED" if items else "PARTIAL"
    if source_status == "REQUESTED_UNVERIFIED" and not items:
        return "NOT_STARTED"
    return "PARTIAL"


def _next_action(task: dict[str, Any], items: list[dict[str, Any]]) -> str:
    override = LATEST_OVERRIDES.get(task["id"], {}).get("next_action")
    if override:
        return override
    if items:
        return items[0]["remaining_work"]
    criterion = task["acceptance_criteria"][0]
    return f"Implement and prove: {criterion}."


def build_backlog(transcript: dict[str, Any], implementation: dict[str, Any]) -> dict[str, Any]:
    rows = transcript.get("requirements", [])
    if not rows:
        raise ValueError("transcript requirements must be non-empty")
    source_ids = [row["id"] for row in rows]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("transcript requirement IDs must be unique")

    implementation_items = implementation.get("items", [])
    for item in implementation_items:
        stale = _superseded_evidence_hits(item)
        if stale:
            raise ValueError(f"{item.get('id', '<missing>')}: superseded evidence markers {stale}")
    implementation_index = _implementation_index(implementation_items)
    tasks: list[dict[str, Any]] = []
    for row in rows:
        task_id = row["id"]
        prefix = task_id.split("-", 1)[0]
        domain, owner = PREFIX_METADATA[prefix]
        linked = implementation_index.get(task_id, [])
        evidence_paths = _unique([
            path for item in linked for path in item.get("evidence_paths", [])
        ])
        tests = _unique([test for item in linked for test in item.get("tests", [])])
        live_readback = " | ".join(
            f"{item['id']}: {item.get('live_readback', '')}" for item in linked
            if item.get("live_readback")
        )
        remaining = " | ".join(_unique([
            item.get("remaining_work", "") for item in linked
        ]))
        if not remaining:
            remaining = f"Implement and verify all acceptance criteria for {task_id}."
        blockers = _unique([
            blocker for item in linked for blocker in item.get("blocked_by", [])
        ])
        status = _status(row["status_from_transcript"], linked, task_id)
        if status not in {"VERIFIED", "SUPERSEDED"} and not blockers:
            blockers = ["IMPLEMENTATION_REQUIRED" if status == "NOT_STARTED" else "ACCEPTANCE_EVIDENCE_REQUIRED"]
        contradictions = _unique(
            list(row.get("contradictions", []))
            + list(LATEST_OVERRIDES.get(task_id, {}).get("contradictions", []))
        )
        dates = sorted({item.get("last_verified") for item in linked if item.get("last_verified")})
        tasks.append({
            "id": task_id,
            "domain": domain,
            "title": row["title"],
            "requirement": row["requirement"],
            "priority": row["priority"],
            "status": status,
            "owner_workstream": owner,
            "acceptance_validator": ACCEPTANCE_VALIDATORS[prefix],
            "dependencies": list(row.get("dependencies", [])),
            "related_tasks": list(row.get("related_tasks", [])),
            "acceptance_criteria": list(row["acceptance_criteria"]),
            "implementation_state": _combined_implementation_state(linked),
            "verification_state": _combined_verification_state(linked),
            "evidence_paths": evidence_paths,
            "tests": tests,
            "live_readback": live_readback,
            "remaining_work": remaining,
            "blockers": blockers,
            "source_status": row["status_from_transcript"],
            "contradictions": contradictions,
            "last_verified": dates[-1] if dates else None,
            "next_action": _next_action(row, linked),
        })

    known = {task["id"] for task in tasks}
    for task in tasks:
        for field in ("dependencies", "related_tasks"):
            unknown = set(task[field]) - known
            if unknown:
                raise ValueError(f"{task['id']}: unknown {field} {sorted(unknown)}")
        overlap = set(task["dependencies"]) & set(task["related_tasks"])
        if overlap:
            raise ValueError(f"{task['id']}: dependencies and related_tasks overlap {sorted(overlap)}")

    queue_rank = {task_id: rank for rank, task_id in enumerate(QUEUE_FRONT)}
    priority_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    queued = sorted(
        (task for task in tasks if task["status"] != "SUPERSEDED"),
        key=lambda task: (
            queue_rank.get(task["id"], len(QUEUE_FRONT)),
            priority_rank[task["priority"]],
            task["id"],
        ),
    )
    ordered_queue = [
        {
            "rank": rank,
            "id": task["id"],
            "status": task["status"],
            "priority": task["priority"],
            "owner_workstream": task["owner_workstream"],
            "next_action": task["next_action"],
            "blocked_by": task["blockers"],
        }
        for rank, task in enumerate(queued, start=1)
    ]

    return {
        "schema_version": 1,
        "generated_at": transcript["generated_at"],
        "source_artifacts": [
            "docs/project-audit/transcript-requirements-ledger-2026-08-19.json",
            "docs/project-audit/implementation-status.json",
        ],
        "summary": {
            "total": len(tasks),
            "verified_count": sum(task["status"] == "VERIFIED" for task in tasks),
            "by_status": dict(sorted(Counter(task["status"] for task in tasks).items())),
            "by_priority": dict(sorted(Counter(task["priority"] for task in tasks).items())),
            "by_domain": dict(sorted(Counter(task["domain"] for task in tasks).items())),
        },
        "ordered_executable_queue": ordered_queue,
        "tasks": tasks,
    }


def validate_backlog(payload: dict[str, Any], transcript_ids: set[str] | None = None) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    progress = payload.get("progress_overlay")
    if progress is not None:
        required_progress = {
            "path", "recorded_at", "evidence_commit", "evidence_commits",
            "snapshot_notes", "resolved_blockers", "superseded_text_markers", "update_count",
        }
        if set(progress) != required_progress:
            errors.append("progress_overlay has invalid fields")
        if progress.get("path") not in payload.get("source_artifacts", []):
            errors.append("progress_overlay path must be a source artifact")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return errors + ["tasks must be a non-empty array"]
    ids = [task.get("id") for task in tasks]
    if len(ids) != len(set(ids)):
        errors.append("task IDs must be unique")
    if transcript_ids is not None and set(ids) != transcript_ids:
        errors.append("backlog task IDs must exactly preserve transcript requirement IDs")
    known = set(ids)
    required_fields = {
        "id", "domain", "title", "requirement", "priority", "status",
        "owner_workstream", "acceptance_validator", "dependencies", "related_tasks", "acceptance_criteria", "implementation_state",
        "verification_state", "evidence_paths", "tests", "live_readback", "remaining_work",
        "blockers", "source_status", "contradictions", "last_verified", "next_action",
    }
    for task in tasks:
        task_id = task.get("id", "<missing>")
        stale = _superseded_evidence_hits(task)
        if stale:
            errors.append(f"{task_id}: superseded evidence markers {stale}")
        missing = required_fields - set(task)
        if missing:
            errors.append(f"{task_id}: missing fields {sorted(missing)}")
        if task.get("status") not in VALID_STATUSES:
            errors.append(f"{task_id}: invalid status {task.get('status')}")
        prefix = str(task_id).split("-", 1)[0]
        expected_owner = PREFIX_METADATA.get(prefix, (None, None))[1]
        if task.get("owner_workstream") != expected_owner:
            errors.append(f"{task_id}: owner_workstream must be {expected_owner}")
        expected_validator = ACCEPTANCE_VALIDATORS.get(prefix)
        if task.get("acceptance_validator") != expected_validator:
            errors.append(f"{task_id}: acceptance_validator must match its workstream")
        for field in ("dependencies", "related_tasks"):
            unknown = set(task.get(field, [])) - known
            if unknown:
                errors.append(f"{task_id}: unknown {field} {sorted(unknown)}")
        overlap = set(task.get("dependencies", [])) & set(task.get("related_tasks", []))
        if overlap:
            errors.append(f"{task_id}: dependencies and related_tasks overlap {sorted(overlap)}")
        if not task.get("acceptance_criteria"):
            errors.append(f"{task_id}: acceptance_criteria must be non-empty")
        for evidence_path in task.get("evidence_paths", []):
            if not str(evidence_path).startswith("/") and not (ROOT / evidence_path).exists():
                errors.append(f"{task_id}: missing relative evidence path {evidence_path}")
        if task.get("status") == "VERIFIED":
            if not task.get("evidence_paths") or not task.get("live_readback") or not task.get("last_verified"):
                errors.append(f"{task_id}: VERIFIED requires evidence paths, live readback and last_verified")
        if task.get("status") not in {"VERIFIED", "SUPERSEDED"} and not task.get("blockers"):
            errors.append(f"{task_id}: unverified task requires blockers")

    adjacency = {task["id"]: list(task.get("dependencies", [])) for task in tasks}
    visit_state: dict[str, int] = {}
    stack: list[str] = []

    def visit(task_id: str) -> None:
        state = visit_state.get(task_id, 0)
        if state == 2:
            return
        if state == 1:
            start = stack.index(task_id)
            errors.append(f"dependency cycle: {' -> '.join(stack[start:] + [task_id])}")
            return
        visit_state[task_id] = 1
        stack.append(task_id)
        for dependency in adjacency.get(task_id, []):
            if dependency in adjacency:
                visit(dependency)
        stack.pop()
        visit_state[task_id] = 2

    for task_id in adjacency:
        if visit_state.get(task_id, 0) == 0:
            visit(task_id)

    summary = payload.get("summary", {})
    if summary.get("total") != len(tasks):
        errors.append("summary total does not equal task count")
    if summary.get("verified_count") != sum(task.get("status") == "VERIFIED" for task in tasks):
        errors.append("summary verified_count is incorrect")
    queue = payload.get("ordered_executable_queue", [])
    queue_ids = [item.get("id") for item in queue]
    expected_queue_ids = [task["id"] for task in tasks if task.get("status") != "SUPERSEDED"]
    if set(queue_ids) != set(expected_queue_ids) or len(queue_ids) != len(set(queue_ids)):
        errors.append("ordered queue must contain every non-superseded task exactly once")
    if [item.get("rank") for item in queue] != list(range(1, len(queue) + 1)):
        errors.append("ordered queue ranks must be contiguous from 1")
    task_by_id = {task["id"]: task for task in tasks}
    for item in queue:
        task = task_by_id.get(item.get("id"))
        if task and item.get("owner_workstream") != task.get("owner_workstream"):
            errors.append(f"{item.get('id')}: queue owner_workstream does not match task")
    return errors


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Finance platform orchestrated backlog",
        "",
        f"Generated: {payload['generated_at']}",
        f"Tasks: **{summary['total']}**",
        f"Verified: **{summary['verified_count']}**",
        "",
        "This backlog merges the full transcript requirements ledger with the independent implementation audit.",
        "Repository code, tests and historical user acceptance remain unverified until the task's current acceptance criteria and live readback pass.",
        "",
        "## Status summary",
        "",
        "| Status | Count |",
        "| --- | ---: |",
    ]
    for status, count in summary["by_status"].items():
        lines.append(f"| `{status}` | {count} |")
    progress = payload.get("progress_overlay")
    if progress:
        lines.extend([
            "",
            "## Evidence snapshot",
            "",
            f"Progress recorded: **{progress['recorded_at']}** at `{progress['evidence_commit'][:7]}`.",
            "Evidence commits: " + ", ".join(
                f"`{item[:7]}`" for item in progress["evidence_commits"]
            ),
            "",
        ])
        lines.extend(f"- {note}" for note in progress["snapshot_notes"])
    lines.extend([
        "",
        "## Latest orchestration overrides",
        "",
        "- Manual workflow-layout optimization is removed. Use **Tidy Workflow**; clear code, node names, notes, sections and folders remain required.",
        "- Execute Sub-workflow selectors should use **From list** when the target is available.",
        "- The finance workflows must live under the sole top-level `Finance` application root, with role-based child folders and peer application roots left untouched.",
        "- The 15-table Data Table contract is a review baseline. The unapproved direction is four domain tables; n8n owns generic execution/failures, Cloudflare access logs, optional Langfuse sanitized traces, and cashback companion period close.",
        "- Community agent candidates are pinned to `n8n-nodes-prodex@0.5.1` and `@ggomez91npm/n8n-nodes-claude-code@0.8.0`; neither is production-approved until disposable registration, isolation, authentication and structured-output proof passes.",
        "- The backlog intentionally has no automatically promoted `VERIFIED` tasks.",
        "",
        "## Ordered executable queue",
        "",
        "| # | ID | Owner | Priority | Status | Next action |",
        "| ---: | --- | --- | --- | --- | --- |",
    ])
    for item in payload["ordered_executable_queue"]:
        action = item["next_action"].replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {item['rank']} | `{item['id']}` | `{item['owner_workstream']}` | "
            f"{item['priority']} | `{item['status']}` | {action} |"
        )
    lines.extend(["", "## Tasks by workstream", ""])
    domains = sorted({task["domain"] for task in payload["tasks"]})
    for domain in domains:
        lines.extend([
            f"### {domain}", "",
            "| ID | Owner | Strict dependencies | Related tasks | Status | Acceptance evidence and validator | Title |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ])
        for task in payload["tasks"]:
            if task["domain"] == domain:
                dependencies = ", ".join(
                    f"`{item}`" for item in task["dependencies"]
                ) or "None"
                related_tasks = ", ".join(
                    f"`{item}`" for item in task["related_tasks"]
                ) or "None"
                evidence = (
                    f"{task['implementation_state']} / {task['verification_state']}; "
                    f"{len(task['evidence_paths'])} files, {len(task['tests'])} tests; "
                    f"live readback: {'recorded' if task['live_readback'] else 'none'}; "
                    f"{len(task['acceptance_criteria'])} acceptance checks; "
                    f"validator: {task['acceptance_validator']}"
                ).replace("|", "\\|")
                title = task["title"].replace("|", "\\|")
                lines.append(
                    f"| `{task['id']}` | `{task['owner_workstream']}` | {dependencies} | "
                    f"{related_tasks} | `{task['status']}` | {evidence} | {title} |"
                )
        lines.append("")
    lines.extend([
        "## Validation",
        "",
        "Regenerate and validate deterministically:",
        "",
        "```powershell",
        "python scripts/generate-project-backlog.py --check",
        "python -m unittest tests.test_project_backlog -v",
        "```",
        "",
    ])
    return "\n".join(lines)


def load_and_build() -> dict[str, Any]:
    transcript = json.loads(TRANSCRIPT_PATH.read_text(encoding="utf-8"))
    implementation = json.loads(IMPLEMENTATION_PATH.read_text(encoding="utf-8"))
    backlog = build_backlog(transcript, implementation)
    if PROGRESS_PATH.exists():
        overlay = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
        backlog = apply_progress_overlay(backlog, overlay)
    return backlog


def write_outputs(payload: dict[str, Any]) -> None:
    BACKLOG_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    BACKLOG_DOC_PATH.write_text(render_markdown(payload), encoding="utf-8")
