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

    def test_adcb_authorization_is_live_traceable_and_classified(self):
        result = parse_outlook_notifications(
            [self.message], {"8833": "ADCB_CASHBACK"}, self.rules
        )
        self.assertEqual(result.accepted_count, 1)
        event = result.events[0]
        self.assertEqual(event["source_event_id"], "outlook-message-1:0")
        self.assertEqual(event["merchant"], "Mollak")
        self.assertEqual(event["purchase_type"], "GENERAL")
        self.assertEqual(event["status"], "ACTIVE")
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
        self.assertEqual(event["bucket_code"], "RAK_STANDARD")
        self.assertEqual(event["status"], "ACTIVE")
        self.assertFalse(event["review_required"])

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

    def test_rakbank_unresolved_retail_uses_configured_apple_pay_default(self):
        message = json.loads(
            Path("tests/fixtures/rakbank-card-transaction-unknown-channel.json").read_text(
                encoding="utf-8"
            )
        )
        result = parse_outlook_notifications(
            [message], {"7210": "RAK_WORLD"}, self.rules
        )
        self.assertEqual(result.accepted_count, 1)
        event = result.events[0]
        self.assertEqual(event["amount_aed"], "16.00")
        self.assertEqual(event["merchant"], "Best of Vends")
        self.assertEqual(event["channel"], "APPLE_PAY_POS")
        self.assertEqual(event["bucket_code"], "RAK_DINING")
        self.assertIn("channel-config-default", event["tags"])
        self.assertFalse(event["review_required"])

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


    def test_observed_rakbank_charged_template_preserves_identity_and_aed(self):
        # Redacted structural fixture from the real issuer template: no private
        # message ID, merchant, card number, balance, or original body retained.
        message = {
            "id": "redacted-rak-charged",
            "sender": {"emailAddress": {"address": "alerts@rakbank.ae"}},
            "subject": "An update on your Card transaction",
            "receivedDateTime": "2026-08-27T01:36:29Z",
            "bodyPreview": "AED 12.34 is charged on your Credit Card 000000******0000 from REDACTED MERCHANT on 26/08.",
        }
        result = parse_outlook_notifications([message], {"0000": "RAK_WORLD"}, self.rules)
        self.assertEqual(result.accepted_count, 1)
        event = result.events[0]
        self.assertEqual(event["source_event_id"], message["id"] + ":0")
        self.assertEqual(event["amount_aed"], "12.34")
        self.assertEqual(event["merchant"], "REDACTED MERCHANT")
        self.assertEqual(event["occurred_at"], "2026-08-26T00:00:00+00:00")
        self.assertEqual(event["event_type"], "PURCHASE")
        self.assertEqual(parse_outlook_notifications([message], {"0000": "RAK_WORLD"}, self.rules).events, result.events)

    def test_charged_foreign_and_reversal_skip_without_blocking_aed_batch(self):
        base = {
            "sender": {"emailAddress": {"address": "alerts@rakbank.ae"}},
            "subject": "An update on your Card transaction",
            "receivedDateTime": "2026-08-27T01:36:29Z",
        }
        foreign = {**base, "id": "foreign-charge", "bodyPreview": "EUR 12.34 is charged on your Credit Card 000000******0000 from REDACTED MERCHANT on 26/08. Your combined available balance is AED 50000.00."}
        reversal = {**base, "id": "reversal", "bodyPreview": "AED 1.00 is reversed on your Credit Card 000000******0000 from REDACTED MERCHANT on 26/08."}
        valid = {**base, "id": "valid-charge", "bodyPreview": "AED 12.34 is charged on your Credit Card 000000******0000 from REDACTED MERCHANT on 26/08."}
        result = parse_outlook_notifications([foreign, reversal, valid], {"0000": "RAK_WORLD"}, self.rules)
        self.assertEqual(result.scanned_count, 3)
        self.assertEqual(result.accepted_count, 1)
        self.assertEqual(result.events[0]["source_event_id"], "valid-charge:0")
        self.assertEqual(result.skipped[0], {"message_id": "foreign-charge", "reason": "MISSING_AED_EQUIVALENT"})
        self.assertTrue(result.skipped[1]["reason"].startswith("PARSE_ERROR:"))
        self.assertEqual(result.skipped[1]["message_id"], "reversal")

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
