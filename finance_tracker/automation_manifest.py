from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


STATUSES = frozenset({"ACTIVE", "PAUSED"})
KINDS = frozenset({"cron"})
NOTIFICATION_POLICIES = frozenset({"failed_runs_only"})
REASONING_EFFORTS = frozenset({"medium", "max"})
FIELDS = (
    "name",
    "kind",
    "status",
    "rrule",
    "model",
    "reasoning_effort",
    "notification_policy",
    "prompt",
)


@dataclass(frozen=True, slots=True)
class AutomationAudit:
    status: str
    expected_count: int
    actual_count: int
    missing: tuple[str, ...]
    extra: tuple[str, ...]
    drift: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "expected_count": self.expected_count,
            "actual_count": self.actual_count,
            "missing": list(self.missing),
            "extra": list(self.extra),
            "drift": list(self.drift),
        }


def load_automation_manifest(path: Path, project_root: Path | None = None) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported Codex automation manifest schema version")
    if payload.get("timezone") != "Asia/Dubai":
        raise ValueError("Finance automations must declare timezone Asia/Dubai")
    rows = payload.get("automations")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Codex automation manifest must contain automations")
    seen: set[str] = set()
    root = project_root or path.parent.parent
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Codex automation rows must be objects")
        automation_id = str(row.get("id") or "").strip()
        if not automation_id or automation_id in seen:
            raise ValueError(f"Codex automation id is blank or duplicated: {automation_id}")
        seen.add(automation_id)
        if row.get("status") not in STATUSES:
            raise ValueError(f"Automation {automation_id} has invalid status")
        if row.get("kind") not in KINDS:
            raise ValueError(f"Automation {automation_id} has invalid kind")
        if row.get("reasoning_effort") not in REASONING_EFFORTS:
            raise ValueError(f"Automation {automation_id} has invalid reasoning effort")
        if row.get("notification_policy") not in NOTIFICATION_POLICIES:
            raise ValueError(f"Automation {automation_id} has invalid notification policy")
        for field in FIELDS:
            if not str(row.get(field) or "").strip():
                raise ValueError(f"Automation {automation_id} requires {field}")
        runbook = str(row.get("runbook") or "").strip()
        if not runbook or not (root / runbook).is_file():
            raise ValueError(f"Automation {automation_id} runbook does not exist: {runbook}")
        if runbook not in str(row["prompt"]):
            raise ValueError(f"Automation {automation_id} prompt must reference its runbook")
    return payload


def _read_installed(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return result
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        automation_file = directory / "automation.toml"
        if not automation_file.is_file():
            continue
        row = tomllib.loads(automation_file.read_text(encoding="utf-8"))
        automation_id = str(row.get("id") or directory.name).strip()
        if automation_id in result:
            raise ValueError(f"Installed Codex automation id is duplicated: {automation_id}")
        result[automation_id] = row
    return result


def audit_automations(manifest: dict[str, Any], automation_root: Path) -> AutomationAudit:
    expected = {str(row["id"]): row for row in manifest["automations"]}
    installed = _read_installed(automation_root)
    missing = tuple(sorted(set(expected) - set(installed)))
    extra = (
        tuple(sorted(set(installed) - set(expected)))
        if not bool(manifest.get("allow_extra_automations"))
        else ()
    )
    drift: list[dict[str, str]] = []
    for automation_id in sorted(set(expected) & set(installed)):
        for field in FIELDS:
            expected_value = str(expected[automation_id].get(field) or "")
            actual_value = str(installed[automation_id].get(field) or "")
            if expected_value != actual_value:
                drift.append(
                    {
                        "id": automation_id,
                        "field": field,
                        "expected": expected_value,
                        "actual": actual_value,
                    }
                )
    status = "ok" if not missing and not extra and not drift else "drift"
    return AutomationAudit(
        status=status,
        expected_count=len(expected),
        actual_count=len(installed),
        missing=missing,
        extra=extra,
        drift=tuple(drift),
    )

