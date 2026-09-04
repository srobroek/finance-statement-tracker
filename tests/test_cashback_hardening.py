from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from finance_tracker.cashback import PaymentIntent
from finance_tracker.cashback_events import CashbackEventStore
from finance_tracker.notifications import parse_outlook_notifications


class CashbackHardeningTests(unittest.TestCase):
    def test_payment_intents_reject_non_positive_or_non_finite_amounts(self) -> None:
        for value in ("0", "-1", "NaN", "Infinity"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PaymentIntent("GENERAL", value)

        normalized = PaymentIntent(" grocery ", "100", "aed", "physical_pos")
        self.assertEqual(normalized.category, "GROCERY")
        self.assertEqual(normalized.currency, "AED")
        self.assertEqual(normalized.channel, "PHYSICAL_POS")

    def test_event_amounts_reject_rounding_and_non_finite_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            event = {
                "source_event_id": "hardening:amount",
                "occurred_at": "2026-08-16T12:30:00+04:00",
                "card_code": "RAK_WORLD",
                "merchant": "Example",
            }
            for value, message in (
                ("10.001", "no more than two decimal places"),
                ("NaN", "finite"),
                ("Infinity", "finite"),
            ):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                    store.validate([{**event, "amount_aed": value}])

    def test_ingest_receipt_preserves_id_digest_pairing_and_cardinality(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            source = {
                "source": "outlook:rakbank",
                "completed_at": "2026-08-16T12:30:00+04:00",
                "scanned_count": 2,
                "accepted_count": 2,
                "cursor": "2026-08-16T12:30:00+04:00",
            }
            receipt = store.create_ingest_receipt(
                source,
                event_ids=["event-b", "event-a"],
                event_digests=["digest-b", "digest-a"],
            )
            self.assertEqual(receipt["event_ids"], ["event-a", "event-b"])
            self.assertEqual(receipt["event_digests"], ["digest-a", "digest-b"])
            with self.assertRaisesRegex(ValueError, "accepted_count"):
                store.create_ingest_receipt(
                    {**source, "accepted_count": 1},
                    event_ids=["event-a", "event-b"],
                    event_digests=["digest-a", "digest-b"],
                )
            with self.assertRaisesRegex(ValueError, "unique"):
                store.create_ingest_receipt(
                    source,
                    event_ids=["event-a", "event-a"],
                    event_digests=["digest-a", "digest-b"],
                )

    def test_notification_duplicate_ids_and_naive_received_times_fail_closed(self) -> None:
        message = json.loads(
            Path("tests/fixtures/rakbank-card-transaction.json").read_text(encoding="utf-8")
        )
        with self.assertRaisesRegex(ValueError, "Duplicate Outlook message id"):
            parse_outlook_notifications([message, dict(message)], {"7210": "RAK_WORLD"})

        naive = dict(message)
        naive["id"] = "rakbank-naive-time"
        naive["receivedDateTime"] = "2026-08-16T10:30:00"
        result = parse_outlook_notifications([naive], {"7210": "RAK_WORLD"})
        self.assertEqual(result.accepted_count, 0)
        self.assertIn("timezone", result.skipped[0]["reason"])

    def test_reconciled_matching_facts_cannot_be_changed_without_new_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.upsert([{
                "source_event_id": "reconciled:notification",
                "occurred_at": "2026-08-10T12:30:00+04:00",
                "card_code": "RAK_WORLD",
                "amount_aed": "100",
                "purchase_type": "GROCERY",
                "channel": "PHYSICAL_POS",
                "merchant": "Carrefour Market",
            }])
            statement = {
                "statement_reference": "RAK-2026-08-hardening",
                "statement_sha256": "a" * 64,
                "card_code": "RAK_WORLD",
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
                "transactions": [{
                    "statement_transaction_id": "line-1",
                    "occurred_at": "2026-08-10T12:30:00+04:00",
                    "amount_aed": "100",
                    "purchase_type": "GROCERY",
                    "channel": "PHYSICAL_POS",
                    "merchant": "CARREFOUR MARKET LLC",
                }],
            }
            store.reconcile_statement(statement)
            with self.assertRaisesRegex(ValueError, "authoritative reconciliation facts"):
                store.correct_event({
                    "correction_id": "reconciled:correction",
                    "source_event_id": "reconciled:notification",
                    "changes": {"amount_aed": "101"},
                })
            # Classification enrichment remains available after reconciliation.
            result = store.correct_event({
                "correction_id": "reconciled:classification",
                "source_event_id": "reconciled:notification",
                "changes": {"purchase_type": "DINING"},
            })
            self.assertFalse(result["idempotent_replay"])

    def test_source_scoped_stats_choose_the_latest_successful_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            for source, timestamp in (
                ("outlook:rakbank", "2026-08-16T12:30:00+04:00"),
                ("outlook:other", "2026-08-18T12:30:00+04:00"),
            ):
                receipt = store.create_ingest_receipt(
                    {
                        "source": source,
                        "completed_at": timestamp,
                        "scanned_count": 0,
                        "accepted_count": 0,
                        "cursor": timestamp,
                    }
                )
                store.record_ingest_success({
                    "source": source,
                    "completed_at": timestamp,
                    "scanned_count": 0,
                    "accepted_count": 0,
                    "cursor": timestamp,
                    "service_receipt": receipt,
                })
            stats = store.stats("outlook:rakbank")
            self.assertEqual(stats["last_ingest_source"], "outlook:rakbank")
            self.assertEqual(stats["last_successful_ingest_at"], "2026-08-16T12:30:00+04:00")
            self.assertIsNone(store.stats("outlook:missing")["last_successful_ingest_at"])


if __name__ == "__main__":
    unittest.main()
