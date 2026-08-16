import json
import tempfile
import unittest
from pathlib import Path

from finance_tracker.actual_pipeline import load_compiled_rules
from finance_tracker.notifications import parse_outlook_notifications


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.rules = load_compiled_rules(Path("config/static-rules.seed.json"))
        self.message = {
            "id": "outlook-message-1",
            "subject": "ADCB Card Transaction OTP generated",
            "sender": {"emailAddress": {"address": "adcbalert@adcb.com"}},
            "receivedDateTime": "2026-08-16T10:30:00Z",
            "bodyPreview": (
                "OTP for transaction at Mollak for AED 3057.92 on your ADCB Credit Card "
                "XXX8833 sent to your registered mobile number."
            ),
            "web_link": "https://outlook.office.example/message-1",
        }

    def test_adcb_authorization_is_provisional_traceable_and_classified(self):
        result = parse_outlook_notifications(
            [self.message], {"8833": "ADCB_CASHBACK"}, self.rules
        )
        self.assertEqual(result.accepted_count, 1)
        event = result.events[0]
        self.assertEqual(event["source_event_id"], "outlook-message-1:0")
        self.assertEqual(event["merchant"], "Mollak")
        self.assertEqual(event["purchase_type"], "GENERAL")
        self.assertEqual(event["status"], "PROVISIONAL")
        self.assertTrue(event["review_required"])
        self.assertLess(event["confidence"], 0.8)
        self.assertTrue(event["decision_trace"])

    def test_foreign_authorization_without_aed_equivalent_is_not_ingested(self):
        foreign = json.loads(json.dumps(self.message))
        foreign["bodyPreview"] = (
            "OTP for transaction at Example for USD 124.25 on your ADCB Credit Card "
            "XXX8833 sent to your registered mobile number."
        )
        result = parse_outlook_notifications([foreign], {"8833": "ADCB_CASHBACK"}, self.rules)
        self.assertEqual(result.accepted_count, 0)
        self.assertEqual(result.skipped[0]["reason"], "MISSING_AED_EQUIVALENT")

    def test_cli_batch_shape_is_json_serializable(self):
        result = parse_outlook_notifications(
            [self.message], {"8833": "ADCB_CASHBACK"}, self.rules
        )
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "batch.json"
            target.write_text(json.dumps(result.to_dict()), encoding="utf-8")
            payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(payload["scanned_count"], 1)
        self.assertEqual(payload["accepted_count"], 1)


if __name__ == "__main__":
    unittest.main()
