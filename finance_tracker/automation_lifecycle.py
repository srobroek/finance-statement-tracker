from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


THREAD_ID_PATTERN = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
AUTOMATION_ID_PATTERN = re.compile(r"^Automation ID:\s*([a-z0-9][a-z0-9-]*)\s*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class ThreadLifecycle:
    thread_id: str
    location: str
    active_paths: tuple[str, ...]
    archived_paths: tuple[str, ...]
    automation_id: str | None
    is_automation: bool
    task_complete: bool
    has_final_answer: bool
    receipt_status: str
    eligible_for_archive: bool
    problems: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _validate_thread_id(thread_id: str) -> str:
    value = str(thread_id or "").strip().lower()
    if not THREAD_ID_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid Codex thread id: {thread_id}")
    return value


def _rollout_paths(root: Path, thread_id: str) -> tuple[Path, ...]:
    if not root.is_dir():
        return ()
    return tuple(sorted(root.rglob(f"rollout-*-{thread_id}.jsonl")))


def _message_text(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    pieces: list[str] = []
    for item in content:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            pieces.append(item["text"])
    return "\n".join(pieces)


def _read_rollout(path: Path) -> tuple[bool, str | None, bool, bool, tuple[str, ...]]:
    is_automation = False
    automation_id: str | None = None
    task_complete = False
    has_final_answer = False
    problems: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return False, None, False, False, (f"cannot read rollout: {exc}",)
    for line_number, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"invalid JSONL at line {line_number}")
            continue
        if row.get("type") == "session_meta":
            is_automation = row.get("payload", {}).get("thread_source") == "automation"
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if row.get("type") == "event_msg" and payload.get("type") == "task_complete":
            task_complete = True
        if row.get("type") == "response_item" and payload.get("type") == "message":
            text = _message_text(payload)
            if payload.get("role") == "user" and automation_id is None:
                match = AUTOMATION_ID_PATTERN.search(text)
                if match:
                    automation_id = match.group(1)
            if payload.get("role") == "assistant" and payload.get("phase") == "final_answer":
                has_final_answer = True
    return is_automation, automation_id, task_complete, has_final_answer, tuple(problems)


def _read_receipt(receipt_root: Path | None, thread_id: str) -> tuple[str, tuple[str, ...]]:
    if receipt_root is None:
        return "not_required", ()
    path = receipt_root / f"{thread_id}.json"
    if not path.is_file():
        return "missing", ("success receipt is missing",)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return "invalid", (f"success receipt is unreadable: {exc}",)
    problems: list[str] = []
    if payload.get("schema_version") != 1:
        problems.append("success receipt schema_version must be 1")
    if str(payload.get("thread_id") or "").lower() != thread_id:
        problems.append("success receipt thread_id does not match")
    if payload.get("status") != "SUCCESS_VERIFIED":
        problems.append("success receipt status is not SUCCESS_VERIFIED")
    if not str(payload.get("automation_id") or "").strip():
        problems.append("success receipt automation_id is missing")
    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        problems.append("success receipt must list verification evidence")
    return ("valid" if not problems else "invalid"), tuple(problems)


def inspect_thread_lifecycle(
    thread_id: str,
    sessions_root: Path,
    archived_root: Path,
    receipt_root: Path | None = None,
) -> ThreadLifecycle:
    value = _validate_thread_id(thread_id)
    active = _rollout_paths(sessions_root, value)
    archived = _rollout_paths(archived_root, value)
    problems: list[str] = []
    if len(active) + len(archived) == 0:
        location = "missing"
        problems.append("thread rollout is missing")
    elif len(active) + len(archived) > 1:
        location = "ambiguous"
        problems.append("thread rollout exists in more than one lifecycle location")
    elif archived:
        location = "archived"
    else:
        location = "active"
    path = (active or archived or (None,))[0]
    if path is None:
        is_automation, automation_id, task_complete, has_final_answer = False, None, False, False
    else:
        is_automation, automation_id, task_complete, has_final_answer, rollout_problems = _read_rollout(path)
        problems.extend(rollout_problems)
    receipt_status, receipt_problems = _read_receipt(receipt_root, value)
    problems.extend(receipt_problems)
    if path is not None and not is_automation:
        problems.append("thread is not an automation run")
    if path is not None and not task_complete:
        problems.append("thread has no task_complete event")
    if path is not None and not has_final_answer:
        problems.append("thread has no final answer")
    eligible = (
        location == "active"
        and is_automation
        and task_complete
        and has_final_answer
        and receipt_status in {"not_required", "valid"}
        and not problems
    )
    return ThreadLifecycle(
        thread_id=value,
        location=location,
        active_paths=tuple(str(path) for path in active),
        archived_paths=tuple(str(path) for path in archived),
        automation_id=automation_id,
        is_automation=is_automation,
        task_complete=task_complete,
        has_final_answer=has_final_answer,
        receipt_status=receipt_status,
        eligible_for_archive=eligible,
        problems=tuple(problems),
    )


def audit_threads(
    thread_ids: Iterable[str],
    sessions_root: Path,
    archived_root: Path,
    receipt_root: Path | None = None,
) -> dict[str, Any]:
    rows = [
        inspect_thread_lifecycle(thread_id, sessions_root, archived_root, receipt_root)
        for thread_id in thread_ids
    ]
    return {
        "schema_version": 1,
        "status": "ok" if all(not row.problems for row in rows) else "attention",
        "threads": [row.to_dict() for row in rows],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit Codex automation thread lifecycle state")
    parser.add_argument("--thread-id", action="append", required=True)
    parser.add_argument("--sessions-root", type=Path, required=True)
    parser.add_argument("--archived-root", type=Path, required=True)
    parser.add_argument("--receipt-root", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = audit_threads(
        args.thread_id,
        args.sessions_root,
        args.archived_root,
        args.receipt_root,
    )
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
