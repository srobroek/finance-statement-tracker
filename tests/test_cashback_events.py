from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from finance_tracker.cashback_events import CashbackEventStore, build_live_dashboard


class CashbackEventStoreTests(unittest.TestCase):
    def test_events_are_idempotent_and_drive_live_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            event = {
                "source_event_id": "message-1:transaction-1",
                "occurred_at": "2026-08-16T12:30:00+04:00",
                "card_code": "RAK_WORLD",
                "amount_aed": "245.50",
                "purchase_type": "GROCERY",
                "channel": "PHYSICAL_POS",
                "merchant": "Carrefour",
            }
            self.assertEqual(store.upsert([event]), {"inserted": 1, "updated": 0, "duplicates": 0})
            self.assertEqual(store.upsert([event]), {"inserted": 0, "updated": 1, "duplicates": 0})

            dashboard = build_live_dashboard(store, date(2026, 8, 16))

            rak = next(card for card in dashboard["cards"] if card["card"] == "RAK_WORLD")
            grocery = next(bucket for bucket in rak["buckets"] if bucket["code"] == "RAK_GROCERY")
            self.assertEqual(rak["total_spend_aed"], "245.5")
            self.assertEqual(grocery["spend_aed"], "245.5")
            self.assertEqual(dashboard["data_status"]["event_count"], 1)
            self.assertEqual(dashboard["data_status"]["provisional_count"], 1)

    def test_different_source_ids_with_same_normalized_identity_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            base = {
                "occurred_at": "2026-08-16T12:30:00+04:00",
                "card_code": "RAK_WORLD",
                "amount_aed": "25.50",
                "purchase_type": "GROCERY",
                "channel": "PHYSICAL_POS",
                "merchant": "Carrefour Market",
            }
            store.upsert([{**base, "source_event_id": "mail-one:1"}])

            result = store.upsert([{**base, "source_event_id": "forwarded-mail:1"}])

            self.assertEqual(result, {"inserted": 0, "updated": 0, "duplicates": 1})
            self.assertEqual(store.stats()["event_count"], 1)

    def test_refund_reduces_live_bucket_and_ignored_event_does_not_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            base = {
                "occurred_at": "2026-08-16T12:30:00+04:00",
                "card_code": "SC_PLATINUM_X",
                "purchase_type": "GENERAL",
                "channel": "ONLINE",
                "merchant": "Example",
            }
            store.upsert([
                {**base, "source_event_id": "purchase", "amount_aed": "100"},
                {**base, "source_event_id": "refund", "amount_aed": "25", "event_type": "REFUND"},
                {**base, "source_event_id": "ignored", "amount_aed": "1000", "status": "IGNORED"},
            ])

            dashboard = build_live_dashboard(store, date(2026, 8, 16))

            sc = next(card for card in dashboard["cards"] if card["card"] == "SC_PLATINUM_X")
            online = next(bucket for bucket in sc["buckets"] if bucket["code"] == "SC_ONLINE")
            self.assertEqual(sc["total_spend_aed"], "75")
            self.assertEqual(online["spend_aed"], "75")

    def test_reversal_requires_reference_and_reduces_spend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            base = {
                "occurred_at": "2026-08-16T12:30:00+04:00",
                "card_code": "RAK_WORLD",
                "purchase_type": "DINING",
                "channel": "PHYSICAL_POS",
                "merchant": "Example Restaurant",
            }
            store.upsert([{**base, "source_event_id": "purchase", "amount_aed": "200"}])
            with self.assertRaisesRegex(ValueError, "reversal_of"):
                store.upsert([
                    {
                        **base,
                        "source_event_id": "invalid-reversal",
                        "amount_aed": "50",
                        "event_type": "REVERSAL",
                    }
                ])
            store.upsert([
                {
                    **base,
                    "source_event_id": "reversal",
                    "amount_aed": "50",
                    "event_type": "REVERSAL",
                    "reversal_of": "purchase",
                }
            ])

            dashboard = build_live_dashboard(store, date(2026, 8, 16))

            rak = next(card for card in dashboard["cards"] if card["card"] == "RAK_WORLD")
            dining = next(bucket for bucket in rak["buckets"] if bucket["code"] == "RAK_DINING")
            self.assertEqual(rak["total_spend_aed"], "150")
            self.assertEqual(dining["spend_aed"], "150")

    def test_ingest_heartbeat_controls_feed_freshness_even_when_scan_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            completed_at = datetime.now(timezone.utc).isoformat()

            result = store.record_ingest_success({
                "source": "outlook",
                "completed_at": completed_at,
                "scanned_count": 0,
                "accepted_count": 0,
                "cursor": "message-cursor",
            })
            dashboard = build_live_dashboard(
                store,
                date.today(),
                stale_after_minutes=90,
            )

            self.assertEqual(result["source"], "outlook")
            self.assertFalse(dashboard["data_status"]["is_stale"])
            self.assertEqual(dashboard["data_status"]["last_scan_count"], 0)
            self.assertEqual(dashboard["data_status"]["last_accepted_count"], 0)

    def test_low_confidence_event_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.upsert([{
                "source_event_id": "uncertain",
                "occurred_at": "2026-08-16T12:30:00+04:00",
                "card_code": "SC_PLATINUM_X",
                "amount_aed": "100",
                "purchase_type": "GROCERY",
                "channel": "ONLINE",
                "merchant": "Unknown Market",
                "confidence": 0.61,
            }])

            dashboard = build_live_dashboard(store, date(2026, 8, 16))

            self.assertEqual(dashboard["review_count"], 1)

    def test_alert_acknowledgements_are_durable_and_reversible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")

            self.assertEqual(
                store.set_alert_acknowledgement("feed:stale", True),
                {"alert_key": "feed:stale", "acknowledged": True},
            )
            self.assertEqual(store.alert_acknowledgements(), ["feed:stale"])
            store.set_alert_acknowledgement("feed:stale", False)

            self.assertEqual(store.alert_acknowledgements(), [])

    def test_statement_reconciliation_replaces_provisional_variances_with_authoritative_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            common = {
                "occurred_at": "2026-08-10T12:30:00+04:00",
                "card_code": "RAK_WORLD",
                "purchase_type": "GROCERY",
                "channel": "PHYSICAL_POS",
            }
            store.upsert([
                {**common, "source_event_id": "matched-notification", "amount_aed": "100", "merchant": "Carrefour Market"},
                {**common, "source_event_id": "missing-from-statement", "amount_aed": "50", "merchant": "Example Cafe"},
            ])
            reconciliation = {
                "statement_reference": "RAK-2026-08",
                "card_code": "RAK_WORLD",
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
                "transactions": [
                    {
                        **common,
                        "statement_transaction_id": "line-1",
                        "amount_aed": "100",
                        "merchant": "CARREFOUR MARKET LLC",
                    },
                    {
                        **common,
                        "statement_transaction_id": "line-2",
                        "occurred_at": "2026-08-11T12:30:00+04:00",
                        "amount_aed": "80",
                        "merchant": "Spinneys",
                    },
                ],
            }

            result = store.reconcile_statement(reconciliation)
            replay = store.reconcile_statement(reconciliation)
            dashboard = build_live_dashboard(store, date(2026, 8, 16))

            self.assertEqual(result["matched"], 1)
            self.assertEqual(result["statement_only"], 1)
            self.assertEqual(result["notification_only"], 1)
            self.assertTrue(replay["idempotent_replay"])
            rak = next(card for card in dashboard["cards"] if card["card"] == "RAK_WORLD")
            self.assertEqual(rak["total_spend_aed"], "180")
            self.assertEqual(dashboard["data_status"]["variance_count"], 1)

    def test_correction_is_idempotent_and_recalculates_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.upsert([{
                "source_event_id": "misclassified",
                "occurred_at": "2026-08-16T12:30:00+04:00",
                "card_code": "RAK_WORLD",
                "amount_aed": "120",
                "purchase_type": "GENERAL",
                "channel": "UNKNOWN",
                "merchant": "Carrefour",
                "confidence": 0.5,
                "review_required": True,
                "decision_trace": [{"rule_id": "merchant-carrefour"}],
            }])
            correction = {
                "correction_id": "manual-review-1",
                "source_event_id": "misclassified",
                "source": "review",
                "reason": "Known supermarket",
                "changes": {
                    "purchase_type": "GROCERY",
                    "channel": "PHYSICAL_POS",
                    "bucket_code": "RAK_GROCERY",
                    "confidence": 1,
                    "review_required": False,
                    "ai_trace": [{"policy": "unresolved-purchase-type", "model": "gpt-5.6-sol"}],
                },
            }

            first = store.correct_event(correction)
            replay = store.correct_event(correction)
            dashboard = build_live_dashboard(store, date(2026, 8, 16))

            self.assertFalse(first["idempotent_replay"])
            self.assertTrue(replay["idempotent_replay"])
            rak = next(card for card in dashboard["cards"] if card["card"] == "RAK_WORLD")
            grocery = next(bucket for bucket in rak["buckets"] if bucket["code"] == "RAK_GROCERY")
            self.assertEqual(grocery["spend_aed"], "120")
            self.assertEqual(dashboard["review_count"], 0)
            self.assertEqual(dashboard["data_status"]["correction_count"], 1)
            self.assertFalse(store.review_queue(20))
            stored = store.rows(date(2026, 8, 1), date(2026, 8, 31))[0]
            self.assertEqual(
                json.loads(stored["decision_trace_json"]),
                [{"rule_id": "merchant-carrefour"}],
            )
            self.assertEqual(
                json.loads(stored["ai_trace_json"]),
                [{"model": "gpt-5.6-sol", "policy": "unresolved-purchase-type"}],
            )

    def test_ai_correction_endpoint_rejects_protected_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.upsert([{
                "source_event_id": "ai-protected",
                "occurred_at": "2026-08-16T12:30:00+04:00",
                "card_code": "RAK_WORLD",
                "amount_aed": "120",
                "purchase_type": "GENERAL",
                "channel": "UNKNOWN",
                "merchant": "Unknown",
            }])

            with self.assertRaisesRegex(ValueError, "AI corrections cannot modify protected"):
                store.correct_event({
                    "correction_id": "ai-unsafe-1",
                    "source_event_id": "ai-protected",
                    "source": "ai-policy:classify-unresolved",
                    "changes": {"amount_aed": "1"},
                })

            stored = store.rows(date(2026, 8, 1), date(2026, 8, 31))[0]
            self.assertEqual(stored["amount_aed_minor"], 12000)
            self.assertEqual(store.stats()["correction_count"], 0)

    def test_third_week_and_near_full_bucket_alerts_are_calculated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.upsert([{
                "source_event_id": "near-full",
                "occurred_at": "2026-08-21T12:30:00+04:00",
                "card_code": "RAK_WORLD",
                "amount_aed": "2750",
                "purchase_type": "GROCERY",
                "channel": "PHYSICAL_POS",
                "merchant": "Carrefour",
            }])

            dashboard = build_live_dashboard(store, date(2026, 8, 21))

            keys = {alert["key"] for alert in dashboard["alerts"]}
            self.assertIn("minimum:RAK_WORLD:2026-08-01:2026-08-31", keys)
            self.assertIn("bucket:RAK_WORLD:RAK_GROCERY:near_full", keys)
            rak = next(card for card in dashboard["cards"] if card["card"] == "RAK_WORLD")
            self.assertEqual(rak["provisional_event_count"], 1)
            self.assertEqual(rak["confirmed_event_count"], 0)

    def test_unmet_card_targets_warn_during_the_final_week(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")

            dashboard = build_live_dashboard(store, date(2026, 8, 25))

            keys = {alert["key"] for alert in dashboard["alerts"]}
            self.assertIn("close:RAK_WORLD:2026-08-01:2026-08-31", keys)
            self.assertIn("close:SC_PLATINUM_X:2026-08-01:2026-08-31", keys)

    def test_finalization_opens_the_next_configured_card_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = CashbackEventStore(root / "events.sqlite3")
            configuration = json.loads(Path("config/cashback-programs.json").read_text(encoding="utf-8"))
            for program in configuration["programs"]:
                if program["card"] == "RAK_WORLD":
                    program["statement_cycle"]["close_day"] = 15
            config_path = root / "cashback-programs.json"
            config_path.write_text(json.dumps(configuration), encoding="utf-8")
            store.reconcile_statement({
                "statement_reference": "RAK-2026-08-15",
                "card_code": "RAK_WORLD",
                "period_start": "2026-07-16",
                "period_end": "2026-08-15",
                "transactions": [],
            })
            store.finalize_period(
                {
                    "statement_reference": "RAK-2026-08-15",
                    "statement_evidence_reference": "sha256:test",
                    "statement_document_url": "https://evidence.example/rak.pdf",
                    "actual_import_verified": True,
                },
                program_config_path=config_path,
            )
            open_period = next(row for row in store.period_rows() if row["status"] == "OPEN")
            self.assertEqual(open_period["period_start"], "2026-08-16")
            self.assertEqual(open_period["period_end"], "2026-09-15")

    def test_period_finalization_requires_statement_evidence_and_verified_actual_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            reconciliation = {
                "statement_reference": "EI-2026-08",
                "card_code": "EI_AMAZON",
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
                "transactions": [],
            }
            store.reconcile_statement(reconciliation)
            with self.assertRaisesRegex(ValueError, "statement_evidence_reference"):
                store.finalize_period({"statement_reference": "EI-2026-08"})

            payload = {
                "statement_reference": "EI-2026-08",
                "statement_evidence_reference": "sha256:abc",
                "statement_document_url": "Finance Evidence/2026/08/ei/statement.pdf",
                "actual_import_verified": True,
            }
            finalized = store.finalize_period(payload)
            replay = store.finalize_period(payload)

            self.assertEqual(finalized["status"], "FINALIZED")
            self.assertFalse(finalized["idempotent_replay"])
            self.assertTrue(replay["idempotent_replay"])
            periods = store.period_rows()
            self.assertEqual(periods[0]["status"], "OPEN")
            self.assertEqual(periods[1]["status"], "FINALIZED")

    def test_period_with_variances_requires_explicit_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.upsert([{
                "source_event_id": "notification-only",
                "occurred_at": "2026-08-10T12:30:00+04:00",
                "card_code": "RAK_WORLD",
                "amount_aed": "10",
                "merchant": "Example",
            }])
            store.reconcile_statement({
                "statement_reference": "RAK-2026-08",
                "card_code": "RAK_WORLD",
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
                "transactions": [],
            })
            payload = {
                "statement_reference": "RAK-2026-08",
                "statement_evidence_reference": "sha256:def",
                "statement_document_url": "Finance Evidence/2026/08/rak/statement.pdf",
                "actual_import_verified": True,
            }

            with self.assertRaisesRegex(ValueError, "variances"):
                store.finalize_period(payload)
            payload["acknowledge_variances"] = True
            self.assertEqual(
                store.finalize_period(payload)["reconciliation_status"],
                "RECONCILED_WITH_ACKNOWLEDGED_VARIANCES",
            )


if __name__ == "__main__":
    unittest.main()
