from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from finance_tracker.cashback_events import (
    CashbackEventStore,
    _json_digest,
    _legacy_recovery_digest,
    build_live_dashboard,
)


def statement_digest(reference: str) -> str:
    return hashlib.sha256(reference.encode()).hexdigest()


def actual_receipt(
    reference: str,
    period_start: str,
    period_end: str,
    *,
    account_id: str = "EI_AMAZON",
    card_code: str | None = None,
) -> dict[str, object]:
    payload_digest = statement_digest(f"actual-payload:{reference}")
    return {
        "outbox_id": f"outbox:{reference}",
        "verification_version": 1,
        "actual_file_id": f"actual-file:{reference}",
        "account_id": account_id,
        "card_code": card_code or account_id,
        "period_start": period_start,
        "period_end": period_end,
        "expected_payload_sha256": payload_digest,
        "observed_payload_sha256": payload_digest,
        "expected_count": 0,
        "observed_count": 0,
        "expected_amount_sum_minor": 0,
        "observed_amount_sum_minor": 0,
        "invariants_passed": True,
        "state": "COMMITTED",
        "writer_release_verified": True,
        "verified_at": "2026-08-20T00:00:00+00:00",
    }


def actual_receipt_digest(receipt: dict[str, object]) -> str:
    return _json_digest(receipt)


class CashbackEventStoreTests(unittest.TestCase):
    def test_currency_neutral_amount_alias_is_supported_and_conflicts_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            event = {
                "source_event_id": "portable-api:1",
                "occurred_at": "2026-08-16T12:30:00+04:00",
                "card_code": "ANY_CARD",
                "amount": "25.50",
                "currency": "USD",
                "merchant": "Example",
            }
            self.assertEqual(store.upsert([event])["inserted"], 1)
            with self.assertRaisesRegex(ValueError, "disagree"):
                store.validate([{**event, "source_event_id": "portable-api:2", "amount_aed": "30"}])

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
            self.assertEqual(dashboard["data_status"]["live_event_count"], 1)

    def test_legacy_notification_status_is_migrated_to_active(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.upsert([{
                "source_event_id": "legacy-message:1",
                "occurred_at": "2026-08-16T12:30:00+04:00",
                "card_code": "RAK_WORLD",
                "amount_aed": "25.50",
                "merchant": "Example",
                "status": "PROVISIONAL",
            }])

            rows = store.rows(date(2026, 8, 16), date(2026, 8, 16))

            self.assertEqual(rows[0]["status"], "ACTIVE")

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

            receipt = store.create_ingest_receipt({
                "source": "outlook",
                "completed_at": completed_at,
                "scanned_count": 0,
                "accepted_count": 0,
                "cursor": "message-cursor",
            })
            result = store.record_ingest_success({
                "source": "outlook",
                "completed_at": completed_at,
                "scanned_count": 0,
                "accepted_count": 0,
                "cursor": "message-cursor",
                "service_receipt": receipt,
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

            self.assertNotIn("review_count", dashboard)

    def test_general_purchase_with_explicit_classification_does_not_require_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.upsert([{
                "source_event_id": "configured-default",
                "occurred_at": "2026-08-16T12:30:00+04:00",
                "card_code": "RAK_WORLD",
                "amount_aed": "16",
                "purchase_type": "GENERAL",
                "channel": "APPLE_PAY_POS",
                "merchant": "Best of Vends",
                "confidence": 0.95,
                "review_required": False,
            }])

            dashboard = build_live_dashboard(store, date(2026, 8, 16))

            self.assertNotIn("review_count", dashboard)
            self.assertEqual(store.review_queue(20), [])

            store.correct_event({
                "correction_id": "approve-live-classification",
                "source_event_id": "configured-default",
                "source": "dashboard-review",
                "changes": {"review_required": False},
            })
            stored = store.rows(date(2026, 8, 1), date(2026, 8, 31))[0]
            self.assertEqual(stored["status"], "ACTIVE")
            self.assertEqual(stored["reconciliation_status"], "UNMATCHED")

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

    def test_statement_reconciliation_replaces_live_variances_with_authoritative_rows(self) -> None:
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
                "statement_sha256": statement_digest("RAK-2026-08"),
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
            self.assertNotIn("review_count", dashboard)
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

            dashboard = build_live_dashboard(store, date(2026, 8, 26))

            keys = {alert["key"] for alert in dashboard["alerts"]}
            self.assertIn("minimum:RAK_WORLD:2026-08-06:2026-09-05", keys)
            self.assertIn("bucket:RAK_WORLD:RAK_GROCERY:near_full", keys)
            rak = next(card for card in dashboard["cards"] if card["card"] == "RAK_WORLD")
            self.assertEqual(rak["transaction_count"], 1)

    def test_unmet_card_targets_warn_during_the_final_week(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")

            dashboard = build_live_dashboard(store, date(2026, 8, 30))

            keys = {alert["key"] for alert in dashboard["alerts"]}
            self.assertIn("close:RAK_WORLD:2026-08-06:2026-09-05", keys)
            self.assertIn("close:SC_PLATINUM_X:2026-08-06:2026-09-05", keys)

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
                "statement_sha256": statement_digest("RAK-2026-08-15"),
                "card_code": "RAK_WORLD",
                "period_start": "2026-07-16",
                "period_end": "2026-08-15",
                "transactions": [],
            })
            store.finalize_period(
                {
                    "statement_reference": "RAK-2026-08-15",
                    "statement_sha256": statement_digest("RAK-2026-08-15"),
                    "statement_evidence_reference": "sha256:test",
                    "actual_import_receipt": actual_receipt(
                        "RAK-2026-08-15", "2026-07-16", "2026-08-15",
                        account_id="RAK_WORLD",
                    ),
                    "actual_import_receipt_sha256": actual_receipt_digest(
                        actual_receipt(
                            "RAK-2026-08-15", "2026-07-16", "2026-08-15",
                            account_id="RAK_WORLD",
                        )
                    ),
                    "statement_document_url": "https://evidence.example/rak.pdf",
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
                "statement_sha256": statement_digest("EI-2026-08"),
                "card_code": "EI_AMAZON",
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
                "transactions": [],
            }
            premature_receipt = actual_receipt("EI-2026-08", "2026-08-01", "2026-08-31")
            with self.assertRaisesRegex(ValueError, "successful statement reconciliation"):
                store.finalize_period({
                    "statement_reference": "EI-2026-08",
                    "statement_sha256": statement_digest("EI-2026-08"),
                    "statement_evidence_reference": "sha256:premature",
                    "statement_document_url": "Finance Evidence/ei-premature.pdf",
                    "actual_import_receipt": premature_receipt,
                    "actual_import_receipt_sha256": actual_receipt_digest(premature_receipt),
                })
            store.reconcile_statement(reconciliation)
            with self.assertRaisesRegex(ValueError, "statement_evidence_reference"):
                store.finalize_period({"statement_reference": "EI-2026-08"})

            payload = {
                "statement_reference": "EI-2026-08",
                "statement_sha256": statement_digest("EI-2026-08"),
                "statement_evidence_reference": "sha256:abc",
                "actual_import_receipt": actual_receipt(
                    "EI-2026-08", "2026-08-01", "2026-08-31"
                ),
                "actual_import_receipt_sha256": actual_receipt_digest(
                    actual_receipt("EI-2026-08", "2026-08-01", "2026-08-31")
                ),
                "statement_document_url": "Finance Evidence/2026/08/ei/statement.pdf",
            }
            finalized = store.finalize_period(payload)
            replay = store.finalize_period(payload)

            self.assertEqual(finalized["status"], "FINALIZED")
            self.assertEqual(finalized["close_id"], "cashback-close:EI_AMAZON:2026-08-01:2026-08-31")
            self.assertFalse(finalized["idempotent_replay"])
            self.assertTrue(replay["idempotent_replay"])
            self.assertEqual(replay["close_id"], finalized["close_id"])
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
                "statement_reference": "RAK-2026-09-05",
                "statement_sha256": statement_digest("RAK-2026-09-05"),
                "card_code": "RAK_WORLD",
                "period_start": "2026-08-06",
                "period_end": "2026-09-05",
                "transactions": [],
            })
            payload = {
                "statement_reference": "RAK-2026-09-05",
                "statement_sha256": statement_digest("RAK-2026-09-05"),
                "statement_evidence_reference": "sha256:def",
                "actual_import_receipt": actual_receipt(
                    "RAK-2026-09-05", "2026-08-06", "2026-09-05",
                    account_id="RAK_WORLD",
                ),
                "actual_import_receipt_sha256": actual_receipt_digest(
                    actual_receipt(
                        "RAK-2026-09-05", "2026-08-06", "2026-09-05",
                        account_id="RAK_WORLD",
                    )
                ),
                "statement_document_url": "Finance Evidence/2026/08/rak/statement.pdf",
            }

            with self.assertRaisesRegex(ValueError, "variances"):
                store.finalize_period(payload)
            payload["acknowledge_variances"] = True
            self.assertEqual(
                store.finalize_period(payload)["reconciliation_status"],
                "RECONCILED_WITH_ACKNOWLEDGED_VARIANCES",
            )

    def test_reconciliation_replay_rejects_changed_content_or_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            payload = {
                "statement_reference": "EI-2026-09",
                "statement_sha256": statement_digest("EI-2026-09"),
                "card_code": "EI_AMAZON",
                "period_start": "2026-09-01",
                "period_end": "2026-09-30",
                "transactions": [{
                    "statement_transaction_id": "line-1",
                    "occurred_at": "2026-09-10T12:30:00+04:00",
                    "amount_aed": "20",
                    "merchant": "Example",
                }],
            }
            first = store.reconcile_statement(payload)
            self.assertFalse(first["idempotent_replay"])

            changed_content = {
                **payload,
                "transactions": [{**payload["transactions"][0], "merchant": "Changed"}],
            }
            with self.assertRaisesRegex(ValueError, "different statement content or digest"):
                store.reconcile_statement(changed_content)

            changed_digest = {**payload, "statement_sha256": statement_digest("other")}
            with self.assertRaisesRegex(ValueError, "different statement content or digest"):
                store.reconcile_statement(changed_digest)

            self.assertEqual(store.stats()["event_count"], 1)

    def test_actual_verified_boolean_without_receipt_digest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            reference = "EI-2026-10"
            store.reconcile_statement({
                "statement_reference": reference,
                "statement_sha256": statement_digest(reference),
                "card_code": "EI_AMAZON",
                "period_start": "2026-10-01",
                "period_end": "2026-10-31",
                "transactions": [],
            })
            with self.assertRaisesRegex(ValueError, "actual_import_receipt_sha256"):
                store.finalize_period({
                    "statement_reference": reference,
                    "statement_sha256": statement_digest(reference),
                    "statement_evidence_reference": "sha256:evidence",
                    "statement_document_url": "Finance Evidence/ei.pdf",
                    "actual_import_verified": True,
                })
            self.assertNotIn("FINALIZED", {row["status"] for row in store.period_rows()})

    def test_actual_close_requires_trusted_readback_receipt_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            reference = "EI-2026-10-receipt-shape"
            statement_sha256 = statement_digest(reference)
            store.reconcile_statement({
                "statement_reference": reference,
                "statement_sha256": statement_sha256,
                "card_code": "EI_AMAZON",
                "period_start": "2026-10-01",
                "period_end": "2026-10-31",
                "transactions": [],
            })
            close_fields = {
                "statement_reference": reference,
                "statement_sha256": statement_sha256,
                "statement_evidence_reference": "sha256:evidence",
                "statement_document_url": "Finance Evidence/ei.pdf",
            }
            with self.assertRaisesRegex(ValueError, "missing required fields"):
                store.finalize_period({
                    **close_fields,
                    "actual_import_receipt": {},
                    "actual_import_receipt_sha256": statement_digest("empty"),
                })
            with self.assertRaisesRegex(ValueError, "missing required fields"):
                store.finalize_period({
                    **close_fields,
                    "actual_import_receipt": {
                        "outbox_id": "caller-invented",
                        "observed_payload_sha256": statement_digest("payload"),
                        "invariants_passed": True,
                    },
                    "actual_import_receipt_sha256": statement_digest("caller-invented"),
                })
            with self.assertRaisesRegex(ValueError, "readback object"):
                store.finalize_period({
                    **close_fields,
                    "actual_import_receipt_sha256": statement_digest("digest-only"),
                })
            receipt = actual_receipt(reference, "2026-10-01", "2026-10-31")
            mismatched_readback = {
                **receipt,
                "observed_payload_sha256": statement_digest("different-readback"),
            }
            with self.assertRaisesRegex(ValueError, "payload digests differ"):
                store.finalize_period({
                    **close_fields,
                    "actual_import_receipt": mismatched_readback,
                    "actual_import_receipt_sha256": actual_receipt_digest(mismatched_readback),
                })
            with self.assertRaisesRegex(ValueError, "invariants must pass"):
                failed_receipt = {**receipt, "invariants_passed": False}
                store.finalize_period({
                    **close_fields,
                    "actual_import_receipt": failed_receipt,
                    "actual_import_receipt_sha256": actual_receipt_digest(failed_receipt),
                })
            self.assertNotIn("FINALIZED", {row["status"] for row in store.period_rows()})

    def test_actual_close_rejects_stale_post_actual_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            reference = "EI-2026-10-stale"
            statement_sha256 = statement_digest(reference)
            store.reconcile_statement({
                "statement_reference": reference,
                "statement_sha256": statement_sha256,
                "card_code": "EI_AMAZON",
                "period_start": "2026-10-01",
                "period_end": "2026-10-31",
                "transactions": [],
            })
            receipt = actual_receipt(reference, "2026-10-01", "2026-10-31")
            receipt["state"] = "ACTUAL_OBSERVED"
            with self.assertRaisesRegex(ValueError, "state must be COMMITTED"):
                store.finalize_period({
                    "statement_reference": reference,
                    "statement_sha256": statement_sha256,
                    "statement_evidence_reference": "sha256:stale",
                    "statement_document_url": "Finance Evidence/stale.pdf",
                    "actual_import_receipt": receipt,
                    "actual_import_receipt_sha256": actual_receipt_digest(receipt),
                })
            self.assertNotIn("FINALIZED", {row["status"] for row in store.period_rows()})

    def test_actual_receipt_account_identity_must_match_reconciled_card(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            reference = "RAK-2026-09-05-account-identity"
            statement_sha256 = statement_digest(reference)
            store.reconcile_statement({
                "statement_reference": reference,
                "statement_sha256": statement_sha256,
                "card_code": "RAK_WORLD",
                "period_start": "2026-08-06",
                "period_end": "2026-09-05",
                "transactions": [],
            })
            close_fields = {
                "statement_reference": reference,
                "statement_sha256": statement_sha256,
                "statement_evidence_reference": "sha256:account-identity",
                "statement_document_url": "Finance Evidence/rak-account.pdf",
            }
            wrong_account_receipt = actual_receipt(reference, "2026-08-06", "2026-09-05")
            with self.assertRaisesRegex(ValueError, "account identity"):
                store.finalize_period({
                    **close_fields,
                    "actual_import_receipt": wrong_account_receipt,
                    "actual_import_receipt_sha256": actual_receipt_digest(wrong_account_receipt),
                })
            exact_receipt = actual_receipt(
                reference,
                "2026-08-06",
                "2026-09-05",
                account_id="actual-account:RAK_WORLD",
                card_code="RAK_WORLD",
            )
            missing_card_receipt = {key: value for key, value in exact_receipt.items() if key != "card_code"}
            with self.assertRaisesRegex(ValueError, "missing required fields.*card_code"):
                store.finalize_period({
                    **close_fields,
                    "actual_import_receipt": missing_card_receipt,
                    "actual_import_receipt_sha256": actual_receipt_digest(missing_card_receipt),
                })
            exact_payload = {
                **close_fields,
                "actual_import_receipt": exact_receipt,
                "actual_import_receipt_sha256": actual_receipt_digest(exact_receipt),
            }
            finalized = store.finalize_period(exact_payload)
            self.assertEqual(finalized["status"], "FINALIZED")
            self.assertTrue(store.finalize_period(exact_payload)["idempotent_replay"])

    def test_legacy_reconciliation_digest_recovery_backfills_once_and_rejects_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            legacy_row = {
                "statement_reference": "legacy-EI-2026-10",
                "card_code": "EI_AMAZON",
                "period_start": "2026-10-01",
                "period_end": "2026-10-31",
                "matched_count": 0,
                "statement_only_count": 0,
                "notification_only_count": 0,
            }
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    CREATE TABLE reconciliation_runs (
                        statement_reference TEXT PRIMARY KEY,
                        card_code TEXT NOT NULL,
                        period_start TEXT NOT NULL,
                        period_end TEXT NOT NULL,
                        matched_count INTEGER NOT NULL,
                        statement_only_count INTEGER NOT NULL,
                        notification_only_count INTEGER NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO reconciliation_runs (
                        statement_reference, card_code, period_start, period_end,
                        matched_count, statement_only_count, notification_only_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(legacy_row.values()),
                )
            store = CashbackEventStore(database)
            payload = {
                **legacy_row,
                "statement_sha256": statement_digest(legacy_row["statement_reference"]),
                "transactions": [],
            }
            with self.assertRaisesRegex(ValueError, "recovery proof is invalid"):
                store.reconcile_statement({**payload, "legacy_recovery_digest": "0" * 64})
            recovered = store.reconcile_statement({
                **payload,
                "legacy_recovery_digest": _legacy_recovery_digest(legacy_row),
            })
            self.assertTrue(recovered["idempotent_replay"])
            self.assertTrue(recovered["legacy_digest_backfilled"])
            replay = store.reconcile_statement(payload)
            self.assertTrue(replay["idempotent_replay"])
            with self.assertRaisesRegex(ValueError, "different statement content or digest"):
                store.reconcile_statement({
                    **payload,
                    "statement_sha256": statement_digest("changed-legacy"),
                })

    def test_finalization_replay_rejects_changed_evidence_and_receipt_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            reference = "EI-2026-11"
            statement_sha256 = statement_digest(reference)
            store = CashbackEventStore(database)
            store.reconcile_statement({
                "statement_reference": reference,
                "statement_sha256": statement_sha256,
                "card_code": "EI_AMAZON",
                "period_start": "2026-11-01",
                "period_end": "2026-11-30",
                "transactions": [],
            })
            receipt = actual_receipt(reference, "2026-11-01", "2026-11-30")
            payload = {
                "statement_reference": reference,
                "statement_sha256": statement_sha256,
                "statement_evidence_reference": "sha256:evidence-1",
                "statement_document_url": "Finance Evidence/ei-11.pdf",
                "actual_import_receipt": receipt,
                "actual_import_receipt_sha256": actual_receipt_digest(receipt),
            }
            first = store.finalize_period(payload)
            self.assertFalse(first["idempotent_replay"])

            restarted = CashbackEventStore(database)
            self.assertTrue(restarted.finalize_period(payload)["idempotent_replay"])
            with self.assertRaisesRegex(ValueError, "different content, digest, or evidence"):
                restarted.finalize_period({**payload, "statement_evidence_reference": "sha256:evidence-2"})
            with self.assertRaisesRegex(ValueError, "Actual import receipt digest"):
                restarted.finalize_period({**payload, "actual_import_receipt_sha256": statement_digest("other")})

    def test_finalization_can_digest_an_independent_actual_receipt_object(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            reference = "EI-2026-11-object"
            statement_sha256 = statement_digest(reference)
            store.reconcile_statement({
                "statement_reference": reference,
                "statement_sha256": statement_sha256,
                "card_code": "EI_AMAZON",
                "period_start": "2026-11-01",
                "period_end": "2026-11-30",
                "transactions": [],
            })
            receipt = {
                "outbox_id": "outbox:ei-2026-11",
                "verification_version": 1,
                "actual_file_id": "actual-file:ei-2026-11",
                "account_id": "EI_AMAZON",
                "card_code": "EI_AMAZON",
                "period_start": "2026-11-01",
                "period_end": "2026-11-30",
                "expected_payload_sha256": statement_digest("actual-payload"),
                "observed_payload_sha256": statement_digest("actual-payload"),
                "invariants_passed": True,
                "state": "COMMITTED",
                "writer_release_verified": True,
                "verified_at": "2026-08-20T00:00:00+00:00",
            }
            receipt_sha256 = actual_receipt_digest(receipt)
            result = store.finalize_period({
                "statement_reference": reference,
                "statement_sha256": statement_sha256,
                "statement_evidence_reference": "sha256:evidence-object",
                "statement_document_url": "Finance Evidence/ei-object.pdf",
                "actual_import_receipt": receipt,
                "actual_import_receipt_sha256": receipt_sha256,
            })
            self.assertEqual(result["actual_import_receipt_sha256"], receipt_sha256)

    def test_finalization_fault_rolls_back_and_retry_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            reference = "EI-2026-12"
            statement_sha256 = statement_digest(reference)
            store = CashbackEventStore(database)
            store.reconcile_statement({
                "statement_reference": reference,
                "statement_sha256": statement_sha256,
                "card_code": "EI_AMAZON",
                "period_start": "2026-12-01",
                "period_end": "2026-12-31",
                "transactions": [],
            })
            receipt = actual_receipt(reference, "2026-12-01", "2026-12-31")
            receipt_sha256 = actual_receipt_digest(receipt)
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    CREATE TRIGGER fail_card_period_insert
                    BEFORE INSERT ON card_periods
                    WHEN NEW.status = 'FINALIZED'
                    BEGIN
                        SELECT RAISE(ABORT, 'synthetic finalization fault');
                    END
                    """
                )
            payload = {
                "statement_reference": reference,
                "statement_sha256": statement_sha256,
                "statement_evidence_reference": "sha256:evidence-12",
                "statement_document_url": "Finance Evidence/ei-12.pdf",
                "actual_import_receipt": receipt,
                "actual_import_receipt_sha256": receipt_sha256,
            }
            with self.assertRaisesRegex(sqlite3.IntegrityError, "synthetic finalization fault"):
                store.finalize_period(payload)
            restarted = CashbackEventStore(database)
            self.assertNotIn("FINALIZED", {row["status"] for row in restarted.period_rows()})
            with sqlite3.connect(database) as connection:
                connection.execute("DROP TRIGGER fail_card_period_insert")
            self.assertEqual(restarted.finalize_period(payload)["status"], "FINALIZED")

    def test_digest_migration_adds_close_proof_columns_to_legacy_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE card_periods (
                        card_code TEXT NOT NULL,
                        period_start TEXT NOT NULL,
                        period_end TEXT NOT NULL,
                        statement_reference TEXT,
                        statement_evidence_reference TEXT,
                        statement_document_url TEXT,
                        actual_import_verified INTEGER NOT NULL DEFAULT 0,
                        reconciliation_status TEXT NOT NULL DEFAULT 'PENDING',
                        status TEXT NOT NULL DEFAULT 'OPEN',
                        finalized_at TEXT,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(card_code, period_start, period_end)
                    );
                    CREATE TABLE reconciliation_runs (
                        statement_reference TEXT PRIMARY KEY,
                        card_code TEXT NOT NULL,
                        period_start TEXT NOT NULL,
                        period_end TEXT NOT NULL,
                        matched_count INTEGER NOT NULL,
                        statement_only_count INTEGER NOT NULL,
                        notification_only_count INTEGER NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    """
                )
            CashbackEventStore(database)
            with sqlite3.connect(database) as connection:
                period_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(card_periods)")
                }
                run_columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(reconciliation_runs)")
                }
            self.assertTrue(
                {
                    "statement_sha256",
                    "statement_content_sha256",
                    "actual_import_receipt_sha256",
                    "actual_verification_sha256",
                }
                <= period_columns
            )
            self.assertTrue({"statement_sha256", "statement_content_sha256"} <= run_columns)


if __name__ == "__main__":
    unittest.main()
