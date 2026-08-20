from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from finance_tracker.automation_lifecycle import inspect_thread_lifecycle


THREAD_ID = "01a0130d-0c6f-71d3-a24b-05d13eaaf2e3"


def _rollout(path: Path, *, task_complete: bool = True) -> None:
    rows = [
        {
            "type": "session_meta",
            "payload": {"thread_source": "automation", "id": THREAD_ID},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Automation ID: rakbank-morning-cashback-scan"}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "Verified."}],
            },
        },
    ]
    if task_complete:
        rows.append({"type": "event_msg", "payload": {"type": "task_complete"}})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _receipt(root: Path, *, status: str = "SUCCESS_VERIFIED") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{THREAD_ID}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "thread_id": THREAD_ID,
                "automation_id": "rakbank-morning-cashback-scan",
                "status": status,
                "evidence": ["runtime/verified-state.json"],
            }
        ),
        encoding="utf-8",
    )


class AutomationLifecycleTests(unittest.TestCase):
    def test_completed_active_run_with_receipt_is_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            active = root / "sessions" / "2026" / "08" / "19" / f"rollout-test-{THREAD_ID}.jsonl"
            _rollout(active)
            _receipt(root / "receipts")

            result = inspect_thread_lifecycle(
                THREAD_ID,
                root / "sessions",
                root / "archived_sessions",
                root / "receipts",
            )

            self.assertEqual(result.location, "active")
            self.assertTrue(result.eligible_for_archive)
            self.assertEqual(result.automation_id, "rakbank-morning-cashback-scan")

    def test_archived_run_is_confirmed_but_not_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archived = root / "archived_sessions" / f"rollout-test-{THREAD_ID}.jsonl"
            _rollout(archived)

            result = inspect_thread_lifecycle(
                THREAD_ID,
                root / "sessions",
                root / "archived_sessions",
            )

            self.assertEqual(result.location, "archived")
            self.assertFalse(result.eligible_for_archive)
            self.assertEqual(result.problems, ())

    def test_incomplete_or_unreceipted_run_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            active = root / "sessions" / "2026" / "08" / "19" / f"rollout-test-{THREAD_ID}.jsonl"
            _rollout(active, task_complete=False)

            result = inspect_thread_lifecycle(
                THREAD_ID,
                root / "sessions",
                root / "archived_sessions",
                root / "receipts",
            )

            self.assertFalse(result.eligible_for_archive)
            self.assertIn("thread has no task_complete event", result.problems)
            self.assertIn("success receipt is missing", result.problems)

    def test_duplicate_active_and_archived_rollouts_are_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _rollout(root / "sessions" / f"rollout-active-{THREAD_ID}.jsonl")
            _rollout(root / "archived_sessions" / f"rollout-archived-{THREAD_ID}.jsonl")

            result = inspect_thread_lifecycle(
                THREAD_ID,
                root / "sessions",
                root / "archived_sessions",
            )

            self.assertEqual(result.location, "ambiguous")
            self.assertFalse(result.eligible_for_archive)


if __name__ == "__main__":
    unittest.main()
