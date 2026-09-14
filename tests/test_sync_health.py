from __future__ import annotations

import json
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any
from unittest import TestCase

from finance_tracker.cashback_events import CashbackEventStore, build_live_dashboard
from finance_tracker.sync_health import scheduled_sync_health


class ScheduledSyncHealthTests(TestCase):
    @staticmethod
    def at(value: str) -> datetime:
        return datetime.fromisoformat(value)

    def health(
        self,
        now: str,
        last: str | None = "2026-09-04T08:10:00+04:00",
        **kwargs: Any,
    ) -> dict[str, object]:
        return scheduled_sync_health(
            last,
            now=self.at(now),
            grace_minutes=90,
            ingest_source="outlook:rakbank",
            **kwargs,
        )

    def test_daily_dubai_schedule_and_grace_boundary_are_deterministic(self) -> None:
        before_deadline = self.health("2026-09-05T09:34:59+04:00")
        self.assertEqual(before_deadline["check_status"], "CURRENT")
        self.assertFalse(before_deadline["is_stale"])
        self.assertEqual(
            before_deadline["expected_due_at"], "2026-09-04T04:05:00+00:00"
        )

        at_deadline = self.health("2026-09-05T09:35:00+04:00")
        self.assertEqual(at_deadline["check_status"], "OVERDUE")
        self.assertTrue(at_deadline["is_stale"])
        self.assertEqual(
            at_deadline["next_scheduled_check_at"], "2026-09-06T04:05:00+00:00"
        )

    def test_successful_late_check_recovers_and_transaction_age_is_irrelevant(
        self,
    ) -> None:
        state = self.health(
            "2026-09-05T23:00:00+04:00",
            last="2026-09-05T10:00:00+04:00",
        )
        self.assertEqual(state["check_status"], "CURRENT")
        self.assertFalse(state["is_stale"])

    def test_empty_receipt_is_authoritative_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.upsert(
                [
                    {
                        "source_event_id": "old-purchase",
                        "occurred_at": "2026-08-20T12:00:00+04:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "10",
                        "merchant": "Shop",
                        "purchase_type": "GENERAL",
                        "channel": "ONLINE",
                    }
                ]
            )
            payload = {
                "source": "outlook:rakbank",
                "completed_at": "2026-09-04T08:06:00+04:00",
                "scanned_count": 0,
                "accepted_count": 0,
                "cursor": "quiet-day",
            }
            receipt = store.create_ingest_receipt(payload)
            store.record_ingest_success({**payload, "service_receipt": receipt})
            dashboard = build_live_dashboard(
                store,
                date(2026, 9, 5),
                stale_after_minutes=90,
                ingest_source="outlook:rakbank",
                now=self.at("2026-09-05T09:34:00+04:00"),
            )
            status = dashboard["data_status"]
            self.assertEqual(status["check_status"], "CURRENT")
            self.assertFalse(status["is_stale"])
            self.assertEqual(
                status["last_successful_check_at"], "2026-09-04T04:06:00+00:00"
            )
            self.assertEqual(status["last_event_at"], "2026-08-20T08:00:00+00:00")

    def test_invalid_timestamp_and_schedule_configuration_fail_closed(self) -> None:
        invalid = self.health("2026-09-05T10:00:00+04:00", last="invalid")
        self.assertEqual(invalid["check_status"], "INVALID_CHECK_TIMESTAMP")
        self.assertTrue(invalid["is_stale"])

        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "ingestion.json"
            config.write_text(
                json.dumps({"outlook": {"schedule": {}}}), encoding="utf-8"
            )
            unconfigured = self.health(
                "2026-09-05T10:00:00+04:00",
                config_path=config,
            )
            self.assertEqual(unconfigured["check_status"], "SCHEDULE_UNCONFIGURED")
            self.assertTrue(unconfigured["is_stale"])

            config.write_text("{not-json", encoding="utf-8")
            error = self.health(
                "2026-09-05T10:00:00+04:00",
                config_path=config,
            )
            self.assertEqual(error["check_status"], "SCHEDULE_ERROR")
            self.assertTrue(error["is_stale"])

    def test_configured_timezone_is_used_for_due_and_next_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "ingestion.json"
            config.write_text(
                json.dumps(
                    {
                        "outlook": {
                            "cursor_source": "outlook",
                            "schedule": {
                                "cadence": "DAILY_MORNING_PER_ACTIVE_BANK",
                                "timezone": "America/Los_Angeles",
                                "active_bank_slots": [
                                    {"source": "outlook:rakbank", "times": ["08:05"]}
                                ],
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            state = self.health(
                "2026-09-05T16:36:00+00:00",
                last="2026-09-04T08:10:00-07:00",
                config_path=config,
            )
            self.assertEqual(state["check_status"], "OVERDUE")
            self.assertEqual(state["expected_due_at"], "2026-09-05T15:05:00+00:00")

    def test_malformed_timezone_keys_and_cross_source_receipts_fail_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "ingestion.json"
            schedule = {
                "cadence": "DAILY_MORNING_PER_ACTIVE_BANK",
                "timezone": "Asia/Dubai",
                "active_bank_slots": [
                    {"source": "outlook:rakbank", "times": ["08:05"]},
                    {"source": "outlook:other", "times": ["09:05"]},
                ],
            }
            config.write_text(
                json.dumps(
                    {"outlook": {"cursor_source": "outlook", "schedule": schedule}}
                ),
                encoding="utf-8",
            )
            for timezone_name in ("/Asia/Dubai", "Asia/Dubai/"):
                schedule["timezone"] = timezone_name
                config.write_text(
                    json.dumps(
                        {"outlook": {"cursor_source": "outlook", "schedule": schedule}}
                    ),
                    encoding="utf-8",
                )
                malformed = self.health(
                    "2026-09-05T10:00:00+04:00",
                    config_path=config,
                )
                self.assertEqual(malformed["check_status"], "SCHEDULE_ERROR")
                self.assertTrue(malformed["is_stale"])

            schedule["timezone"] = "Asia/Dubai"
            config.write_text(
                json.dumps(
                    {"outlook": {"cursor_source": "outlook", "schedule": schedule}}
                ),
                encoding="utf-8",
            )
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            receipt_payload = {
                "source": "outlook:other",
                "completed_at": "2026-09-05T09:06:00+04:00",
                "scanned_count": 0,
                "accepted_count": 0,
                "cursor": "other-check",
            }
            receipt = store.create_ingest_receipt(receipt_payload)
            store.record_ingest_success({**receipt_payload, "service_receipt": receipt})
            rakbank = build_live_dashboard(
                store,
                date(2026, 9, 5),
                ingest_source="outlook:rakbank",
                check_schedule_config_path=config,
                now=self.at("2026-09-05T10:00:00+04:00"),
            )["data_status"]
            self.assertEqual(rakbank["check_status"], "NEVER_CHECKED")
            self.assertIsNone(rakbank["last_successful_check_at"])
            self.assertIsNone(rakbank["last_ingest_source"])

            other = build_live_dashboard(
                store,
                date(2026, 9, 5),
                ingest_source="outlook:other",
                check_schedule_config_path=config,
                now=self.at("2026-09-05T10:00:00+04:00"),
            )["data_status"]
            self.assertEqual(other["check_status"], "CURRENT")
            self.assertEqual(other["last_ingest_source"], "outlook:other")


if __name__ == "__main__":
    import unittest

    unittest.main()
