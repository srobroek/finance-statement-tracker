import json
import tempfile
import unittest
from pathlib import Path

from finance_tracker.notification_sources import (
    load_notification_sources,
    validate_notification_adapter_coverage,
)
from finance_tracker.notifications import DEFAULT_NOTIFICATION_ADAPTERS


class NotificationSourceTests(unittest.TestCase):
    def test_registry_declares_active_adapter_and_unmatchable_placeholders(self) -> None:
        sources = load_notification_sources(Path("config/transaction-email-sources.json"))
        validate_notification_adapter_coverage(
            sources,
            (adapter.code for adapter in DEFAULT_NOTIFICATION_ADAPTERS),
        )
        active = [source for source in sources if source.active]
        placeholders = [source for source in sources if not source.active]
        self.assertEqual([source.code for source in active], ["ADCB_CARD_OTP"])
        self.assertEqual(len(placeholders), 3)
        for source in placeholders:
            with self.subTest(source=source.code):
                self.assertIsNone(source.adapter)
                self.assertFalse(source.senders)
                self.assertFalse(source.subjects)

    def test_placeholder_cannot_accidentally_become_matchable(self) -> None:
        payload = {
            "schema_version": 1,
            "sources": [
                {
                    "code": "UNKNOWN_BANK",
                    "institution": "Unknown",
                    "card_code": "UNKNOWN_CARD",
                    "status": "PLACEHOLDER",
                    "evidence_semantics": "UNKNOWN",
                    "adapter": None,
                    "senders": ["guess@example.com"],
                    "subjects": [],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sources.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must remain unmatchable"):
                load_notification_sources(path)

    def test_adapter_coverage_rejects_missing_implementation(self) -> None:
        sources = load_notification_sources(Path("config/transaction-email-sources.json"))
        with self.assertRaisesRegex(ValueError, "not implemented"):
            validate_notification_adapter_coverage(sources, ())


if __name__ == "__main__":
    unittest.main()
