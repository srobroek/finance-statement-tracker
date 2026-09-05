from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from finance_tracker.web_push import (
    PushCandidate,
    WebPushDispatcher,
    WebPushStore,
    notification_candidates,
)


def _subscription(endpoint: str = "https://push.example/subscription-1") -> dict[str, object]:
    return {
        "endpoint": endpoint,
        "keys": {"p256dh": "valid_public_key-1", "auth": "valid_auth-1"},
    }


def _dashboard(*, grocery_card: str = "RAK_WORLD") -> dict[str, object]:
    return {
        "cards": [{
            "card": "RAK_WORLD",
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
        }],
        "alerts": [
            {
                "key": "bucket:RAK_WORLD:RAK_GROCERY:full",
                "title": "RAK grocery is full",
                "detail": "Use another card for groceries.",
            },
            {
                "key": "close:RAK_WORLD:2026-08-01:2026-08-31",
                "title": "RAK target is not secured",
                "detail": "AED 500 remains with 7 days until cycle close.",
            },
        ],
        "routing_graphs": [{
            "label": "Groceries",
            "channel": "PHYSICAL_POS",
            "currency": "AED",
            "use_card": grocery_card,
        }],
        "data_status": {"acknowledged_alerts": [], "is_stale": False},
    }


class WebPushStoreTests(unittest.TestCase):
    def test_subscription_lifecycle_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = WebPushStore(Path(temporary) / "cashback.sqlite3")
            result = store.upsert_subscription(_subscription(), "Safari iOS")

            self.assertTrue(result["enabled"])
            self.assertEqual(len(store.subscriptions()), 1)
            self.assertEqual(store.stats()["subscription_count"], 1)
            self.assertTrue(store.remove_subscription(result["endpoint"])["removed"])
            self.assertEqual(store.stats()["subscription_count"], 0)

            with self.assertRaisesRegex(ValueError, "HTTPS"):
                store.upsert_subscription(_subscription("http://push.example/insecure"))

    def test_candidates_cover_full_bucket_close_warning_and_routing_change(self) -> None:
        initial = _dashboard(grocery_card="RAK_WORLD")
        candidates, routing = notification_candidates(initial, None)
        self.assertEqual(
            {candidate.title for candidate in candidates},
            {"RAK grocery is full", "RAK target is not secured"},
        )

        changed = _dashboard(grocery_card="SC_PLATINUM_X")
        candidates, _ = notification_candidates(changed, routing)
        routing_candidate = next(item for item in candidates if item.title == "Card routing changed")
        self.assertIn("Groceries", routing_candidate.body)
        self.assertIn("Sc Platinum X", routing_candidate.body)

    def test_declarative_payload_is_sent_once_per_subscription_and_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            calls: list[dict[str, object]] = []

            def sender(**kwargs: object) -> None:
                calls.append(kwargs)

            store = WebPushStore(Path(temporary) / "cashback.sqlite3")
            store.upsert_subscription(_subscription())
            dispatcher = WebPushDispatcher(
                store,
                public_key="vapid-public",
                private_key="vapid-private",
                subject="https://cashback.example",
                public_url="https://cashback.example",
                sender=sender,
            )
            candidate = PushCandidate(
                "bucket:full:2026-08",
                "Bucket full",
                "Move this spend to another card.",
                "cards",
            )

            self.assertEqual(dispatcher.send([candidate]), {"sent": 1, "failed": 0, "skipped": 0})
            self.assertEqual(dispatcher.send([candidate]), {"sent": 0, "failed": 0, "skipped": 1})
            self.assertEqual(len(calls), 1)
            payload = json.loads(str(calls[0]["data"]))
            self.assertEqual(payload["web_push"], 8030)
            self.assertEqual(payload["notification"]["navigate"], "https://cashback.example/?screen=cards")
            self.assertEqual(payload["notification"]["tag"], candidate.key)
            self.assertEqual(calls[0]["headers"], {"Urgency": "high"})

    def test_public_push_config_contains_no_subscription_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = WebPushStore(Path(temporary) / "cashback.sqlite3")
            store.upsert_subscription(_subscription())
            dispatcher = WebPushDispatcher(
                store,
                public_key="vapid-public",
                private_key="vapid-private",
                subject="https://cashback.example",
                public_url="https://cashback.example",
            )

            self.assertEqual(
                dispatcher.config(),
                {"enabled": True, "public_key": "vapid-public"},
            )

    def test_first_dashboard_does_not_create_a_routing_change_notification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            calls: list[dict[str, object]] = []
            store = WebPushStore(Path(temporary) / "cashback.sqlite3")
            store.upsert_subscription(_subscription())
            dispatcher = WebPushDispatcher(
                store,
                public_key="vapid-public",
                private_key="vapid-private",
                subject="https://cashback.example",
                public_url="https://cashback.example",
                sender=lambda **kwargs: calls.append(kwargs),
            )

            result = dispatcher.evaluate({
                "cards": [],
                "alerts": [],
                "routing_graphs": _dashboard()["routing_graphs"],
                "data_status": {},
            })
            self.assertEqual(result["sent"], 0)
            self.assertEqual(calls, [])

    def test_stale_feed_notification_is_episode_scoped_and_acknowledgeable(self) -> None:
        dashboard = _dashboard()
        dashboard["alerts"] = []
        dashboard["data_status"] = {
            "acknowledged_alerts": [],
            "is_stale": True,
            "stale_after_minutes": 90,
            "check_status": "OVERDUE",
            "check_timezone": "UTC",
            "expected_due_at": "2026-08-18T08:05:00+00:00",
            "last_successful_ingest_at": "2026-08-17T13:06:03+00:00",
        }
        candidates, _ = notification_candidates(dashboard, None)
        stale = next(item for item in candidates if item.title == "Cashback check overdue")
        self.assertEqual(stale.key, "feed:stale:2026-08-17T13:06:03+00:00")
        self.assertEqual(stale.body, "Due 18 Aug, 08:05; last checked 17 Aug, 13:06 (UTC).")

        dashboard["data_status"]["acknowledged_alerts"] = ["feed:stale"]
        candidates, _ = notification_candidates(dashboard, None)
        self.assertNotIn("Cashback check overdue", {item.title for item in candidates})

    def test_schedule_and_clock_errors_do_not_claim_a_missed_run(self) -> None:
        for status, expected in (("SCHEDULE_UNCONFIGURED", "No active check schedule is configured."), ("INVALID_CHECK_TIMESTAMP", "The last check time is invalid.")):
            dashboard = _dashboard()
            dashboard["alerts"] = []
            dashboard["data_status"] = {"is_stale": True, "check_status": status}
            candidates, _ = notification_candidates(dashboard, None)
            check = next(item for item in candidates if item.key.startswith("feed:stale:"))
            self.assertEqual(check.title, "Cashback sync needs attention")
            self.assertEqual(check.body, expected)
            self.assertNotIn("overdue", check.body)


if __name__ == "__main__":
    unittest.main()
