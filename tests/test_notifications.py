import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from finance_tracker.actual_pipeline import load_compiled_rules
from finance_tracker.cashback_events import CashbackEventStore
from finance_tracker.fx_rates import FxQuote, NoUsableQuoteError
from finance_tracker.notifications import parse_outlook_notifications


class _Provider:
    source = "fixture"

    def __init__(self, result: FxQuote | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def quote(self, **kwargs: object) -> FxQuote:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


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
        self.foreign_message = json.loads(json.dumps(self.message))
        self.foreign_message["bodyPreview"] = (
            "OTP for transaction at Example for USD 124.25 on your ADCB Credit Card "
            "XXX8833 sent to your registered mobile number."
        )

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

    def test_foreign_authorization_persists_deterministic_aed_estimate_and_trace(self):
        provider = _Provider(
            FxQuote("USD", "AED", Decimal("3.692"), date(2026, 8, 16), "fixture")
        )
        result = parse_outlook_notifications(
            [self.foreign_message],
            {"8833": "ADCB_CASHBACK"},
            self.rules,
            fx_providers=(provider,),
        )
        self.assertEqual(result.accepted_count, 1)
        event = result.events[0]
        self.assertEqual(event["amount_aed"], "458.73")
        self.assertEqual(event["currency"], "USD")
        trace = next(
            item
            for item in event["decision_trace"]
            if item.get("trace_type") == "FX_CONVERSION"
        )
        self.assertEqual(trace["original_amount"], "124.25")
        self.assertEqual(trace["original_currency"], "USD")
        self.assertEqual(trace["amount_aed"], "458.73")
        self.assertEqual(trace["quote"]["source"], "fixture")
        self.assertEqual(len(provider.calls), 1)

    def test_foreign_replay_uses_persisted_trace_without_requoting(self):
        provider = _Provider(
            FxQuote("USD", "AED", Decimal("3.692"), date(2026, 8, 16), "fixture")
        )
        first = parse_outlook_notifications(
            [self.foreign_message],
            {"8833": "ADCB_CASHBACK"},
            self.rules,
            fx_providers=(provider,),
        )
        with tempfile.TemporaryDirectory() as folder:
            store = CashbackEventStore(Path(folder) / "events.sqlite3")
            self.assertEqual(store.upsert(first.events)["inserted"], 1)
            persisted = store.fx_replay_for_source_event_ids(["outlook-message-1:0"])
            self.assertIn("outlook-message-1:0", persisted)
            replay_provider = _Provider(
                NoUsableQuoteError("provider must not be called", errors=[])
            )
            replay = parse_outlook_notifications(
                [self.foreign_message],
                {"8833": "ADCB_CASHBACK"},
                self.rules,
                fx_providers=(replay_provider,),
                fx_replay={"outlook-message-1": persisted["outlook-message-1:0"]},
            )
            self.assertEqual(replay.events[0]["amount_aed"], "458.73")
            self.assertEqual(
                replay.events[0]["decision_trace"],
                first.events[0]["decision_trace"],
            )
            self.assertEqual(replay_provider.calls, [])
            self.assertEqual(
                store.upsert(replay.events),
                {"inserted": 0, "updated": 0, "unchanged": 1, "duplicates": 0},
            )

    def test_foreign_conversion_failure_produces_no_batch(self):
        provider = _Provider(NoUsableQuoteError("provider unavailable", errors=[]))
        with self.assertRaises(NoUsableQuoteError):
            parse_outlook_notifications(
                [self.foreign_message],
                {"8833": "ADCB_CASHBACK"},
                self.rules,
                fx_providers=(provider,),
            )
        self.assertEqual(len(provider.calls), 1)

    def test_rakbank_transaction_extracts_verified_fields_and_uses_configured_normalization(
        self,
    ):
        message = json.loads(
            Path("tests/fixtures/rakbank-card-transaction.json").read_text(
                encoding="utf-8"
            )
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
            Path("tests/fixtures/rakbank-card-transaction.json").read_text(
                encoding="utf-8"
            )
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
            Path(
                "tests/fixtures/rakbank-card-transaction-unknown-channel.json"
            ).read_text(encoding="utf-8")
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
            Path("tests/fixtures/rakbank-card-transaction.json").read_text(
                encoding="utf-8"
            )
        )
        message["id"] = "rakbank-malformed-transaction"
        message["bodyPreview"] = (
            "A card transaction occurred, but no transaction facts are present."
        )
        result = parse_outlook_notifications(
            [message], {"7210": "RAK_WORLD"}, self.rules
        )
        self.assertEqual(result.accepted_count, 0)
        self.assertIn(
            "PARSE_ERROR:RAKBANK transaction email", result.skipped[0]["reason"]
        )

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
