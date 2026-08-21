from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import date
from pathlib import Path

from finance_tracker.cashback_events import (
    CashbackEventStore,
    _canonical_statement_events,
    _rank_statement_candidates,
)


class CashbackStatementMatchingTests(unittest.TestCase):
    def _store(self) -> tuple[tempfile.TemporaryDirectory[str], CashbackEventStore]:
        temporary = tempfile.TemporaryDirectory()
        return temporary, CashbackEventStore(Path(temporary.name) / "events.sqlite3")

    def _statement(
        self,
        *,
        merchant: str,
        currency: str = "AED",
        occurred_at: str = "2026-08-10T12:00:00+04:00",
    ) -> dict[str, object]:
        return {
            "statement_reference": "RAK-2026-08",
            "statement_sha256": hashlib.sha256(
                f"{merchant}|{currency}|{occurred_at}".encode()
            ).hexdigest(),
            "card_code": "RAK_WORLD",
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
            "transactions": [
                {
                    "statement_transaction_id": "line-1",
                    "occurred_at": occurred_at,
                    "amount_aed": "100",
                    "currency": currency,
                    "merchant": merchant,
                    "event_type": "PURCHASE",
                }
            ],
        }

    def _notification(
        self,
        *,
        source_event_id: str = "notification-1",
        merchant: str = "Expected Merchant",
        currency: str = "AED",
        occurred_at: str = "2026-08-10T12:00:00+04:00",
    ) -> dict[str, object]:
        return {
            "source_event_id": source_event_id,
            "occurred_at": occurred_at,
            "card_code": "RAK_WORLD",
            "amount_aed": "100",
            "currency": currency,
            "merchant": merchant,
            "event_type": "PURCHASE",
        }

    def test_canonical_statement_events_preserve_normalized_payload_contract(self) -> None:
        statement = self._statement(merchant="Expected Merchant")

        events, transaction_ids = _canonical_statement_events(
            statement,
            statement_reference="RAK-2026-08",
            card_code="RAK_WORLD",
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
        )

        self.assertEqual(transaction_ids, ["line-1"])
        self.assertEqual(events[0]["source_event_id"], "statement:RAK-2026-08:line-1")
        self.assertEqual(events[0]["card_code"], "RAK_WORLD")
        self.assertEqual(events[0]["source"], "statement")
        self.assertEqual(events[0]["reconciliation_status"], "RECONCILED")

    def test_candidate_ranking_preserves_amount_currency_polarity_and_order(self) -> None:
        event = {
            "occurred_at": "2026-08-10T12:00:00+04:00",
            "amount_aed_minor": 10000,
            "currency": "AED",
            "event_type": "PURCHASE",
            "merchant": "Expected Merchant",
        }
        candidates = [
            {
                **event,
                "source_event_id": "exact-near",
                "occurred_at": "2026-08-11T12:00:00+04:00",
            },
            {
                **event,
                "source_event_id": "alias-same-day",
                "merchant": "Expected Merchant LLC",
            },
            {**event, "source_event_id": "wrong-currency", "currency": "USD"},
            {**event, "source_event_id": "wrong-amount", "amount_aed_minor": 10001},
            {**event, "source_event_id": "wrong-polarity", "event_type": "REFUND"},
            {
                **event,
                "source_event_id": "outside-window",
                "occurred_at": "2026-08-14T12:00:00+04:00",
            },
        ]

        ranked = _rank_statement_candidates(event, candidates)

        self.assertEqual(
            ranked,
            [(2, -1, "exact-near"), (1, 0, "alias-same-day")],
        )

    def test_unrelated_merchant_is_statement_only_and_notification_is_variance(
        self,
    ) -> None:
        temporary, store = self._store()
        with temporary:
            store.upsert([self._notification(merchant="Different Merchant")])

            result = store.reconcile_statement(
                self._statement(merchant="Expected Merchant")
            )

            self.assertEqual(result["matched"], 0)
            self.assertEqual(result["statement_only"], 1)
            self.assertEqual(result["notification_only"], 1)
            self.assertEqual(store.stats()["variance_count"], 1)

    def test_currency_mismatch_is_not_an_authoritative_match(self) -> None:
        temporary, store = self._store()
        with temporary:
            store.upsert([self._notification(currency="USD")])

            result = store.reconcile_statement(
                self._statement(merchant="Expected Merchant")
            )

            self.assertEqual(result["matched"], 0)
            self.assertEqual(result["statement_only"], 1)
            self.assertEqual(result["notification_only"], 1)
            self.assertEqual(store.stats()["variance_count"], 1)

    def test_normalized_merchant_alias_remains_a_match(self) -> None:
        temporary, store = self._store()
        with temporary:
            store.upsert([self._notification(merchant="Carrefour Market")])

            result = store.reconcile_statement(
                self._statement(merchant="CARREFOUR MARKET LLC")
            )

            self.assertEqual(result["matched"], 1)
            self.assertEqual(result["statement_only"], 0)
            self.assertEqual(result["notification_only"], 0)

    def test_equal_candidates_with_same_merchant_and_day_remain_ambiguous(self) -> None:
        temporary, store = self._store()
        with temporary:
            store.upsert([
                self._notification(
                    source_event_id="notification-1",
                    merchant="Expected Merchant",
                    occurred_at="2026-08-10T11:00:00+04:00",
                ),
                self._notification(
                    source_event_id="notification-2",
                    merchant="Expected Merchant",
                    occurred_at="2026-08-10T13:00:00+04:00",
                ),
            ])

            result = store.reconcile_statement(
                self._statement(
                    merchant="Expected Merchant",
                    occurred_at="2026-08-10T12:00:00+04:00",
                )
            )

            self.assertEqual(result["matched"], 0)
            self.assertEqual(result["statement_only"], 1)
            self.assertEqual(result["notification_only"], 2)
            self.assertEqual(store.stats()["variance_count"], 2)

    def test_same_merchant_candidates_on_different_days_remain_ambiguous(self) -> None:
        temporary, store = self._store()
        with temporary:
            store.upsert([
                self._notification(
                    source_event_id="notification-1",
                    merchant="Expected Merchant",
                    occurred_at="2026-08-10T12:00:00+04:00",
                ),
                self._notification(
                    source_event_id="notification-2",
                    merchant="Expected Merchant",
                    occurred_at="2026-08-11T12:00:00+04:00",
                ),
            ])

            result = store.reconcile_statement(
                self._statement(merchant="Expected Merchant")
            )

            self.assertEqual(result["matched"], 0)
            self.assertEqual(result["statement_only"], 1)
            self.assertEqual(result["notification_only"], 2)
            self.assertEqual(store.stats()["variance_count"], 2)

    def test_exact_and_alias_candidates_remain_ambiguous(self) -> None:
        temporary, store = self._store()
        with temporary:
            store.upsert([
                self._notification(
                    source_event_id="notification-exact",
                    merchant="Expected Merchant",
                ),
                self._notification(
                    source_event_id="notification-alias",
                    merchant="Expected Merchant LLC",
                ),
            ])

            statement = self._statement(merchant="Expected Merchant")
            result = store.reconcile_statement(statement)

            self.assertEqual(result["matched"], 0)
            self.assertEqual(result["statement_only"], 1)
            self.assertEqual(result["notification_only"], 2)
            self.assertEqual(store.stats()["variance_count"], 2)
            statement_event = next(
                row
                for row in store.rows(date(2026, 8, 1), date(2026, 8, 31))
                if row["source_event_id"] == "statement:RAK-2026-08:line-1"
            )
            self.assertEqual(statement_event["source"], "statement")
            self.assertEqual(statement_event["status"], "ACTIVE")
            self.assertEqual(statement_event["reconciliation_status"], "RECONCILED")
            identity_key = statement_event["identity_key"]
            self.assertTrue(identity_key)
            self.assertEqual(store.stats()["event_count"], 3)

            replay = store.reconcile_statement(statement)

            self.assertTrue(replay["idempotent_replay"])
            replay_event = next(
                row
                for row in store.rows(date(2026, 8, 1), date(2026, 8, 31))
                if row["source_event_id"] == "statement:RAK-2026-08:line-1"
            )
            self.assertEqual(replay_event["identity_key"], identity_key)
            self.assertEqual(store.stats()["event_count"], 3)

    def test_exact_reconciliation_replay_is_a_no_op(self) -> None:
        temporary, store = self._store()
        with temporary:
            store.upsert([self._notification()])
            statement = self._statement(merchant="Expected Merchant")

            first = store.reconcile_statement(statement)
            replay = store.reconcile_statement(statement)

            self.assertEqual(first["matched"], 1)
            self.assertTrue(replay["idempotent_replay"])
            self.assertEqual(store.stats()["event_count"], 1)


if __name__ == "__main__":
    unittest.main()
