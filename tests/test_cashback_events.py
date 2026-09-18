from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from finance_tracker.actual_snapshot import cashback_dashboard
from finance_tracker.cashback import PaymentIntent, configured_programs
from finance_tracker.cashback_events import (
    CashbackEventStore,
    _canonical_statement_events,
    _json_digest,
    _legacy_recovery_digest,
    _statement_content_digest,
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
    def test_currency_neutral_amount_alias_is_supported_and_conflicts_are_rejected(
        self,
    ) -> None:
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
                store.validate(
                    [{**event, "source_event_id": "portable-api:2", "amount_aed": "30"}]
                )

    def test_statement_only_card_rejects_live_events_but_keeps_evidence_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            event = {
                "source_event_id": "ei-live:1",
                "occurred_at": "2026-08-16T12:30:00+04:00",
                "card_code": "EI_AMAZON",
                "amount_aed": "100",
                "purchase_type": "AMAZON",
                "channel": "ONLINE",
                "merchant": "Amazon",
            }
            with self.assertRaisesRegex(ValueError, "STATEMENT_ONLY"):
                store.validate([event])
            with self.assertRaisesRegex(ValueError, "STATEMENT_ONLY"):
                store.upsert([event])

            reconciliation = store.reconcile_statement(
                {
                    "statement_reference": "EI-2026-08",
                    "statement_sha256": statement_digest("EI-2026-08"),
                    "card_code": "EI_AMAZON",
                    "period_start": "2026-08-01",
                    "period_end": "2026-08-31",
                    "transactions": [
                        {
                            "statement_transaction_id": "ei-statement:1",
                            "occurred_at": "2026-08-16T12:30:00+04:00",
                            "amount_aed": "100",
                            "purchase_type": "AMAZON",
                            "channel": "ONLINE",
                            "merchant": "Amazon",
                        }
                    ],
                }
            )
            self.assertEqual(reconciliation["statement_only"], 1)
            self.assertEqual(
                store.rows(date(2026, 8, 1), date(2026, 8, 31))[0]["source"],
                "statement",
            )
            store.ensure_period(
                {
                    "period_id": "ei-aug-period",
                    "card_code": "EI_AMAZON",
                    "period_start": "2026-08-01T00:00:00Z",
                    "period_end": "2026-09-01T00:00:00Z",
                }
            )
            receipt = store.record_statement_receipt(
                {
                    "receipt_id": "ei-receipt",
                    "source_identity": "mail:ei:aug",
                    "card_code": "EI_AMAZON",
                    "original_received_at": "2026-09-01T00:00:00Z",
                    "period_id": "ei-aug-period",
                }
            )
            self.assertEqual(receipt["status"], "CLOSED")

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
            self.assertEqual(
                store.upsert([event]),
                {"inserted": 1, "updated": 0, "unchanged": 0, "duplicates": 0},
            )
            self.assertEqual(
                store.upsert([event]),
                {"inserted": 0, "updated": 0, "unchanged": 1, "duplicates": 0},
            )

            dashboard = build_live_dashboard(store, date(2026, 8, 16))

            rak = next(
                card for card in dashboard["cards"] if card["card"] == "RAK_WORLD"
            )
            grocery = next(
                bucket for bucket in rak["buckets"] if bucket["code"] == "RAK_GROCERY"
            )
            self.assertEqual(rak["total_spend_aed"], "245.5")
            self.assertEqual(grocery["spend_aed"], "245.5")
            self.assertEqual(dashboard["data_status"]["event_count"], 1)
            self.assertEqual(dashboard["data_status"]["live_event_count"], 1)

    def test_live_membership_gate_does_not_hide_historical_programmes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")

            live = build_live_dashboard(store, date(2026, 8, 16), memberships=())
            self.assertEqual(
                {card["card"] for card in live["cards"]},
                {"RAK_WORLD"},
            )

            historical = cashback_dashboard(
                configured_programs(),
                [],
                date(2026, 8, 16),
                [PaymentIntent("AMAZON", Decimal("100"), "AED", "ONLINE")],
            )
            historical_cards = cast(list[dict[str, object]], historical["cards"])
            self.assertEqual(
                {card["card"] for card in historical_cards},
                {"RAK_WORLD", "SC_PLATINUM_X", "EI_AMAZON"},
            )

    def test_legacy_notification_status_is_migrated_to_active(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.upsert(
                [
                    {
                        "source_event_id": "legacy-message:1",
                        "occurred_at": "2026-08-16T12:30:00+04:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "25.50",
                        "merchant": "Example",
                        "status": "PROVISIONAL",
                    }
                ]
            )

            rows = store.rows(date(2026, 8, 16), date(2026, 8, 16))

            self.assertEqual(rows[0]["status"], "ACTIVE")

    def test_different_source_ids_with_same_normalized_identity_are_deduplicated(
        self,
    ) -> None:
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

            self.assertEqual(
                result,
                {"inserted": 0, "updated": 0, "unchanged": 0, "duplicates": 1},
            )
            self.assertEqual(store.stats()["event_count"], 1)

    def test_source_event_replay_is_immutable_and_requires_correction_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            event = {
                "source_event_id": "immutable-source:1",
                "occurred_at": "2026-08-16T12:30:00+04:00",
                "card_code": "RAK_WORLD",
                "amount_aed": "25.50",
                "currency": "AED",
                "purchase_type": "GROCERY",
                "channel": "PHYSICAL_POS",
                "merchant": "Carrefour Market",
            }
            store.upsert([event])

            metadata_replay = store.upsert(
                [{**event, "purchase_type": "DINING", "tags": ["reprocessed"]}]
            )
            self.assertEqual(
                metadata_replay,
                {"inserted": 0, "updated": 0, "unchanged": 1, "duplicates": 0},
            )
            self.assertEqual(
                store.rows(date(2026, 8, 1), date(2026, 8, 31))[0]["purchase_type"],
                "GROCERY",
            )

            with self.assertRaisesRegex(ValueError, "corrections path"):
                store.upsert([{**event, "amount_aed": "999"}])
            stored = store.rows(date(2026, 8, 1), date(2026, 8, 31))[0]
            self.assertEqual(stored["amount_aed_minor"], 2550)
            self.assertEqual(stored["merchant"], "Carrefour Market")

            correction = store.correct_event(
                {
                    "correction_id": "immutable-correction:1",
                    "source_event_id": "immutable-source:1",
                    "source": "manual-review",
                    "reason": "Issuer correction",
                    "changes": {"amount_aed": "999", "merchant": "Carrefour Express"},
                }
            )
            self.assertFalse(correction["idempotent_replay"])
            corrected = store.rows(date(2026, 8, 1), date(2026, 8, 31))[0]
            self.assertEqual(corrected["amount_aed_minor"], 99900)
            self.assertEqual(corrected["merchant"], "Carrefour Express")
            self.assertEqual(store.stats()["correction_count"], 1)

            with self.assertRaisesRegex(ValueError, "corrections path"):
                store.upsert([event])

    def test_migrated_duplicate_identity_key_does_not_break_exact_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            event = {
                "source_event_id": "legacy-source:1",
                "occurred_at": "2026-08-16T12:30:00+04:00",
                "card_code": "RAK_WORLD",
                "amount_aed": "25.50",
                "currency": "AED",
                "purchase_type": "GROCERY",
                "channel": "PHYSICAL_POS",
                "merchant": "Carrefour Market",
            }
            CashbackEventStore(database).upsert([event])

            # Simulate two legacy rows that predate identity_key.  Migration
            # assigns a deterministic collision suffix to the second row.
            with sqlite3.connect(database) as connection:
                connection.execute("DROP INDEX idx_cashback_events_identity")
                columns = [
                    row[1]
                    for row in connection.execute("PRAGMA table_info(cashback_events)")
                ]
                values = list(
                    connection.execute(
                        "SELECT * FROM cashback_events WHERE source_event_id = ?",
                        (event["source_event_id"],),
                    ).fetchone()
                )
                source_index = columns.index("source_event_id")
                identity_index = columns.index("identity_key")
                values[source_index] = "legacy-source:2"
                values[identity_index] = None
                connection.execute(
                    "UPDATE cashback_events SET identity_key = NULL WHERE source_event_id = ?",
                    (event["source_event_id"],),
                )
                placeholders = ", ".join("?" for _ in columns)
                connection.execute(
                    f"INSERT INTO cashback_events ({', '.join(columns)}) VALUES ({placeholders})",
                    values,
                )

            migrated = CashbackEventStore(database)
            identity_keys = {
                row["identity_key"]
                for row in migrated.rows(date(2026, 8, 1), date(2026, 8, 31))
            }
            self.assertEqual(len(identity_keys), 2)
            replay = migrated.upsert([{**event, "source_event_id": "legacy-source:2"}])
            self.assertEqual(
                replay,
                {"inserted": 0, "updated": 0, "unchanged": 1, "duplicates": 0},
            )

    def test_legacy_offset_identity_migration_replays_and_preserves_refunds(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            purchase = {
                "source_event_id": "legacy-fixture:purchase:1",
                "occurred_at": "2026-08-16T12:30:00+04:00",
                "card_code": "RAK_WORLD",
                "amount_aed": "10000",
                "currency": "AED",
                "purchase_type": "GROCERY",
                "channel": "PHYSICAL_POS",
                "merchant": "Legacy Market",
            }
            refund = {
                **purchase,
                "source_event_id": "legacy-fixture:refund:1",
                "occurred_at": "2026-08-17T12:30:00+04:00",
                "amount_aed": "100",
                "event_type": "REFUND",
            }
            store = CashbackEventStore(database)
            normalized_purchase = store.validate([purchase])[0]
            normalized_refund = store.validate([refund])[0]

            def old_identity(event: dict[str, Any], normalized: dict[str, Any]) -> str:
                return hashlib.sha256(
                    "|".join(
                        (
                            str(event["occurred_at"]),
                            str(normalized["card_code"]),
                            str(normalized["amount_aed_minor"]),
                            str(normalized["currency"]),
                            str(normalized["event_type"]),
                            str(normalized["merchant"]).upper(),
                        )
                    ).encode()
                ).hexdigest()

            purchase_identity = old_identity(purchase, normalized_purchase)
            refund_identity = old_identity(refund, normalized_refund)
            legacy_rows = [
                {
                    **normalized_purchase,
                    "occurred_at": purchase["occurred_at"],
                    "identity_key": purchase_identity,
                    "created_at": "2026-08-01 00:00:00",
                },
                {
                    **normalized_purchase,
                    "source_event_id": "legacy-fixture:purchase:2",
                    "occurred_at": purchase["occurred_at"],
                    "identity_key": hashlib.sha256(
                        f"{purchase_identity}|legacy:legacy-fixture:purchase:2".encode()
                    ).hexdigest(),
                    "created_at": "2026-08-01 00:00:01",
                },
                {
                    **normalized_refund,
                    "occurred_at": refund["occurred_at"],
                    "identity_key": refund_identity,
                    "created_at": "2026-08-01 00:00:02",
                },
            ]
            columns = tuple(legacy_rows[0])
            with sqlite3.connect(database) as connection:
                for row in legacy_rows:
                    connection.execute(
                        f"INSERT INTO cashback_events ({', '.join(columns)}) "
                        f"VALUES ({', '.join('?' for _ in columns)})",
                        tuple(row[column] for column in columns),
                    )

            migrated = CashbackEventStore(database)
            rows = migrated.rows(date(2026, 8, 16), date(2026, 8, 18))
            self.assertEqual(len(rows), 3)
            self.assertEqual(
                {row["occurred_at"] for row in rows},
                {
                    "2026-08-16T08:30:00+00:00",
                    "2026-08-17T08:30:00+00:00",
                },
            )
            purchase_rows = [
                row
                for row in rows
                if row["source_event_id"].startswith("legacy-fixture:purchase:")
            ]
            self.assertEqual(
                {row["source_event_id"] for row in purchase_rows},
                {"legacy-fixture:purchase:1", "legacy-fixture:purchase:2"},
            )
            base_identity = hashlib.sha256(
                "|".join(
                    (
                        "2026-08-16T08:30:00+00:00",
                        "RAK_WORLD",
                        "1000000",
                        "AED",
                        "PURCHASE",
                        "LEGACY MARKET",
                    )
                ).encode()
            ).hexdigest()
            self.assertEqual(
                {row["identity_key"] for row in purchase_rows},
                {
                    base_identity,
                    hashlib.sha256(
                        f"{base_identity}|legacy:legacy-fixture:purchase:2".encode()
                    ).hexdigest(),
                },
            )
            exact = migrated.upsert([purchase, refund])
            self.assertEqual(
                exact,
                {"inserted": 0, "updated": 0, "unchanged": 2, "duplicates": 0},
            )
            alternate = migrated.upsert(
                [
                    {**purchase, "source_event_id": "alternate-source:purchase"},
                    {**refund, "source_event_id": "alternate-source:refund"},
                ]
            )
            self.assertEqual(
                alternate,
                {"inserted": 0, "updated": 0, "unchanged": 0, "duplicates": 2},
            )
            self.assertEqual(
                len(
                    [
                        row
                        for row in migrated.rows(date(2026, 8, 16), date(2026, 8, 18))
                        if row["event_type"] == "REFUND"
                    ]
                ),
                1,
            )
            dashboard = build_live_dashboard(migrated, date(2026, 8, 17))
            rak = next(
                card for card in dashboard["cards"] if card["card"] == "RAK_WORLD"
            )
            self.assertEqual(rak["total_spend_aed"], "20000")
            self.assertEqual(rak["expected_cashback_aed"], "290.00")

    def test_legacy_naive_occurred_at_fails_without_mutating_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            event = {
                "source_event_id": "legacy-naive:1",
                "occurred_at": "2026-08-16T12:30:00+04:00",
                "card_code": "RAK_WORLD",
                "amount_aed": "25",
                "merchant": "Legacy Market",
            }
            CashbackEventStore(database).upsert([event])
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "UPDATE cashback_events SET occurred_at = ?, identity_key = ? "
                    "WHERE source_event_id = ?",
                    (
                        "2026-08-16T08:30:00",
                        "legacy-identity",
                        event["source_event_id"],
                    ),
                )

            with self.assertRaisesRegex(ValueError, "invalid occurred_at"):
                CashbackEventStore(database)

            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT occurred_at, identity_key FROM cashback_events "
                        "WHERE source_event_id = ?",
                        (event["source_event_id"],),
                    ).fetchone(),
                    ("2026-08-16T08:30:00", "legacy-identity"),
                )

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
            store.upsert(
                [
                    {**base, "source_event_id": "purchase", "amount_aed": "100"},
                    {
                        **base,
                        "source_event_id": "refund",
                        "amount_aed": "25",
                        "event_type": "REFUND",
                    },
                    {
                        **base,
                        "source_event_id": "ignored",
                        "amount_aed": "1000",
                        "status": "IGNORED",
                    },
                ]
            )

            dashboard = build_live_dashboard(
                store,
                date(2026, 8, 16),
                memberships=(
                    {"card_code": "SC_PLATINUM_X", "coverage": "HELD", "sc_held": True},
                ),
            )

            sc = next(
                card for card in dashboard["cards"] if card["card"] == "SC_PLATINUM_X"
            )
            online = next(
                bucket for bucket in sc["buckets"] if bucket["code"] == "SC_ONLINE"
            )
            self.assertEqual(sc["total_spend_aed"], "100")
            self.assertEqual(online["spend_aed"], "100")

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
                store.upsert(
                    [
                        {
                            **base,
                            "source_event_id": "invalid-reversal",
                            "amount_aed": "50",
                            "event_type": "REVERSAL",
                        }
                    ]
                )
            store.upsert(
                [
                    {
                        **base,
                        "source_event_id": "reversal",
                        "amount_aed": "50",
                        "event_type": "REVERSAL",
                        "reversal_of": "purchase",
                    }
                ]
            )

            dashboard = build_live_dashboard(store, date(2026, 8, 16))

            rak = next(
                card for card in dashboard["cards"] if card["card"] == "RAK_WORLD"
            )
            dining = next(
                bucket for bucket in rak["buckets"] if bucket["code"] == "RAK_DINING"
            )
            self.assertEqual(rak["total_spend_aed"], "200")
            self.assertEqual(dining["spend_aed"], "200")

    def test_receipt_closed_period_does_not_leak_into_current_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.ensure_period(
                {
                    "period_id": "rak-aug-closed",
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-08-01T00:00:00Z",
                    "period_end": "2026-08-16T00:00:00Z",
                }
            )
            store.ensure_period(
                {
                    "period_id": "rak-aug-current",
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-08-16T00:00:00Z",
                    "period_end": "2026-09-01T00:00:00Z",
                }
            )
            store.upsert(
                [
                    {
                        "source_event_id": "closed-period-purchase",
                        "occurred_at": "2026-08-10T12:30:00+04:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "10000",
                        "purchase_type": "GROCERY",
                        "channel": "PHYSICAL_POS",
                        "merchant": "Closed Period Market",
                    },
                    {
                        "source_event_id": "current-period-purchase",
                        "occurred_at": "2026-08-18T12:30:00+04:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "25",
                        "purchase_type": "GROCERY",
                        "channel": "PHYSICAL_POS",
                        "merchant": "Current Period Market",
                    },
                ]
            )
            closed = store.record_statement_receipt(
                {
                    "receipt_id": "rak-aug-receipt",
                    "source_identity": "mail:rak:aug",
                    "card_code": "RAK_WORLD",
                    "original_received_at": "2026-08-16T00:00:00Z",
                    "period_id": "rak-aug-closed",
                }
            )
            self.assertEqual(closed["status"], "CLOSED")

            dashboard = build_live_dashboard(store, date(2026, 8, 20))
            rak = next(
                card for card in dashboard["cards"] if card["card"] == "RAK_WORLD"
            )
            self.assertEqual(rak["total_spend_aed"], "25")
            self.assertEqual(rak["transaction_count"], 1)

    def test_equivalent_refund_timestamps_deduct_cashback_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            purchase = {
                "source_event_id": "purchase-for-refund",
                "occurred_at": "2026-08-16T12:30:00+04:00",
                "card_code": "RAK_WORLD",
                "amount_aed": "10000",
                "purchase_type": "GROCERY",
                "channel": "PHYSICAL_POS",
                "merchant": "Refunded Market",
            }
            result = store.upsert(
                [
                    purchase,
                    {
                        **purchase,
                        "source_event_id": "refund-plus-four",
                        "occurred_at": "2026-08-17T12:30:00+04:00",
                        "amount_aed": "100",
                        "event_type": "REFUND",
                    },
                    {
                        **purchase,
                        "source_event_id": "refund-utc",
                        "occurred_at": "2026-08-17T08:30:00Z",
                        "amount_aed": "100",
                        "event_type": "REFUND",
                    },
                ]
            )

            self.assertEqual(result["inserted"], 2)
            self.assertEqual(result["duplicates"], 1)
            dashboard = build_live_dashboard(store, date(2026, 8, 17))
            rak = next(
                card for card in dashboard["cards"] if card["card"] == "RAK_WORLD"
            )
            self.assertEqual(rak["total_spend_aed"], "10000")
            self.assertEqual(rak["expected_cashback_aed"], "290.00")

    def test_ingest_heartbeat_controls_feed_freshness_even_when_scan_is_empty(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            completed_at = datetime.now(timezone.utc).isoformat()

            receipt = store.create_ingest_receipt(
                {
                    "source": "outlook",
                    "completed_at": completed_at,
                    "scanned_count": 0,
                    "accepted_count": 0,
                    "cursor": "message-cursor",
                }
            )
            result = store.record_ingest_success(
                {
                    "source": "outlook",
                    "completed_at": completed_at,
                    "scanned_count": 0,
                    "accepted_count": 0,
                    "cursor": "message-cursor",
                    "service_receipt": receipt,
                }
            )
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
            store.upsert(
                [
                    {
                        "source_event_id": "uncertain",
                        "occurred_at": "2026-08-16T12:30:00+04:00",
                        "card_code": "SC_PLATINUM_X",
                        "amount_aed": "100",
                        "purchase_type": "GROCERY",
                        "channel": "ONLINE",
                        "merchant": "Unknown Market",
                        "confidence": 0.61,
                    }
                ]
            )

            dashboard = build_live_dashboard(store, date(2026, 8, 16))

            self.assertNotIn("review_count", dashboard)

    def test_general_purchase_with_explicit_classification_does_not_require_review(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.upsert(
                [
                    {
                        "source_event_id": "configured-default",
                        "occurred_at": "2026-08-16T12:30:00+04:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "16",
                        "purchase_type": "GENERAL",
                        "channel": "APPLE_PAY_POS",
                        "merchant": "Best of Vends",
                        "confidence": 0.95,
                        "review_required": False,
                    }
                ]
            )

            dashboard = build_live_dashboard(store, date(2026, 8, 16))

            self.assertNotIn("review_count", dashboard)
            self.assertEqual(store.review_queue(20), [])

            store.correct_event(
                {
                    "correction_id": "approve-live-classification",
                    "source_event_id": "configured-default",
                    "source": "dashboard-review",
                    "changes": {"review_required": False},
                }
            )
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

    def test_statement_reconciliation_replaces_live_variances_with_authoritative_rows(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            common = {
                "occurred_at": "2026-08-10T12:30:00+04:00",
                "card_code": "RAK_WORLD",
                "purchase_type": "GROCERY",
                "channel": "PHYSICAL_POS",
            }
            store.upsert(
                [
                    {
                        **common,
                        "source_event_id": "matched-notification",
                        "amount_aed": "100",
                        "merchant": "Carrefour Market",
                    },
                    {
                        **common,
                        "source_event_id": "missing-from-statement",
                        "amount_aed": "50",
                        "merchant": "Example Cafe",
                    },
                ]
            )
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
            rak = next(
                card for card in dashboard["cards"] if card["card"] == "RAK_WORLD"
            )
            self.assertEqual(rak["total_spend_aed"], "180")
            self.assertEqual(dashboard["data_status"]["variance_count"], 1)

    def test_legacy_boundary_offset_replay_uses_local_period_only_for_exact_replay(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            store = CashbackEventStore(database)
            payload = {
                "statement_reference": "legacy-boundary-replay",
                "statement_sha256": statement_digest("legacy-boundary-replay"),
                "card_code": "EI_AMAZON",
                "period_start": "2026-09-01",
                "period_end": "2026-09-30",
                "transactions": [
                    {
                        "statement_transaction_id": "line-1",
                        "occurred_at": "2026-09-30T23:30:00-04:00",
                        "amount_aed": "20",
                        "merchant": "Example",
                    }
                ],
            }
            with self.assertRaisesRegex(ValueError, "outside the statement period"):
                store.reconcile_statement(payload)

            # Simulate the old successful run and persisted statement row whose
            # offset-local date was inside the inclusive legacy period.
            wide_payload = {
                **payload,
                "period_end": "2026-10-01",
            }
            store.reconcile_statement(wide_payload)
            period_start = date(2026, 9, 1)
            period_end = date(2026, 9, 30)
            events, transaction_ids = _canonical_statement_events(
                payload,
                statement_reference=payload["statement_reference"],
                card_code=payload["card_code"],
                period_start=period_start,
                period_end=date(2026, 10, 1),
            )
            legacy_digest = _statement_content_digest(
                [
                    {
                        **event,
                        "occurred_at": datetime.fromisoformat(
                            payload["transactions"][0]["occurred_at"].replace(
                                "Z", "+00:00"
                            )
                        ).isoformat(),
                    }
                    for event in events
                ],
                transaction_ids,
                statement_reference=payload["statement_reference"],
                card_code=payload["card_code"],
                period_start=period_start,
                period_end=period_end,
            )
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    UPDATE reconciliation_runs
                    SET period_end = ?, statement_content_sha256 = ?
                    WHERE statement_reference = ?
                    """,
                    (
                        payload["period_end"],
                        legacy_digest,
                        payload["statement_reference"],
                    ),
                )
            replay = store.reconcile_statement(payload)
            self.assertTrue(replay["idempotent_replay"])

            changed = {
                **payload,
                "transactions": [{**payload["transactions"][0], "amount_aed": "21"}],
            }
            with self.assertRaisesRegex(ValueError, "outside the statement period"):
                store.reconcile_statement(changed)

    def test_space_separated_legacy_digest_replays_but_changed_content_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            store = CashbackEventStore(database)
            payload = {
                "statement_reference": "legacy-space-replay",
                "statement_sha256": statement_digest("legacy-space-replay"),
                "card_code": "EI_AMAZON",
                "period_start": "2026-09-01",
                "period_end": "2026-09-30",
                "transactions": [
                    {
                        "statement_transaction_id": "line-1",
                        "occurred_at": "2026-09-10 12:30:00+04:00",
                        "amount_aed": "20",
                        "merchant": "Example",
                    }
                ],
            }
            first = store.reconcile_statement(payload)
            self.assertFalse(first["idempotent_replay"])
            events, transaction_ids = _canonical_statement_events(
                payload,
                statement_reference=payload["statement_reference"],
                card_code=payload["card_code"],
                period_start=date(2026, 9, 1),
                period_end=date(2026, 9, 30),
            )
            legacy_digest = _statement_content_digest(
                [
                    {
                        **event,
                        "occurred_at": datetime.fromisoformat(
                            payload["transactions"][0]["occurred_at"].replace(
                                "Z", "+00:00"
                            )
                        ).isoformat(),
                    }
                    for event in events
                ],
                transaction_ids,
                statement_reference=payload["statement_reference"],
                card_code=payload["card_code"],
                period_start=date(2026, 9, 1),
                period_end=date(2026, 9, 30),
            )
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    UPDATE reconciliation_runs
                    SET statement_content_sha256 = ?
                    WHERE statement_reference = ?
                    """,
                    (legacy_digest, payload["statement_reference"]),
                )
            replay = store.reconcile_statement(payload)
            self.assertTrue(replay["idempotent_replay"])
            changed = {
                **payload,
                "transactions": [{**payload["transactions"][0], "merchant": "Changed"}],
            }
            with self.assertRaisesRegex(
                ValueError, "different statement content or digest"
            ):
                store.reconcile_statement(changed)

    def test_distinct_statement_ids_with_identical_economics_both_count_spend(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            common = {
                "occurred_at": "2026-08-10T12:30:00+04:00",
                "amount_aed": "100",
                "merchant": "Example",
                "purchase_type": "GROCERY",
                "channel": "PHYSICAL_POS",
            }
            payload = {
                "statement_reference": "duplicate-economics",
                "statement_sha256": statement_digest("duplicate-economics"),
                "card_code": "RAK_WORLD",
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
                "transactions": [
                    {**common, "statement_transaction_id": "line-1"},
                    {**common, "statement_transaction_id": "line-2"},
                ],
            }
            result = store.reconcile_statement(payload)
            self.assertEqual(result["matched"], 0)
            self.assertEqual(result["statement_only"], 2)
            self.assertEqual(result["notification_only"], 0)
            rows = store.rows(date(2026, 8, 1), date(2026, 8, 31))
            statement_rows = [
                row
                for row in rows
                if row["statement_reference"] == "duplicate-economics"
            ]
            self.assertEqual(len(statement_rows), 2)
            self.assertEqual(
                sum(int(row["amount_aed_minor"]) for row in statement_rows), 20_000
            )
            self.assertEqual(
                {row["source_event_id"] for row in statement_rows},
                {
                    "statement:duplicate-economics:line-1",
                    "statement:duplicate-economics:line-2",
                },
            )

    def test_correction_is_idempotent_and_recalculates_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.upsert(
                [
                    {
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
                    }
                ]
            )
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
                    "ai_trace": [
                        {"policy": "unresolved-purchase-type", "model": "gpt-5.6-sol"}
                    ],
                },
            }

            first = store.correct_event(correction)
            replay = store.correct_event(correction)
            dashboard = build_live_dashboard(store, date(2026, 8, 16))

            self.assertFalse(first["idempotent_replay"])

            self.assertTrue(replay["idempotent_replay"])
            rak = next(
                card for card in dashboard["cards"] if card["card"] == "RAK_WORLD"
            )
            grocery = next(
                bucket for bucket in rak["buckets"] if bucket["code"] == "RAK_GROCERY"
            )
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

    def test_concurrent_unrelated_corrections_preserve_both_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.upsert(
                [
                    {
                        "source_event_id": "concurrent-correction",
                        "occurred_at": "2026-08-16T12:30:00+04:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "120",
                        "merchant": "Example",
                    }
                ]
            )
            corrections = [
                {
                    "correction_id": "concurrent-bucket",
                    "source_event_id": "concurrent-correction",
                    "source": "manual-review",
                    "changes": {"bucket_code": "RAK_GROCERY"},
                },
                {
                    "correction_id": "concurrent-document",
                    "source_event_id": "concurrent-correction",
                    "source": "manual-review",
                    "changes": {"document_url": "Finance Evidence/manual.pdf"},
                },
            ]
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(store.correct_event, corrections))
            self.assertEqual(
                {result["correction_id"] for result in results},
                {"concurrent-bucket", "concurrent-document"},
            )
            stored = store.rows(date(2026, 8, 1), date(2026, 8, 31))[0]
            self.assertEqual(stored["bucket_code"], "RAK_GROCERY")
            self.assertEqual(stored["document_url"], "Finance Evidence/manual.pdf")

    def test_ai_cannot_override_manually_locked_mutable_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            store = CashbackEventStore(database)
            store.upsert(
                [
                    {
                        "source_event_id": "manual-locks",
                        "occurred_at": "2026-08-16T12:30:00+04:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "120",
                        "merchant": "Example",
                    }
                ]
            )
            store.correct_event(
                {
                    "correction_id": "manual-locks:all",
                    "source": "manual-review",
                    "source_event_id": "manual-locks",
                    "changes": {
                        "bucket_code": "RAK_GROCERY",
                        "review_required": False,
                        "status": "IGNORED",
                        "document_url": "Finance Evidence/manual.pdf",
                    },
                }
            )

            def current_fields() -> dict[str, object]:
                with sqlite3.connect(database) as connection:
                    row = connection.execute(
                        """
                        SELECT bucket_code, review_required, status, document_url
                        FROM cashback_events
                        WHERE source_event_id = ?
                        """,
                        ("manual-locks",),
                    ).fetchone()
                assert row is not None
                return dict(
                    zip(
                        ("bucket_code", "review_required", "status", "document_url"),
                        row,
                        strict=True,
                    )
                )

            original = current_fields()
            for index, (field, value) in enumerate(
                {
                    "bucket_code": "RAK_DINING",
                    "review_required": True,
                    "status": "ACTIVE",
                    "document_url": "Finance Evidence/ai.pdf",
                }.items(),
                start=1,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "manual correction|AI corrections cannot modify protected",
                ):
                    store.correct_event(
                        {
                            "correction_id": f"ai-locks:{index}",
                            "source": "ai-policy:classify",
                            "source_event_id": "manual-locks",
                            "changes": {field: value},
                        }
                    )
            stored = current_fields()
            for field in ("bucket_code", "review_required", "status", "document_url"):
                self.assertEqual(stored[field], original[field])

    def test_ai_correction_endpoint_rejects_protected_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.upsert(
                [
                    {
                        "source_event_id": "ai-protected",
                        "occurred_at": "2026-08-16T12:30:00+04:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "120",
                        "purchase_type": "GENERAL",
                        "channel": "UNKNOWN",
                        "merchant": "Unknown",
                    }
                ]
            )

            with self.assertRaisesRegex(
                ValueError, "AI corrections cannot modify protected"
            ):
                store.correct_event(
                    {
                        "correction_id": "ai-unsafe-1",
                        "source_event_id": "ai-protected",
                        "source": "ai-policy:classify-unresolved",
                        "changes": {"amount_aed": "1"},
                    }
                )

            stored = store.rows(date(2026, 8, 1), date(2026, 8, 31))[0]
            self.assertEqual(stored["amount_aed_minor"], 12000)
            self.assertEqual(store.stats()["correction_count"], 0)

    def test_third_week_and_near_full_bucket_alerts_are_calculated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.upsert(
                [
                    {
                        "source_event_id": "near-full",
                        "occurred_at": "2026-08-21T12:30:00+04:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "2750",
                        "purchase_type": "GROCERY",
                        "channel": "PHYSICAL_POS",
                        "merchant": "Carrefour",
                    }
                ]
            )

            dashboard = build_live_dashboard(store, date(2026, 8, 26))

            keys = {alert["key"] for alert in dashboard["alerts"]}
            self.assertIn("minimum:RAK_WORLD:2026-08-06:2026-09-05", keys)
            self.assertIn("bucket:RAK_WORLD:RAK_GROCERY:near_full", keys)
            rak = next(
                card for card in dashboard["cards"] if card["card"] == "RAK_WORLD"
            )
            self.assertEqual(rak["transaction_count"], 1)

    def test_unmet_card_targets_warn_during_the_final_week(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")

            dashboard = build_live_dashboard(
                store,
                date(2026, 8, 30),
                memberships=(
                    {"card_code": "SC_PLATINUM_X", "coverage": "HELD", "sc_held": True},
                ),
            )

            keys = {alert["key"] for alert in dashboard["alerts"]}
            self.assertIn("close:RAK_WORLD:2026-08-06:2026-09-05", keys)
            self.assertIn("close:SC_PLATINUM_X:2026-08-06:2026-09-05", keys)

    def test_finalization_opens_the_next_configured_card_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = CashbackEventStore(root / "events.sqlite3")
            configuration = json.loads(
                Path("config/cashback-programs.json").read_text(encoding="utf-8")
            )
            for program in configuration["programs"]:
                if program["card"] == "RAK_WORLD":
                    program["statement_cycle"]["close_day"] = 15
            config_path = root / "cashback-programs.json"
            config_path.write_text(json.dumps(configuration), encoding="utf-8")
            store.reconcile_statement(
                {
                    "statement_reference": "RAK-2026-08-15",
                    "statement_sha256": statement_digest("RAK-2026-08-15"),
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-07-16",
                    "period_end": "2026-08-15",
                    "transactions": [],
                }
            )
            store.finalize_period(
                {
                    "statement_reference": "RAK-2026-08-15",
                    "statement_sha256": statement_digest("RAK-2026-08-15"),
                    "statement_evidence_reference": "sha256:test",
                    "actual_import_receipt": actual_receipt(
                        "RAK-2026-08-15",
                        "2026-07-16",
                        "2026-08-15",
                        account_id="RAK_WORLD",
                    ),
                    "actual_import_receipt_sha256": actual_receipt_digest(
                        actual_receipt(
                            "RAK-2026-08-15",
                            "2026-07-16",
                            "2026-08-15",
                            account_id="RAK_WORLD",
                        )
                    ),
                    "statement_document_url": "https://evidence.example/rak.pdf",
                },
                program_config_path=config_path,
            )
            open_period = next(
                row for row in store.period_rows() if row["status"] == "OPEN"
            )
            self.assertEqual(
                open_period["period_id"],
                "cashback-period:RAK_WORLD:2026-08-16T00:00:00.000000Z:2026-09-16T00:00:00.000000Z",
            )
            self.assertEqual(open_period["period_start"], "2026-08-16T00:00:00.000000Z")
            self.assertEqual(open_period["period_end"], "2026-09-16T00:00:00.000000Z")

    def test_period_finalization_requires_statement_evidence_and_verified_actual_import(
        self,
    ) -> None:
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
            with self.assertRaisesRegex(
                ValueError, "successful statement reconciliation"
            ):
                store.finalize_period(
                    {
                        "statement_reference": "EI-2026-08",
                        "statement_sha256": statement_digest("EI-2026-08"),
                        "statement_evidence_reference": "sha256:premature",
                        "statement_document_url": "Finance Evidence/ei-premature.pdf",
                        "actual_import_receipt": premature_receipt,
                        "actual_import_receipt_sha256": actual_receipt_digest(
                            premature_receipt
                        ),
                    }
                )
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
            self.assertEqual(
                finalized["close_id"], "cashback-close:EI_AMAZON:2026-08-01:2026-08-31"
            )
            self.assertFalse(finalized["idempotent_replay"])
            self.assertTrue(replay["idempotent_replay"])
            self.assertEqual(replay["close_id"], finalized["close_id"])
            periods = store.period_rows()
            self.assertEqual(periods[0]["status"], "OPEN")
            self.assertEqual(periods[1]["status"], "FINALIZED")

    def test_period_with_variances_requires_explicit_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.upsert(
                [
                    {
                        "source_event_id": "notification-only",
                        "occurred_at": "2026-08-10T12:30:00+04:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "10",
                        "merchant": "Example",
                    }
                ]
            )
            store.reconcile_statement(
                {
                    "statement_reference": "RAK-2026-09-05",
                    "statement_sha256": statement_digest("RAK-2026-09-05"),
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-08-06",
                    "period_end": "2026-09-05",
                    "transactions": [],
                }
            )
            payload = {
                "statement_reference": "RAK-2026-09-05",
                "statement_sha256": statement_digest("RAK-2026-09-05"),
                "statement_evidence_reference": "sha256:def",
                "actual_import_receipt": actual_receipt(
                    "RAK-2026-09-05",
                    "2026-08-06",
                    "2026-09-05",
                    account_id="RAK_WORLD",
                ),
                "actual_import_receipt_sha256": actual_receipt_digest(
                    actual_receipt(
                        "RAK-2026-09-05",
                        "2026-08-06",
                        "2026-09-05",
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
                "transactions": [
                    {
                        "statement_transaction_id": "line-1",
                        "occurred_at": "2026-09-10T12:30:00+04:00",
                        "amount_aed": "20",
                        "merchant": "Example",
                    }
                ],
            }
            first = store.reconcile_statement(payload)
            self.assertFalse(first["idempotent_replay"])

            changed_content = {
                **payload,
                "transactions": [{**payload["transactions"][0], "merchant": "Changed"}],
            }
            with self.assertRaisesRegex(
                ValueError, "different statement content or digest"
            ):
                store.reconcile_statement(changed_content)

            changed_digest = {**payload, "statement_sha256": statement_digest("other")}
            with self.assertRaisesRegex(
                ValueError, "different statement content or digest"
            ):
                store.reconcile_statement(changed_digest)

            self.assertEqual(store.stats()["event_count"], 1)

    def test_actual_verified_boolean_without_receipt_digest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            reference = "EI-2026-10"
            store.reconcile_statement(
                {
                    "statement_reference": reference,
                    "statement_sha256": statement_digest(reference),
                    "card_code": "EI_AMAZON",
                    "period_start": "2026-10-01",
                    "period_end": "2026-10-31",
                    "transactions": [],
                }
            )
            with self.assertRaisesRegex(ValueError, "actual_import_receipt_sha256"):
                store.finalize_period(
                    {
                        "statement_reference": reference,
                        "statement_sha256": statement_digest(reference),
                        "statement_evidence_reference": "sha256:evidence",
                        "statement_document_url": "Finance Evidence/ei.pdf",
                        "actual_import_verified": True,
                    }
                )
            self.assertNotIn(
                "FINALIZED", {row["status"] for row in store.period_rows()}
            )

    def test_actual_close_requires_trusted_readback_receipt_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            reference = "EI-2026-10-receipt-shape"
            statement_sha256 = statement_digest(reference)
            store.reconcile_statement(
                {
                    "statement_reference": reference,
                    "statement_sha256": statement_sha256,
                    "card_code": "EI_AMAZON",
                    "period_start": "2026-10-01",
                    "period_end": "2026-10-31",
                    "transactions": [],
                }
            )
            close_fields = {
                "statement_reference": reference,
                "statement_sha256": statement_sha256,
                "statement_evidence_reference": "sha256:evidence",
                "statement_document_url": "Finance Evidence/ei.pdf",
            }
            with self.assertRaisesRegex(ValueError, "missing required fields"):
                store.finalize_period(
                    {
                        **close_fields,
                        "actual_import_receipt": {},
                        "actual_import_receipt_sha256": statement_digest("empty"),
                    }
                )
            with self.assertRaisesRegex(ValueError, "missing required fields"):
                store.finalize_period(
                    {
                        **close_fields,
                        "actual_import_receipt": {
                            "outbox_id": "caller-invented",
                            "observed_payload_sha256": statement_digest("payload"),
                            "invariants_passed": True,
                        },
                        "actual_import_receipt_sha256": statement_digest(
                            "caller-invented"
                        ),
                    }
                )
            with self.assertRaisesRegex(ValueError, "readback object"):
                store.finalize_period(
                    {
                        **close_fields,
                        "actual_import_receipt_sha256": statement_digest("digest-only"),
                    }
                )
            receipt = actual_receipt(reference, "2026-10-01", "2026-10-31")
            mismatched_readback = {
                **receipt,
                "observed_payload_sha256": statement_digest("different-readback"),
            }
            with self.assertRaisesRegex(ValueError, "payload digests differ"):
                store.finalize_period(
                    {
                        **close_fields,
                        "actual_import_receipt": mismatched_readback,
                        "actual_import_receipt_sha256": actual_receipt_digest(
                            mismatched_readback
                        ),
                    }
                )
            with self.assertRaisesRegex(ValueError, "invariants must pass"):
                failed_receipt = {**receipt, "invariants_passed": False}
                store.finalize_period(
                    {
                        **close_fields,
                        "actual_import_receipt": failed_receipt,
                        "actual_import_receipt_sha256": actual_receipt_digest(
                            failed_receipt
                        ),
                    }
                )
            self.assertNotIn(
                "FINALIZED", {row["status"] for row in store.period_rows()}
            )

    def test_actual_close_rejects_stale_post_actual_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            reference = "EI-2026-10-stale"
            statement_sha256 = statement_digest(reference)
            store.reconcile_statement(
                {
                    "statement_reference": reference,
                    "statement_sha256": statement_sha256,
                    "card_code": "EI_AMAZON",
                    "period_start": "2026-10-01",
                    "period_end": "2026-10-31",
                    "transactions": [],
                }
            )
            receipt = actual_receipt(reference, "2026-10-01", "2026-10-31")
            receipt["state"] = "ACTUAL_OBSERVED"
            with self.assertRaisesRegex(ValueError, "state must be COMMITTED"):
                store.finalize_period(
                    {
                        "statement_reference": reference,
                        "statement_sha256": statement_sha256,
                        "statement_evidence_reference": "sha256:stale",
                        "statement_document_url": "Finance Evidence/stale.pdf",
                        "actual_import_receipt": receipt,
                        "actual_import_receipt_sha256": actual_receipt_digest(receipt),
                    }
                )
            self.assertNotIn(
                "FINALIZED", {row["status"] for row in store.period_rows()}
            )

    def test_actual_receipt_account_identity_must_match_reconciled_card(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            reference = "RAK-2026-09-05-account-identity"
            statement_sha256 = statement_digest(reference)
            store.reconcile_statement(
                {
                    "statement_reference": reference,
                    "statement_sha256": statement_sha256,
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-08-06",
                    "period_end": "2026-09-05",
                    "transactions": [],
                }
            )
            close_fields = {
                "statement_reference": reference,
                "statement_sha256": statement_sha256,
                "statement_evidence_reference": "sha256:account-identity",
                "statement_document_url": "Finance Evidence/rak-account.pdf",
            }
            wrong_account_receipt = actual_receipt(
                reference, "2026-08-06", "2026-09-05"
            )
            with self.assertRaisesRegex(ValueError, "account identity"):
                store.finalize_period(
                    {
                        **close_fields,
                        "actual_import_receipt": wrong_account_receipt,
                        "actual_import_receipt_sha256": actual_receipt_digest(
                            wrong_account_receipt
                        ),
                    }
                )
            exact_receipt = actual_receipt(
                reference,
                "2026-08-06",
                "2026-09-05",
                account_id="actual-account:RAK_WORLD",
                card_code="RAK_WORLD",
            )
            missing_card_receipt = {
                key: value for key, value in exact_receipt.items() if key != "card_code"
            }
            with self.assertRaisesRegex(
                ValueError, "missing required fields.*card_code"
            ):
                store.finalize_period(
                    {
                        **close_fields,
                        "actual_import_receipt": missing_card_receipt,
                        "actual_import_receipt_sha256": actual_receipt_digest(
                            missing_card_receipt
                        ),
                    }
                )
            exact_payload = {
                **close_fields,
                "actual_import_receipt": exact_receipt,
                "actual_import_receipt_sha256": actual_receipt_digest(exact_receipt),
            }
            finalized = store.finalize_period(exact_payload)
            self.assertEqual(finalized["status"], "FINALIZED")
            self.assertTrue(store.finalize_period(exact_payload)["idempotent_replay"])

    def test_legacy_reconciliation_digest_recovery_backfills_once_and_rejects_mismatch(
        self,
    ) -> None:
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
                store.reconcile_statement(
                    {**payload, "legacy_recovery_digest": "0" * 64}
                )
            recovered = store.reconcile_statement(
                {
                    **payload,
                    "legacy_recovery_digest": _legacy_recovery_digest(legacy_row),
                }
            )
            self.assertTrue(recovered["idempotent_replay"])
            self.assertTrue(recovered["legacy_digest_backfilled"])
            replay = store.reconcile_statement(payload)
            self.assertTrue(replay["idempotent_replay"])
            with self.assertRaisesRegex(
                ValueError, "different statement content or digest"
            ):
                store.reconcile_statement(
                    {
                        **payload,
                        "statement_sha256": statement_digest("changed-legacy"),
                    }
                )

    def test_concurrent_legacy_digest_recovery_binds_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            legacy_row = {
                "statement_reference": "legacy-EI-2026-10-race",
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

            first_read = threading.Event()
            second_connected = threading.Event()
            second_read = threading.Event()
            release_first_read = threading.Event()

            class GatedStore(CashbackEventStore):
                def __init__(
                    self,
                    path: Path,
                    *,
                    connected: threading.Event | None = None,
                    read: threading.Event | None = None,
                    release: threading.Event | None = None,
                ) -> None:
                    self._gate_enabled = False
                    self._connected = connected
                    self._read = read
                    self._release = release
                    super().__init__(path)
                    self._gate_enabled = True

                def _connect(self) -> sqlite3.Connection:
                    connection = super()._connect()
                    if not self._gate_enabled:
                        return connection
                    if self._connected is not None:
                        self._connected.set()

                    def trace(sql: str) -> None:
                        normalized_sql = " ".join(sql.split())
                        if not normalized_sql.startswith(
                            "SELECT * FROM reconciliation_runs "
                            "WHERE statement_reference ="
                        ):
                            return
                        if self._read is not None:
                            self._read.set()
                        if self._release is not None:
                            self._release.wait(5)

                    connection.set_trace_callback(trace)
                    return connection

            store_one = GatedStore(
                database,
                read=first_read,
                release=release_first_read,
            )
            store_two = GatedStore(
                database,
                connected=second_connected,
                read=second_read,
            )
            recovery_proof = _legacy_recovery_digest(legacy_row)
            payloads = (
                {
                    **legacy_row,
                    "statement_sha256": statement_digest("legacy-race-one"),
                    "transactions": [],
                    "legacy_recovery_digest": recovery_proof,
                },
                {
                    **legacy_row,
                    "statement_sha256": statement_digest("legacy-race-two"),
                    "transactions": [],
                    "legacy_recovery_digest": recovery_proof,
                },
            )

            def recover(
                store: CashbackEventStore, payload: dict[str, object]
            ) -> tuple[str, dict[str, Any] | str]:
                try:
                    return "ok", store.reconcile_statement(payload)
                except ValueError as error:
                    return "error", str(error)

            with ThreadPoolExecutor(max_workers=2) as executor:
                first_future = executor.submit(recover, store_one, payloads[0])
                self.assertTrue(first_read.wait(5))
                second_future = executor.submit(recover, store_two, payloads[1])
                try:
                    self.assertTrue(second_connected.wait(5))
                    second_read.wait(0.25)
                finally:
                    release_first_read.set()
                outcomes = [
                    first_future.result(timeout=10),
                    second_future.result(timeout=10),
                ]

            successful = [
                result
                for kind, result in outcomes
                if kind == "ok" and isinstance(result, dict)
            ]
            failures = [
                result
                for kind, result in outcomes
                if kind == "error" and isinstance(result, str)
            ]
            self.assertEqual(len(successful), 1)
            self.assertEqual(len(failures), 1)
            self.assertIn(
                "different statement content or digest",
                failures[0],
            )
            winner = successful[0]
            winner_digest = str(winner["statement_sha256"])
            self.assertIn(
                winner_digest, {payload["statement_sha256"] for payload in payloads}
            )

            with sqlite3.connect(database) as connection:
                row = connection.execute(
                    """
                    SELECT statement_sha256, statement_content_sha256
                    FROM reconciliation_runs
                    WHERE statement_reference = ?
                    """,
                    (legacy_row["statement_reference"],),
                ).fetchone()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row[0], winner_digest)
            self.assertTrue(row[1])

            winner_payload = next(
                payload
                for payload in payloads
                if payload["statement_sha256"] == winner_digest
            )
            replay = CashbackEventStore(database).reconcile_statement(
                {
                    key: value
                    for key, value in winner_payload.items()
                    if key != "legacy_recovery_digest"
                }
            )
            self.assertTrue(replay["idempotent_replay"])
            self.assertNotIn("legacy_digest_backfilled", replay)

    def test_finalization_replay_rejects_changed_evidence_and_receipt_after_restart(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            reference = "EI-2026-11"
            statement_sha256 = statement_digest(reference)
            store = CashbackEventStore(database)
            store.reconcile_statement(
                {
                    "statement_reference": reference,
                    "statement_sha256": statement_sha256,
                    "card_code": "EI_AMAZON",
                    "period_start": "2026-11-01",
                    "period_end": "2026-11-30",
                    "transactions": [],
                }
            )
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
            with self.assertRaisesRegex(
                ValueError, "different content, digest, or evidence"
            ):
                restarted.finalize_period(
                    {**payload, "statement_evidence_reference": "sha256:evidence-2"}
                )
            with self.assertRaisesRegex(ValueError, "Actual import receipt digest"):
                restarted.finalize_period(
                    {
                        **payload,
                        "actual_import_receipt_sha256": statement_digest("other"),
                    }
                )

    def test_finalization_can_digest_an_independent_actual_receipt_object(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            reference = "EI-2026-11-object"
            statement_sha256 = statement_digest(reference)
            store.reconcile_statement(
                {
                    "statement_reference": reference,
                    "statement_sha256": statement_sha256,
                    "card_code": "EI_AMAZON",
                    "period_start": "2026-11-01",
                    "period_end": "2026-11-30",
                    "transactions": [],
                }
            )
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
            result = store.finalize_period(
                {
                    "statement_reference": reference,
                    "statement_sha256": statement_sha256,
                    "statement_evidence_reference": "sha256:evidence-object",
                    "statement_document_url": "Finance Evidence/ei-object.pdf",
                    "actual_import_receipt": receipt,
                    "actual_import_receipt_sha256": receipt_sha256,
                }
            )
            self.assertEqual(result["actual_import_receipt_sha256"], receipt_sha256)

    def test_finalization_fault_rolls_back_and_retry_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            reference = "EI-2026-12"
            statement_sha256 = statement_digest(reference)
            store = CashbackEventStore(database)
            store.reconcile_statement(
                {
                    "statement_reference": reference,
                    "statement_sha256": statement_sha256,
                    "card_code": "EI_AMAZON",
                    "period_start": "2026-12-01",
                    "period_end": "2026-12-31",
                    "transactions": [],
                }
            )
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
            with self.assertRaisesRegex(
                sqlite3.IntegrityError, "synthetic finalization fault"
            ):
                store.finalize_period(payload)
            restarted = CashbackEventStore(database)
            self.assertNotIn(
                "FINALIZED", {row["status"] for row in restarted.period_rows()}
            )
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
                    row[1]
                    for row in connection.execute("PRAGMA table_info(card_periods)")
                }
                run_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(reconciliation_runs)"
                    )
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
            self.assertTrue(
                {"statement_sha256", "statement_content_sha256"} <= run_columns
            )

    def test_statement_receipts_close_one_card_and_assign_half_open_boundaries(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.ensure_period(
                {
                    "period_id": "card-a-aug",
                    "card_code": "CARD_A",
                    "period_start": "2026-08-01T00:00:00Z",
                    "period_end": "2026-09-01T00:00:00Z",
                }
            )
            store.ensure_period(
                {
                    "period_id": "card-a-sep",
                    "card_code": "CARD_A",
                    "period_start": "2026-09-01T00:00:00Z",
                    "period_end": "2026-10-01T00:00:00Z",
                }
            )
            store.ensure_period(
                {
                    "period_id": "card-b-aug",
                    "card_code": "CARD_B",
                    "period_start": "2026-08-01T00:00:00Z",
                    "period_end": "2026-09-01T00:00:00Z",
                }
            )
            base = {
                "card_code": "CARD_A",
                "amount_aed": "10",
                "merchant": "Example",
            }
            store.upsert(
                [
                    {
                        **base,
                        "source_event_id": "before",
                        "occurred_at": "2026-08-31T23:59:59Z",
                    },
                    {
                        **base,
                        "source_event_id": "equal",
                        "occurred_at": "2026-09-01T00:00:00Z",
                    },
                    {
                        **base,
                        "source_event_id": "after",
                        "occurred_at": "2026-09-01T00:00:01Z",
                    },
                ]
            )

            self.assertEqual(
                {row["source_event_id"] for row in store.rows_for_period("card-a-aug")},
                {"before"},
            )
            self.assertEqual(
                {row["source_event_id"] for row in store.rows_for_period("card-a-sep")},
                {"equal", "after"},
            )

            receipt = {
                "receipt_id": "receipt-a-aug",
                "source_identity": "mail:a:aug",
                "card_code": "CARD_A",
                "original_received_at": "2026-09-01T00:00:00Z",
                "period_id": "card-a-aug",
            }
            first = store.record_statement_receipt(receipt)
            replay = store.record_statement_receipt(receipt)

            self.assertEqual(first["status"], "CLOSED")
            self.assertFalse(first["idempotent_replay"])
            self.assertTrue(replay["idempotent_replay"])
            self.assertEqual(
                next(
                    row
                    for row in store.period_rows()
                    if row["period_id"] == "card-a-aug"
                )["closed_by_receipt_id"],
                "receipt-a-aug",
            )
            self.assertEqual(
                next(
                    row
                    for row in store.period_rows()
                    if row["period_id"] == "card-a-sep"
                )["status"],
                "OPEN",
            )
            self.assertEqual(
                next(
                    row
                    for row in store.period_rows()
                    if row["period_id"] == "card-b-aug"
                )["status"],
                "OPEN",
            )

            same_source_other_card = {
                **receipt,
                "receipt_id": "receipt-b-aug",
                "card_code": "CARD_B",
                "period_id": "card-b-aug",
            }
            other_card = store.record_statement_receipt(same_source_other_card)
            self.assertEqual(other_card["status"], "CLOSED")
            self.assertFalse(other_card["idempotent_replay"])

    def test_delayed_receipts_close_out_of_order_and_date_passage_is_inert(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            for period_id, start, end in (
                ("period-one", "2026-08-01T00:00:00Z", "2026-09-01T00:00:00Z"),
                ("period-two", "2026-09-01T00:00:00Z", "2026-10-01T00:00:00Z"),
            ):
                store.ensure_period(
                    {
                        "period_id": period_id,
                        "card_code": "CARD_A",
                        "period_start": start,
                        "period_end": end,
                    }
                )

            newest = store.record_statement_receipt(
                {
                    "receipt_id": "receipt-two",
                    "source_identity": "mail:a:september",
                    "card_code": "CARD_A",
                    "original_received_at": "2026-10-01T00:00:00Z",
                    "period_id": "period-two",
                }
            )
            delayed = store.record_statement_receipt(
                {
                    "receipt_id": "receipt-one",
                    "source_identity": "mail:a:august",
                    "card_code": "CARD_A",
                    "original_received_at": "2026-09-01T00:00:00Z",
                    "period_id": "period-one",
                }
            )

            self.assertEqual(newest["status"], "CLOSED")
            self.assertEqual(delayed["status"], "CLOSED")
            self.assertEqual(
                {
                    row["period_id"]
                    for row in store.period_rows()
                    if row["status"] == "CLOSED"
                },
                {"period-one", "period-two"},
            )
            with self.assertRaisesRegex(
                ValueError, "cashback period is already closed by another receipt"
            ):
                store.record_statement_receipt(
                    {
                        "receipt_id": "receipt-one-late",
                        "source_identity": "mail:a:august-late",
                        "card_code": "CARD_A",
                        "original_received_at": "2026-09-01T08:00:00Z",
                        "period_id": "period-one",
                    }
                )
            self.assertEqual(len(store.receipt_rows("CARD_A")), 2)

    def test_receipt_without_period_preserves_incomplete_coverage_and_rejects_purchases(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            waiting = store.record_statement_receipt(
                {
                    "receipt_id": "receipt-unbounded",
                    "source_identity": "mail:unknown-period",
                    "card_code": "CARD_A",
                    "original_received_at": "2026-09-02T08:00:00Z",
                }
            )
            self.assertEqual(waiting["status"], "WAITING_STATEMENT")
            self.assertEqual(len(store.receipt_rows("CARD_A")), 1)
            self.assertEqual(store.period_rows(), [])

            with self.assertRaisesRegex(
                ValueError, "cannot contain statement purchases"
            ):
                store.record_statement_receipt(
                    {
                        "receipt_id": "receipt-with-purchases",
                        "source_identity": "mail:with-purchases",
                        "card_code": "CARD_A",
                        "original_received_at": "2026-09-03T08:00:00Z",
                        "transactions": [],
                    }
                )

    def test_waiting_receipt_attaches_after_period_appears_and_replays_idempotently(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            receipt = {
                "receipt_id": "receipt-waiting-inferred",
                "source_identity": "mail:waiting-inferred",
                "card_code": "CARD_A",
                "original_received_at": "2026-09-01T00:00:00Z",
            }
            store.upsert(
                [
                    {
                        "source_event_id": "waiting-boundary-event",
                        "occurred_at": "2026-08-31T23:59:59Z",
                        "card_code": "CARD_A",
                        "amount_aed": "10",
                        "merchant": "Example",
                    }
                ]
            )
            waiting = store.record_statement_receipt(receipt)
            self.assertEqual(waiting["status"], "WAITING_STATEMENT")

            store.ensure_period(
                {
                    "period_id": "waiting-inferred-period",
                    "card_code": "CARD_A",
                    "period_start": "2026-08-01T00:00:00Z",
                    "period_end": "2026-09-01T00:00:00Z",
                }
            )
            attached = store.record_statement_receipt(receipt)
            replay = store.record_statement_receipt(receipt)

            self.assertEqual(attached["status"], "CLOSED")
            self.assertTrue(attached["idempotent_replay"])
            self.assertEqual(replay, attached)
            self.assertEqual(
                store.receipt_rows("CARD_A")[0]["period_id"],
                "waiting-inferred-period",
            )
            self.assertEqual(
                {
                    row["source_event_id"]
                    for row in store.rows_for_period("waiting-inferred-period")
                },
                {"waiting-boundary-event"},
            )
            period = store.period_rows()[0]
            self.assertEqual(period["status"], "CLOSED")
            self.assertEqual(period["closed_by_receipt_id"], receipt["receipt_id"])

    def test_waiting_receipt_accepts_compatible_period_enrichment_and_rejects_conflicts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            receipt = {
                "receipt_id": "receipt-waiting-supplied",
                "source_identity": "mail:waiting-supplied",
                "card_code": "CARD_A",
                "original_received_at": "2026-10-01T00:00:00Z",
            }
            store.record_statement_receipt(receipt)
            period = {
                "period_id": "waiting-supplied-period",
                "card_code": "CARD_A",
                "period_start": "2026-09-01T00:00:00Z",
                "period_end": "2026-10-01T00:00:00Z",
            }
            store.ensure_period(period)

            enriched = store.record_statement_receipt(receipt, period=period)
            replay = store.record_statement_receipt(receipt, period=period)
            self.assertEqual(enriched["status"], "CLOSED")
            self.assertTrue(enriched["idempotent_replay"])
            self.assertEqual(replay, enriched)

            with self.assertRaisesRegex(
                ValueError, "identity was already used for different content"
            ):
                store.record_statement_receipt(
                    {**receipt, "source_identity": "mail:changed"},
                    period=period,
                )
            with self.assertRaisesRegex(
                ValueError, "period_id does not match supplied period bounds"
            ):
                store.record_statement_receipt(
                    receipt,
                    period={
                        **period,
                        "period_end": "2026-10-02T00:00:00Z",
                    },
                )
            stored_receipt = store.receipt_rows("CARD_A")[0]
            self.assertEqual(stored_receipt["period_id"], period["period_id"])
            self.assertEqual(store.period_rows()[0]["status"], "CLOSED")

    def test_statement_receipt_close_is_atomic_across_receipt_and_period_rows(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            store = CashbackEventStore(database)
            store.ensure_period(
                {
                    "period_id": "atomic-period",
                    "card_code": "CARD_A",
                    "period_start": "2026-08-01T00:00:00Z",
                    "period_end": "2026-09-01T00:00:00Z",
                }
            )
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    CREATE TRIGGER fail_statement_close
                    BEFORE UPDATE OF status ON card_periods
                    WHEN NEW.status = 'CLOSED'
                    BEGIN
                        SELECT RAISE(ABORT, 'synthetic receipt close fault');
                    END
                    """
                )
            receipt = {
                "receipt_id": "receipt-atomic",
                "source_identity": "mail:atomic",
                "card_code": "CARD_A",
                "original_received_at": "2026-09-01T00:00:00Z",
                "period_id": "atomic-period",
            }
            with self.assertRaisesRegex(
                sqlite3.IntegrityError, "synthetic receipt close fault"
            ):
                store.record_statement_receipt(receipt)

            restarted = CashbackEventStore(database)
            self.assertEqual(restarted.receipt_rows(), [])
            self.assertEqual(restarted.period_rows()[0]["status"], "OPEN")
            with sqlite3.connect(database) as connection:
                connection.execute("DROP TRIGGER fail_statement_close")
            self.assertEqual(
                restarted.record_statement_receipt(receipt)["status"], "CLOSED"
            )

    def test_finalize_period_accepts_statement_receipt_without_actual_or_reconciliation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            result = store.finalize_period(
                {
                    "receipt_id": "receipt-finalize",
                    "source_identity": "mail:finalize",
                    "card_code": "CARD_A",
                    "original_received_at": "2026-09-01T00:00:00Z",
                    "period_id": "period-finalize",
                    "period_start": "2026-08-01T00:00:00Z",
                    "period_end": "2026-09-01T00:00:00Z",
                }
            )
            self.assertEqual(result["status"], "CLOSED")
            self.assertEqual(result["period_id"], "period-finalize")
            self.assertFalse(result["idempotent_replay"])

    def test_competing_receipt_cannot_mutate_closed_period(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.ensure_period(
                {
                    "period_id": "closed-period",
                    "card_code": "CARD_A",
                    "period_start": "2026-08-01T00:00:00Z",
                    "period_end": "2026-09-01T00:00:00Z",
                }
            )
            first = {
                "receipt_id": "receipt-first",
                "source_identity": "mail:first",
                "card_code": "CARD_A",
                "original_received_at": "2026-09-01T00:00:00Z",
                "period_id": "closed-period",
            }
            self.assertEqual(store.record_statement_receipt(first)["status"], "CLOSED")
            with self.assertRaisesRegex(
                ValueError, "cashback period is already closed by another receipt"
            ):
                store.record_statement_receipt(
                    {
                        **first,
                        "receipt_id": "receipt-second",
                        "source_identity": "mail:second",
                    }
                )
            period = store.period_rows()[0]
            self.assertEqual(period["closed_by_receipt_id"], "receipt-first")
            self.assertEqual(len(store.receipt_rows("CARD_A")), 1)
            self.assertTrue(store.record_statement_receipt(first)["idempotent_replay"])

    def test_concurrent_receipt_closures_are_serialized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            initializer = CashbackEventStore(database)
            initializer.ensure_period(
                {
                    "period_id": "concurrent-period",
                    "card_code": "CARD_A",
                    "period_start": "2026-08-01T00:00:00Z",
                    "period_end": "2026-09-01T00:00:00Z",
                }
            )
            store_one = CashbackEventStore(database)
            store_two = CashbackEventStore(database)
            start = threading.Barrier(2)
            receipts = (
                {
                    "receipt_id": "receipt-concurrent-one",
                    "source_identity": "mail:concurrent-one",
                    "card_code": "CARD_A",
                    "original_received_at": "2026-09-01T00:00:00Z",
                    "period_id": "concurrent-period",
                },
                {
                    "receipt_id": "receipt-concurrent-two",
                    "source_identity": "mail:concurrent-two",
                    "card_code": "CARD_A",
                    "original_received_at": "2026-09-01T00:00:00Z",
                    "period_id": "concurrent-period",
                },
            )

            def close_receipt(
                store: CashbackEventStore, receipt: dict[str, str]
            ) -> tuple[str, Any]:
                start.wait()
                try:
                    return "ok", store.record_statement_receipt(receipt)
                except ValueError as error:
                    return "error", str(error)

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(
                    executor.map(
                        close_receipt,
                        (store_one, store_two),
                        receipts,
                    )
                )

            successful = [result for kind, result in outcomes if kind == "ok"]
            failures = [result for kind, result in outcomes if kind == "error"]
            self.assertEqual(len(successful), 1)
            self.assertEqual(len(failures), 1)
            self.assertIn(
                "cashback period is already closed by another receipt", failures[0]
            )
            winner = successful[0]
            winner_id = winner["receipt_id"]
            self.assertEqual(
                store_one.period_rows()[0]["closed_by_receipt_id"],
                winner_id,
            )
            self.assertEqual(
                [row["receipt_id"] for row in store_one.receipt_rows("CARD_A")],
                [winner_id],
            )
            winner_receipt = next(
                receipt for receipt in receipts if receipt["receipt_id"] == winner_id
            )
            self.assertTrue(
                store_one.record_statement_receipt(winner_receipt)["idempotent_replay"]
            )

    def test_concurrent_overlapping_periods_are_serialized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            store_one = CashbackEventStore(database)
            store_two = CashbackEventStore(database)
            start = threading.Barrier(2)
            periods = (
                {
                    "period_id": "concurrent-period-one",
                    "card_code": "CARD_A",
                    "period_start": "2026-08-01T00:00:00Z",
                    "period_end": "2026-08-01T01:00:00Z",
                },
                {
                    "period_id": "concurrent-period-two",
                    "card_code": "CARD_A",
                    "period_start": "2026-08-01T00:30:00Z",
                    "period_end": "2026-08-01T01:30:00Z",
                },
            )

            def create_period(
                store: CashbackEventStore, period: dict[str, str]
            ) -> tuple[str, Any]:
                start.wait()
                try:
                    return "ok", store.ensure_period(period)
                except ValueError as error:
                    return "error", str(error)

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(
                    executor.map(
                        create_period,
                        (store_one, store_two),
                        periods,
                    )
                )

            self.assertEqual(sum(kind == "ok" for kind, _ in outcomes), 1)
            failures = [result for kind, result in outcomes if kind == "error"]
            self.assertEqual(len(failures), 1)
            self.assertIn("cashback periods overlap", failures[0])
            self.assertEqual(len(store_one.period_rows()), 1)

    def test_second_and_fractional_period_bounds_sort_chronologically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            first = store.ensure_period(
                {
                    "period_id": "whole-second-period",
                    "card_code": "CARD_A",
                    "period_start": "2026-08-01T00:00:00Z",
                    "period_end": "2026-08-01T01:00:00Z",
                }
            )
            adjacent = store.ensure_period(
                {
                    "period_id": "fractional-adjacent-period",
                    "card_code": "CARD_A",
                    "period_start": "2026-08-01T01:00:00.000001Z",
                    "period_end": "2026-08-01T02:00:00Z",
                }
            )
            self.assertEqual(first["period_start"], "2026-08-01T00:00:00.000000Z")
            self.assertEqual(first["period_end"], "2026-08-01T01:00:00.000000Z")
            self.assertEqual(adjacent["period_start"], "2026-08-01T01:00:00.000001Z")
            with self.assertRaisesRegex(ValueError, "periods overlap"):
                store.ensure_period(
                    {
                        "period_id": "fractional-overlap-period",
                        "card_code": "CARD_A",
                        "period_start": "2026-08-01T00:59:59.999999Z",
                        "period_end": "2026-08-01T01:00:00.000001Z",
                    }
                )

    def test_period_id_bounds_and_overlapping_periods_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.ensure_period(
                {
                    "period_id": "period-one",
                    "card_code": "CARD_A",
                    "period_start": "2026-08-01T00:00:00Z",
                    "period_end": "2026-09-01T00:00:00Z",
                }
            )
            with self.assertRaisesRegex(ValueError, "periods overlap"):
                store.ensure_period(
                    {
                        "period_id": "overlap",
                        "card_code": "CARD_A",
                        "period_start": "2026-08-15T00:00:00Z",
                        "period_end": "2026-09-15T00:00:00Z",
                    }
                )
            with self.assertRaisesRegex(
                ValueError, "period_id does not match supplied period bounds"
            ):
                store.record_statement_receipt(
                    {
                        "receipt_id": "receipt-mismatch",
                        "source_identity": "mail:mismatch",
                        "card_code": "CARD_A",
                        "original_received_at": "2026-10-01T00:00:00Z",
                        "period_id": "period-one",
                        "period_start": "2026-09-01T00:00:00Z",
                        "period_end": "2026-10-01T00:00:00Z",
                    }
                )

    def test_equivalent_period_instants_use_one_canonical_utc_spelling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            first = store.ensure_period(
                {
                    "card_code": "CARD_A",
                    "period_start": "2026-08-01T00:00:00Z",
                    "period_end": "2026-09-01T00:00:00Z",
                }
            )
            replay = store.ensure_period(
                {
                    "card_code": "CARD_A",
                    "period_start": "2026-08-01T04:00:00+04:00",
                    "period_end": "2026-09-01T04:00:00+04:00",
                }
            )
            self.assertEqual(replay["period_id"], first["period_id"])
            self.assertEqual(replay["period_start"], "2026-08-01T00:00:00.000000Z")
            self.assertEqual(replay["period_end"], "2026-09-01T00:00:00.000000Z")
            self.assertEqual(len(store.period_rows()), 1)
            assignment = store.upsert(
                [
                    {
                        "source_event_id": "offset-event",
                        "occurred_at": "2026-08-01T05:00:00+04:00",
                        "card_code": "CARD_A",
                        "amount_aed": "1",
                        "merchant": "Example",
                    }
                ]
            )
            self.assertEqual(assignment["inserted"], 1)
            self.assertEqual(
                [
                    row["source_event_id"]
                    for row in store.rows_for_period(first["period_id"])
                ],
                ["offset-event"],
            )

            store.ensure_period(
                {
                    "period_id": "offset-receipt-period",
                    "card_code": "CARD_B",
                    "period_start": "2026-08-01T04:00:00+04:00",
                    "period_end": "2026-09-01T04:00:00+04:00",
                }
            )
            closed = store.record_statement_receipt(
                {
                    "receipt_id": "offset-receipt",
                    "source_identity": "mail:offset-receipt",
                    "card_code": "CARD_B",
                    "original_received_at": "2026-09-01T04:00:00+04:00",
                }
            )
            self.assertEqual(closed["status"], "CLOSED")

    def test_offset_spelled_overlap_cannot_evade_period_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.ensure_period(
                {
                    "period_id": "period-offset",
                    "card_code": "CARD_A",
                    "period_start": "2026-08-01T05:00:00+04:00",
                    "period_end": "2026-08-01T07:00:00+04:00",
                }
            )
            with self.assertRaisesRegex(ValueError, "periods overlap"):
                store.ensure_period(
                    {
                        "period_id": "period-overlap",
                        "card_code": "CARD_A",
                        "period_start": "2026-08-01T02:00:00Z",
                        "period_end": "2026-08-01T04:00:00Z",
                    }
                )

    def test_period_bounds_reject_naive_datetimes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            with self.assertRaisesRegex(
                ValueError, "period_start must include a UTC offset"
            ):
                store.ensure_period(
                    {
                        "card_code": "CARD_A",
                        "period_start": "2026-08-01T00:00:00",
                        "period_end": "2026-09-01T00:00:00",
                    }
                )

    def test_legacy_inclusive_period_migrates_to_half_open_and_keeps_final_day(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            store = CashbackEventStore(database)
            store.ensure_period(
                {
                    "period_id": "legacy-period-id",
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-08-01T00:00:00Z",
                    "period_end": "2026-09-01T00:00:00Z",
                }
            )
            store.upsert(
                [
                    {
                        "source_event_id": "legacy-final-day",
                        "occurred_at": "2026-08-31T23:59:59+04:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "25",
                        "merchant": "Legacy Final Day",
                    }
                ]
            )
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    UPDATE card_periods
                    SET period_start = '2026-08-01', period_end = '2026-08-31'
                    WHERE period_id = 'legacy-period-id'
                    """
                )
            migrated = CashbackEventStore(database)
            period = migrated.period_rows()[0]
            self.assertEqual(period["period_id"], "legacy-period-id")
            self.assertEqual(period["period_start"], "2026-08-01T00:00:00.000000Z")
            self.assertEqual(period["period_end"], "2026-09-01T00:00:00.000000Z")
            self.assertEqual(
                [
                    row["source_event_id"]
                    for row in migrated.rows_for_period("legacy-period-id")
                ],
                ["legacy-final-day"],
            )

    def test_finalize_period_opens_readable_half_open_period_in_same_process(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "events.sqlite3"
            configuration = json.loads(
                Path("config/cashback-programs.json").read_text(encoding="utf-8")
            )
            for program in configuration["programs"]:
                if program["card"] == "RAK_WORLD":
                    program["statement_cycle"]["close_day"] = 15
            config_path = root / "cashback-programs.json"
            config_path.write_text(json.dumps(configuration), encoding="utf-8")
            store = CashbackEventStore(database)
            reference = "RAK-2026-08-15"
            store.reconcile_statement(
                {
                    "statement_reference": reference,
                    "statement_sha256": statement_digest(reference),
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-07-16",
                    "period_end": "2026-08-15",
                    "transactions": [],
                }
            )
            receipt = actual_receipt(
                reference,
                "2026-07-16",
                "2026-08-15",
                account_id="RAK_WORLD",
            )
            store.finalize_period(
                {
                    "statement_reference": reference,
                    "statement_sha256": statement_digest(reference),
                    "statement_evidence_reference": "sha256:test",
                    "statement_document_url": "https://evidence.example/rak.pdf",
                    "actual_import_receipt": receipt,
                    "actual_import_receipt_sha256": actual_receipt_digest(receipt),
                },
                program_config_path=config_path,
            )
            open_period = next(
                row for row in store.period_rows() if row["status"] == "OPEN"
            )
            self.assertEqual(
                open_period["period_id"],
                "cashback-period:RAK_WORLD:2026-08-16T00:00:00.000000Z:2026-09-16T00:00:00.000000Z",
            )
            self.assertEqual(open_period["period_start"], "2026-08-16T00:00:00.000000Z")
            self.assertEqual(open_period["period_end"], "2026-09-16T00:00:00.000000Z")
            dashboard = build_live_dashboard(
                store,
                date(2026, 8, 20),
                program_config_path=config_path,
            )
            self.assertIn("RAK_WORLD", {card["card"] for card in dashboard["cards"]})

    def test_legacy_offset_spelled_reconciliation_replays_but_changed_content_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            store = CashbackEventStore(database)
            payload = {
                "statement_reference": "legacy-offset-replay",
                "statement_sha256": statement_digest("legacy-offset-replay"),
                "card_code": "EI_AMAZON",
                "period_start": "2026-09-01",
                "period_end": "2026-09-30",
                "transactions": [
                    {
                        "statement_transaction_id": "line-1",
                        "occurred_at": "2026-09-10T12:30:00+04:00",
                        "amount_aed": "20",
                        "merchant": "Example",
                    }
                ],
            }
            first = store.reconcile_statement(payload)
            self.assertFalse(first["idempotent_replay"])
            period_start = date(2026, 9, 1)
            period_end = date(2026, 9, 30)
            events, transaction_ids = _canonical_statement_events(
                payload,
                statement_reference=payload["statement_reference"],
                card_code=payload["card_code"],
                period_start=period_start,
                period_end=period_end,
            )
            legacy_digest = _statement_content_digest(
                [
                    {
                        **event,
                        "occurred_at": payload["transactions"][0]["occurred_at"],
                    }
                    for event in events
                ],
                transaction_ids,
                statement_reference=payload["statement_reference"],
                card_code=payload["card_code"],
                period_start=period_start,
                period_end=period_end,
            )
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    UPDATE reconciliation_runs
                    SET statement_content_sha256 = ?
                    WHERE statement_reference = ?
                    """,
                    (legacy_digest, payload["statement_reference"]),
                )
            replay = store.reconcile_statement(payload)
            self.assertTrue(replay["idempotent_replay"])
            changed = {
                **payload,
                "transactions": [{**payload["transactions"][0], "amount_aed": "21"}],
            }
            with self.assertRaisesRegex(
                ValueError, "different statement content or digest"
            ):
                store.reconcile_statement(changed)

    def test_metadata_correction_preserves_migrated_collision_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            event = {
                "source_event_id": "legacy-correction:1",
                "occurred_at": "2026-08-16T12:30:00+04:00",
                "card_code": "RAK_WORLD",
                "amount_aed": "25.50",
                "currency": "AED",
                "purchase_type": "GROCERY",
                "channel": "PHYSICAL_POS",
                "merchant": "Carrefour Market",
            }
            CashbackEventStore(database).upsert([event])
            with sqlite3.connect(database) as connection:
                columns = [
                    row[1]
                    for row in connection.execute("PRAGMA table_info(cashback_events)")
                ]
                values = list(
                    connection.execute(
                        "SELECT * FROM cashback_events WHERE source_event_id = ?",
                        (event["source_event_id"],),
                    ).fetchone()
                )
                source_index = columns.index("source_event_id")
                identity_index = columns.index("identity_key")
                values[source_index] = "legacy-correction:2"
                values[identity_index] = None
                connection.execute(
                    "UPDATE cashback_events SET identity_key = NULL WHERE source_event_id = ?",
                    (event["source_event_id"],),
                )
                connection.execute(
                    f"INSERT INTO cashback_events ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                    values,
                )
            store = CashbackEventStore(database)
            before = next(
                row
                for row in store.rows(date(2026, 8, 1), date(2026, 8, 31))
                if row["source_event_id"] == "legacy-correction:2"
            )
            correction = store.correct_event(
                {
                    "correction_id": "legacy-correction-meta",
                    "source_event_id": "legacy-correction:2",
                    "source": "manual-review",
                    "reason": "Assign review bucket",
                    "changes": {
                        "bucket_code": "RAK_GROCERY",
                        "review_required": False,
                        "status": "IGNORED",
                    },
                }
            )
            self.assertFalse(correction["idempotent_replay"])
            with sqlite3.connect(database) as connection:
                connection.row_factory = sqlite3.Row
                after = dict(
                    connection.execute(
                        "SELECT * FROM cashback_events WHERE source_event_id = ?",
                        ("legacy-correction:2",),
                    ).fetchone()
                )
            self.assertEqual(after["identity_key"], before["identity_key"])
            self.assertEqual(after["bucket_code"], "RAK_GROCERY")
            self.assertEqual(after["status"], "IGNORED")
            store.correct_event(
                {
                    "correction_id": "legacy-correction-economic",
                    "source_event_id": "legacy-correction:2",
                    "source": "manual-review",
                    "reason": "Economic correction",
                    "changes": {"amount_aed": "30"},
                }
            )
            with self.assertRaisesRegex(ValueError, "correction would collide"):
                store.correct_event(
                    {
                        "correction_id": "legacy-correction-collision",
                        "source_event_id": "legacy-correction:2",
                        "source": "manual-review",
                        "reason": "Restore duplicate economics",
                        "changes": {"amount_aed": "25.50"},
                    }
                )

    def test_bank_receipt_is_independent_and_matches_inclusive_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            store = CashbackEventStore(database)
            store.ensure_period(
                {
                    "period_id": "rak-aug-half-open",
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-08-06T00:00:00Z",
                    "period_end": "2026-09-06T00:00:00Z",
                }
            )
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    UPDATE card_periods
                    SET status = 'FINALIZED', finalized_at = ?
                    WHERE period_id = ?
                    """,
                    ("2026-09-07T00:00:00+00:00", "rak-aug-half-open"),
                )
            payload = {
                "source": "outlook",
                "source_message_id": "bank-message-1",
                "received_at": "2026-09-06T00:00:00Z",
                "card_code": "RAK_WORLD",
                "period_start": "2026-08-06",
                "period_end": "2026-09-05",
            }
            before = store.period_rows()[0]
            first = store.record_statement_receipt(payload)
            self.assertEqual(
                first["statement_receipt"]["settlement_state"], "FINALIZED"
            )
            self.assertFalse(first["idempotent_replay"])
            updated = store.update_statement_receipt(
                first["receipt_id"],
                processing_state="PARSED",
                reconciliation_state="RECONCILED",
            )
            self.assertEqual(updated["state"], "BANK_CLOSED")
            self.assertEqual(updated["processing_state"], "PARSED")
            self.assertEqual(updated["reconciliation_state"], "RECONCILED")
            self.assertEqual(store.period_rows()[0]["status"], before["status"])
            replay = store.record_statement_receipt(payload)
            self.assertTrue(replay["idempotent_replay"])
            self.assertEqual(replay["receipt_id"], first["receipt_id"])

    def test_bank_finalized_match_rejects_non_midnight_period_end(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            store = CashbackEventStore(database)
            store.ensure_period(
                {
                    "period_id": "rak-noon-end",
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-08-06T00:00:00Z",
                    "period_end": "2026-09-06T12:00:00Z",
                }
            )
            store.ensure_period(
                {
                    "period_id": "card-midnight-end",
                    "card_code": "CARD_A",
                    "period_start": "2026-08-01T00:00:00Z",
                    "period_end": "2026-09-01T00:00:00Z",
                }
            )
            with sqlite3.connect(database) as connection:
                connection.execute("UPDATE card_periods SET status = 'FINALIZED'")

            def receipt(
                message_id: str,
                card_code: str,
                period_start: str,
                period_end: str,
            ) -> dict[str, object]:
                return {
                    "source": "outlook",
                    "source_message_id": message_id,
                    "received_at": "2026-09-07T00:00:00Z",
                    "card_code": card_code,
                    "period_start": period_start,
                    "period_end": period_end,
                }

            non_midnight_previous_day = store.record_bank_statement_receipt(
                receipt(
                    "bank-noon-previous",
                    "RAK_WORLD",
                    "2026-08-06",
                    "2026-09-05",
                )
            )
            non_midnight_same_day = store.record_bank_statement_receipt(
                receipt(
                    "bank-noon-same",
                    "RAK_WORLD",
                    "2026-08-06",
                    "2026-09-06",
                )
            )
            exact_midnight = store.record_bank_statement_receipt(
                receipt(
                    "bank-midnight-exact",
                    "CARD_A",
                    "2026-08-01",
                    "2026-08-31",
                )
            )
            self.assertEqual(
                non_midnight_previous_day["statement_receipt"]["settlement_state"],
                "UNFINALIZED",
            )
            self.assertEqual(
                non_midnight_same_day["statement_receipt"]["settlement_state"],
                "UNFINALIZED",
            )
            self.assertEqual(
                exact_midnight["statement_receipt"]["settlement_state"],
                "FINALIZED",
            )

    def test_bank_receipt_replay_is_serialized_under_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            payload = {
                "source": "outlook",
                "source_message_id": "bank-message-concurrent",
                "received_at": "2026-09-06T00:00:00Z",
                "card_code": "RAK_WORLD",
            }
            with ThreadPoolExecutor(max_workers=8) as workers:
                results = list(
                    workers.map(
                        lambda _: store.record_bank_statement_receipt(payload),
                        range(8),
                    )
                )
            self.assertEqual(
                sum(not result["idempotent_replay"] for result in results),
                1,
            )
            self.assertEqual(
                sum(result["idempotent_replay"] for result in results),
                7,
            )
            self.assertEqual(len(store.statement_receipts()), 1)


if __name__ == "__main__":
    unittest.main()
