from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from finance_tracker.cashback_events import CashbackEventStore, IngestCursorConflict


class ReceiptDurabilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "events.sqlite3"
        self.store = CashbackEventStore(self.path)
        self.fields = {"source": "outlook:rakbank", "completed_at": "2026-09-05T12:00:00+00:00",
                       "cursor": "2026-09-05T12:00:00+00:00"}
        self.event = json.loads((Path(__file__).parent / "fixtures/cashback-event.sample.json").read_text())
        self.event["source_event_id"] = "synthetic-notification:0"

    def mixed_scan(self):
        child = self.store.ingest_transaction({**self.fields, "event": self.event})["service_receipt"]
        return {
            **self.fields, "scanned_count": 3, "accepted_count": 1, "ignored_count": 1, "review_count": 1,
            "receipts": [{key: child[key] for key in ("receipt_id", "receipt_sha256")}],
            "message_dispositions": [
                {"message_id": "synthetic-notification", "status": "ACCEPTED", "source_event_id": "synthetic-notification:0"},
                {"message_id": "synthetic-fx-review", "status": "REVIEW", "reason": "MISSING_AED_SETTLEMENT"},
                {"message_id": "synthetic-unrelated", "status": "IGNORED", "reason": "UNSUPPORTED_NOTIFICATION"},
            ],
        }

    def commit(self, receipt):
        return self.store.record_ingest_success({**self.fields, "scanned_count": receipt["scanned_count"],
            "accepted_count": receipt["accepted_count"], "service_receipt": receipt})

    def read(self, receipt):
        return self.store.ingest_receipt(receipt["receipt_id"], receipt["receipt_sha256"])

    def test_review_identity_and_reason_survive_restart_commit_and_replay(self):
        scan = self.mixed_scan()
        receipt = self.store.combine_transaction_receipts(scan)["service_receipt"]
        self.store = CashbackEventStore(self.path)
        observed = self.read(receipt)
        self.assertEqual(observed, receipt)
        self.assertEqual(observed["scan_dispositions"]["review_count"], 1)
        held = next(row for row in observed["scan_dispositions"]["message_dispositions"] if row["status"] == "REVIEW")
        self.assertEqual(held, {"message_id": "synthetic-fx-review", "status": "REVIEW", "reason": "MISSING_AED_SETTLEMENT"})
        self.assertEqual(self.store.combine_transaction_receipts(scan)["service_receipt"], receipt)
        self.assertFalse(self.commit(receipt)["idempotent_replay"])
        self.store = CashbackEventStore(self.path)
        self.assertEqual(self.read(receipt)["state"], "COMMITTED")
        self.assertEqual(self.read(receipt)["scan_dispositions"], receipt["scan_dispositions"])
        self.assertTrue(self.commit(receipt)["idempotent_replay"])

    def test_legacy_migration_preserves_rows_and_requires_exact_context_replay(self):
        scan = self.mixed_scan()
        receipt = self.store.combine_transaction_receipts(scan)["service_receipt"]
        with sqlite3.connect(self.path) as db:
            db.execute("ALTER TABLE ingest_receipts DROP COLUMN receipt_payload_json")
            old_rows = db.execute("SELECT * FROM ingest_receipts ORDER BY receipt_id").fetchall()
        self.store = CashbackEventStore(self.path)
        self.store = CashbackEventStore(self.path)  # Migration itself is idempotent.
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT * FROM ingest_receipts ORDER BY receipt_id").fetchall()
        self.assertEqual([row[:-1] for row in rows], old_rows)
        self.assertTrue(all(row[-1] is None for row in rows))
        with self.assertRaisesRegex(IngestCursorConflict, "exact source replay"):
            self.commit(receipt)
        self.assertIsNone(self.store.ingest_state(self.fields["source"])["cursor"])
        # Caller must reconstruct the exact old payload from immutable source evidence.
        replayed = self.store.combine_transaction_receipts(scan)["service_receipt"]
        self.assertEqual(replayed, receipt)
        self.assertEqual(self.read(receipt)["scan_dispositions"], receipt["scan_dispositions"])
        self.commit(receipt)

    def test_corrupted_durable_review_payload_cannot_commit_cursor(self):
        receipt = self.store.combine_transaction_receipts(self.mixed_scan())["service_receipt"]
        with sqlite3.connect(self.path) as db:
            raw = json.loads(db.execute("SELECT receipt_payload_json FROM ingest_receipts WHERE receipt_id=?", (receipt["receipt_id"],)).fetchone()[0])
            raw["scan_dispositions"]["message_dispositions"][0]["reason"] = "ALTERED"
            db.execute("UPDATE ingest_receipts SET receipt_payload_json=? WHERE receipt_id=?", (json.dumps(raw), receipt["receipt_id"]))
        with self.assertRaisesRegex(IngestCursorConflict, "digest mismatch"):
            self.read(receipt)
        with self.assertRaisesRegex(IngestCursorConflict, "digest mismatch"):
            self.commit(receipt)
        self.assertIsNone(self.store.ingest_state(self.fields["source"])["cursor"])

    def test_durable_payload_must_match_receipt_row_binding(self):
        receipt = self.store.combine_transaction_receipts(self.mixed_scan())["service_receipt"]
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE ingest_receipts SET scanned_count=99 WHERE receipt_id=?", (receipt["receipt_id"],))
        with self.assertRaisesRegex(IngestCursorConflict, "row binding mismatch"):
            self.commit(receipt)
        self.assertIsNone(self.store.ingest_state(self.fields["source"])["cursor"])

    def test_change_after_aggregate_readback_blocks_first_commit(self):
        receipt = self.store.combine_transaction_receipts(self.mixed_scan())["service_receipt"]
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE cashback_events SET bucket_code='MANUAL_OVERRIDE' WHERE source_event_id=?", (self.event["source_event_id"],))
        with self.assertRaisesRegex(IngestCursorConflict, "changed before cursor commit"):
            self.commit(receipt)
        self.assertIsNone(self.store.ingest_state(self.fields["source"])["cursor"])

    def test_committed_replay_preserves_subsequent_manual_classification(self):
        receipt = self.store.combine_transaction_receipts(self.mixed_scan())["service_receipt"]
        self.commit(receipt)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE cashback_events SET bucket_code='MANUAL_OVERRIDE' WHERE source_event_id=?", (self.event["source_event_id"],))
        self.assertTrue(self.commit(receipt)["idempotent_replay"])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT bucket_code FROM cashback_events WHERE source_event_id=?", (self.event["source_event_id"],)).fetchone()[0], "MANUAL_OVERRIDE")


if __name__ == "__main__":
    unittest.main()
