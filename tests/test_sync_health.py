from __future__ import annotations

import json
import tempfile
from datetime import date, datetime
from pathlib import Path
from unittest import TestCase

from finance_tracker.cashback_events import CashbackEventStore, build_live_dashboard
from finance_tracker.sync_health import scheduled_sync_health
from finance_tracker.web_push import notification_candidates


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value)


class ScheduledSyncHealthTests(TestCase):
    def health(self, now, last="2026-09-04T08:10:00+04:00", **kwargs):
        return scheduled_sync_health(last, now=instant(now), grace_minutes=90, ingest_source="outlook:rakbank", **kwargs)

    def test_previous_success_stays_current_before_due_and_during_grace(self):
        for now in ("2026-09-05T07:00:00+04:00", "2026-09-05T08:06:00+04:00", "2026-09-05T09:34:59+04:00"):
            with self.subTest(now=now):
                state = self.health(now)
                self.assertFalse(state["is_stale"])
                self.assertEqual(state["expected_due_at"], "2026-09-04T04:05:00+00:00")

    def test_missing_due_run_is_overdue_at_grace_deadline(self):
        state = self.health("2026-09-05T09:35:00+04:00")
        self.assertTrue(state["is_stale"])
        self.assertEqual(state["check_status"], "OVERDUE")
        self.assertEqual(state["expected_due_at"], "2026-09-05T04:05:00+00:00")
        self.assertEqual(state["next_scheduled_check_at"], "2026-09-06T04:05:00+00:00")

    def test_schedule_due_not_rolling_age_controls_status(self):
        state = self.health("2026-09-05T23:00:00+04:00", last="2026-09-05T08:06:00+04:00")
        self.assertFalse(state["is_stale"])
        early = self.health("2026-09-05T09:36:00+04:00", last="2026-09-05T08:00:00+04:00")
        self.assertTrue(early["is_stale"])

    def test_late_success_recovers_and_timezone_offsets_compare_as_instants(self):
        state = self.health("2026-09-05T06:10:00+00:00", last="2026-09-05T10:09:00+04:00")
        self.assertFalse(state["is_stale"])
        self.assertEqual(state["check_status"], "CURRENT")

    def test_portable_daily_schedule_uses_configured_timezone(self):
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary) / "ingestion.json"
            p.write_text(json.dumps({"outlook": {"cursor_source": "outlook", "schedule": {
                "cadence": "DAILY_MORNING_PER_ACTIVE_BANK", "timezone": "America/Los_Angeles",
                "active_bank_slots": [{"source": "outlook:rakbank", "times": ["08:05"]}],
            }}}))
            state = self.health("2026-09-05T16:36:00+00:00", last="2026-09-04T08:10:00-07:00", config_path=p)
            self.assertTrue(state["is_stale"])
            self.assertEqual(state["expected_due_at"], "2026-09-05T15:05:00+00:00")

    def test_missing_and_invalid_check_timestamps_do_not_claim_success(self):
        for value, expected in ((None, "NEVER_CHECKED"), ("invalid", "INVALID_CHECK_TIMESTAMP"), ("2026-09-06T10:00:00+04:00", "INVALID_CHECK_TIMESTAMP")):
            state = self.health("2026-09-05T10:00:00+04:00", last=value)
            self.assertTrue(state["is_stale"])
            self.assertEqual(state["check_status"], expected)

    def test_zero_event_receipt_keeps_dashboard_and_push_healthy_until_next_due(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.upsert([{
                "source_event_id": "old-purchase", "occurred_at": "2026-08-20T12:00:00+04:00",
                "card_code": "RAK_WORLD", "amount_aed": "10", "merchant": "Shop",
                "purchase_type": "GENERAL", "channel": "ONLINE", "bucket_code": "RAK_STANDARD",
            }])
            checked = "2026-09-04T08:06:00+04:00"
            payload = {"source": "outlook:rakbank", "completed_at": checked, "scanned_count": 0, "accepted_count": 0, "cursor": "quiet-day"}
            receipt = store.create_ingest_receipt(payload)
            store.record_ingest_success({**payload, "service_receipt": receipt})
            for now, stale in (("2026-09-04T23:00:00+04:00", False), ("2026-09-05T09:34:00+04:00", False), ("2026-09-05T09:36:00+04:00", True)):
                dashboard = build_live_dashboard(store, date(2026, 9, 5), ingest_source="outlook:rakbank", now=instant(now))
                state = dashboard["data_status"]
                self.assertEqual(state["last_successful_check_at"], checked)
                self.assertEqual(state["last_accepted_count"], 0)
                self.assertEqual(state["event_count"], 1)
                self.assertEqual(state["last_event_at"], "2026-08-20T12:00:00+04:00")
                self.assertEqual(state["is_stale"], stale)
                candidates, _ = notification_candidates(dashboard, None)
                pushes = [p for p in candidates if p.key.startswith("feed:stale:")]
                self.assertEqual(bool(pushes), stale)
                if stale:
                    self.assertIn("scheduled transaction check", pushes[0].body)
                    self.assertIn("90-minute grace", pushes[0].body)
                    self.assertNotIn("within 90 minutes", pushes[0].body)
            recovery = {**payload, "completed_at": "2026-09-05T10:00:00+04:00", "cursor": "quiet-day-recovered"}
            receipt = store.create_ingest_receipt(recovery)
            store.record_ingest_success({**recovery, "service_receipt": receipt})
            dashboard = build_live_dashboard(store, date(2026, 9, 5), ingest_source="outlook:rakbank", now=instant("2026-09-05T10:01:00+04:00"))
            self.assertFalse(dashboard["data_status"]["is_stale"])
            self.assertFalse(any(p.key.startswith("feed:stale:") for p in notification_candidates(dashboard, None)[0]))
