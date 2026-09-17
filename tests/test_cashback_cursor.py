from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from finance_tracker.cashback_events import CashbackEventStore, IngestCursorConflict


class CashbackCursorTests(unittest.TestCase):
    def _store(self, temporary: str) -> CashbackEventStore:
        return CashbackEventStore(Path(temporary) / "events.sqlite3")

    @staticmethod
    def _receipt(store: CashbackEventStore, *, cursor: str, completed_at: str, scanned: int = 0) -> dict[str, object]:
        return store.create_ingest_receipt({
            "source": "outlook",
            "completed_at": completed_at,
            "scanned_count": scanned,
            "accepted_count": 0,
            "cursor": cursor,
        })

    def test_commit_is_receipt_bound_and_exact_replay_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary)
            receipt = self._receipt(
                store,
                cursor="2026-08-16T16:20:00+00:00",
                completed_at="2026-08-16T16:20:00+00:00",
            )
            payload = {
                "source": "outlook",
                "completed_at": receipt["completed_at"],
                "scanned_count": 0,
                "accepted_count": 0,
                "cursor": receipt["cursor"],
                "service_receipt": receipt,
            }

            first = store.record_ingest_success(payload)
            replay = store.record_ingest_success(payload)

            self.assertFalse(first["idempotent_replay"])
            self.assertTrue(replay["idempotent_replay"])
            self.assertEqual(first["cursor_version"], 1)
            self.assertEqual(replay["cursor_version"], 1)
            self.assertEqual(store.ingest_state("outlook")["receipt_id"], receipt["receipt_id"])

    def test_unknown_or_mismatched_receipt_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary)
            receipt = self._receipt(
                store,
                cursor="2026-08-16T16:20:00+00:00",
                completed_at="2026-08-16T16:20:00+00:00",
            )
            with self.assertRaisesRegex(IngestCursorConflict, "exact service receipt"):
                store.record_ingest_success({
                    "source": "outlook",
                    "completed_at": receipt["completed_at"],
                    "scanned_count": 0,
                    "accepted_count": 0,
                    "cursor": receipt["cursor"],
                })
            with self.assertRaisesRegex(IngestCursorConflict, "unknown or mismatched"):
                store.record_ingest_success({
                    "source": "outlook",
                    "completed_at": receipt["completed_at"],
                    "scanned_count": 0,
                    "accepted_count": 0,
                    "cursor": receipt["cursor"],
                    "service_receipt_id": receipt["receipt_id"],
                    "service_receipt_sha256": "0" * 64,
                })
            self.assertIsNone(store.ingest_state("outlook")["cursor"])

    def test_regressive_and_reordered_receipts_do_not_move_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary)
            first_receipt = self._receipt(
                store,
                cursor="2026-08-16T16:20:00+00:00",
                completed_at="2026-08-16T16:20:00+00:00",
            )
            second_receipt = self._receipt(
                store,
                cursor="2026-08-17T16:20:00+00:00",
                completed_at="2026-08-17T16:20:00+00:00",
            )
            def payload(receipt: dict[str, object]) -> dict[str, object]:
                return {
                    "source": "outlook",
                    "completed_at": receipt["completed_at"],
                    "scanned_count": receipt["scanned_count"],
                    "accepted_count": receipt["accepted_count"],
                    "cursor": receipt["cursor"],
                    "service_receipt": receipt,
                }

            store.record_ingest_success(payload(first_receipt))
            store.record_ingest_success(payload(second_receipt))
            with self.assertRaisesRegex(IngestCursorConflict, "stale or regressive"):
                store.record_ingest_success(payload(first_receipt))
            self.assertEqual(
                store.ingest_state("outlook")["cursor"],
                "2026-08-17T16:20:00+00:00",
            )

            same_time = self._receipt(
                store,
                cursor="2026-08-17T16:20:00+00:00/other",
                completed_at="2026-08-17T16:20:00+00:00",
            )
            with self.assertRaisesRegex(IngestCursorConflict, "already committed"):
                store.record_ingest_success(payload(same_time))

    def test_failed_receipt_update_rolls_back_cursor_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            store = CashbackEventStore(database)
            receipt = self._receipt(
                store,
                cursor="2026-08-16T16:20:00+00:00",
                completed_at="2026-08-16T16:20:00+00:00",
            )
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    CREATE TRIGGER fail_receipt_commit
                    BEFORE UPDATE OF state ON ingest_receipts
                    BEGIN
                        SELECT RAISE(ABORT, 'synthetic receipt commit fault');
                    END
                    """
                )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "synthetic receipt commit fault"):
                store.record_ingest_success({
                    "source": "outlook",
                    "completed_at": receipt["completed_at"],
                    "scanned_count": 0,
                    "accepted_count": 0,
                    "cursor": receipt["cursor"],
                    "service_receipt": receipt,
                })

            restarted = CashbackEventStore(database)
            self.assertIsNone(restarted.ingest_state("outlook")["cursor"])
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT state FROM ingest_receipts WHERE receipt_id = ?",
                        (receipt["receipt_id"],),
                    ).fetchone()[0],
                    "READY",
                )

    def test_ingest_state_migration_adds_cursor_proof_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    CREATE TABLE ingest_state (
                        source TEXT PRIMARY KEY,
                        last_success_at TEXT NOT NULL,
                        scanned_count INTEGER NOT NULL,
                        accepted_count INTEGER NOT NULL,
                        cursor TEXT,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            CashbackEventStore(database)
            with sqlite3.connect(database) as connection:
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(ingest_state)").fetchall()
                }
            self.assertTrue({"cursor_version", "receipt_id", "receipt_sha256"} <= columns)


if __name__ == "__main__":
    unittest.main()
