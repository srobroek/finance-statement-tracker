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

    def test_rakbank_transaction_extracts_verified_fields_and_uses_configured_normalization(self):
        message = json.loads(
            Path("tests/fixtures/rakbank-card-transaction.json").read_text(encoding="utf-8")
        )
        result = parse_outlook_notifications(
            [message], {"7210": "RAK_WORLD"}, self.rules
        )
        self.assertEqual(result.accepted_count, 1)
        event = result.events[0]
        self.assertEqual(event["merchant"], "Amazon")
        self.assertEqual(event["amount_aed"], "41.49")
        self.assertEqual(event["currency"], "AED")
        self.assertEqual(event["occurred_at"], "2026-08-17T00:00:00+00:00")
        self.assertEqual(event["card_code"], "RAK_WORLD")
        self.assertEqual(event["purchase_type"], "AMAZON")
        self.assertEqual(event["channel"], "ONLINE")
        self.assertEqual(event["status"], "PROVISIONAL")
        self.assertTrue(event["review_required"])

    def test_rakbank_non_transaction_subject_is_not_accepted(self):
        message = json.loads(
            Path("tests/fixtures/rakbank-card-transaction.json").read_text(encoding="utf-8")
        )
        message["id"] = "rakbank-apple-pay-registration"
        message["subject"] = "Your RAKBANK Card is successfully registered on Apple Pay"
        message["bodyPreview"] = "Your Card ending 7210 is registered on Apple Pay."
        result = parse_outlook_notifications(
            [message], {"7210": "RAK_WORLD"}, self.rules
        )
        self.assertEqual(result.accepted_count, 0)
        self.assertEqual(result.skipped[0]["reason"], "UNSUPPORTED_NOTIFICATION")

    def test_rakbank_matching_subject_with_incomplete_body_is_rejected(self):
        message = json.loads(
            Path("tests/fixtures/rakbank-card-transaction.json").read_text(encoding="utf-8")
        )
        message["id"] = "rakbank-malformed-transaction"
        message["bodyPreview"] = "A card transaction occurred, but no transaction facts are present."
        result = parse_outlook_notifications(
            [message], {"7210": "RAK_WORLD"}, self.rules
        )
        self.assertEqual(result.accepted_count, 0)
        self.assertIn("PARSE_ERROR:RAKBANK transaction email", result.skipped[0]["reason"])

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

    def test_unverified_placeholder_format_has_no_financial_effect(self):
        placeholder = {
            "id": "rak-placeholder-message",
            "subject": "Possible RAKBANK card transaction",
            "sender": {"emailAddress": {"address": "unverified@example.com"}},
            "receivedDateTime": "2026-08-16T10:30:00Z",
            "bodyPreview": "Unverified placeholder format with no trusted parser contract.",
        }
        result = parse_outlook_notifications(
            [placeholder], {"0000": "RAK_WORLD"}, self.rules
        )
        self.assertEqual(result.accepted_count, 0)
        self.assertEqual(result.skipped[0]["reason"], "UNSUPPORTED_NOTIFICATION")


if __name__ == "__main__":
    unittest.main()
