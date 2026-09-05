from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from finance_tracker.cashback_events import CashbackEventStore


class _CoordinatedConnection(sqlite3.Connection):
    coordination: dict[str, threading.Event]

    def execute(self, sql: str, parameters=(), /):  # type: ignore[no-untyped-def]
        normalized = " ".join(sql.split()).upper()
        role = threading.current_thread().name
        if role == "second-upsert" and normalized == "BEGIN IMMEDIATE":
            self.coordination["second_begin"].set()
            result = super().execute(sql, parameters)
            self.coordination["second_acquired"].set()
            return result
        result = super().execute(sql, parameters)
        if role == "first-upsert" and normalized.startswith(
            "SELECT * FROM CASHBACK_EVENTS WHERE SOURCE_EVENT_ID"
        ):
            self.coordination["first_read"].set()
            if not self.coordination["release_first"].wait(5):
                raise AssertionError("test did not release the first transaction")
        return result


class _CoordinatedStore(CashbackEventStore):
    def __init__(self, path: Path, coordination: dict[str, threading.Event]) -> None:
        self.coordination = coordination
        super().__init__(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=5,
            factory=_CoordinatedConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.coordination = self.coordination  # type: ignore[attr-defined]
        return connection


class CashbackConcurrencyTests(unittest.TestCase):
    def test_duplicate_upserts_serialize_before_the_replay_read(self) -> None:
        """A duplicate request waits, then observes the committed first insert."""
        coordination = {
            name: threading.Event()
            for name in ("first_read", "release_first", "second_begin", "second_acquired")
        }
        event = {
            "source_event_id": "concurrent-replay:1",
            "occurred_at": "2026-08-16T12:30:00+04:00",
            "card_code": "RAK_WORLD",
            "amount_aed": "25.50",
            "merchant": "Concurrent Replay Merchant",
        }
        results: dict[str, dict[str, int]] = {}
        errors: list[BaseException] = []

        with tempfile.TemporaryDirectory() as temporary:
            store = _CoordinatedStore(Path(temporary) / "events.sqlite3", coordination)

            def upsert(role: str) -> None:
                try:
                    results[role] = store.upsert([event])
                except BaseException as error:  # capture worker failures for the main assertion
                    errors.append(error)

            first = threading.Thread(target=upsert, args=("first",), name="first-upsert")
            second = threading.Thread(target=upsert, args=("second",), name="second-upsert")
            first.start()
            self.assertTrue(coordination["first_read"].wait(2), "first transaction never reached its replay read")
            second.start()
            self.assertTrue(coordination["second_begin"].wait(2), "second transaction never attempted its write lock")
            self.assertFalse(
                coordination["second_acquired"].wait(0.2),
                "second request entered the replay read while the first transaction was open",
            )
            coordination["release_first"].set()
            first.join(5)
            second.join(5)

        self.assertFalse(first.is_alive() or second.is_alive(), "concurrent upserts did not finish")
        self.assertEqual(errors, [])
        self.assertEqual(results["first"]["inserted"], 1)
        self.assertEqual(results["second"]["unchanged"], 1)
        self.assertEqual(results["second"]["inserted"], 0)


if __name__ == "__main__":
    unittest.main()
