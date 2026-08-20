from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from finance_tracker.cashback_events import CashbackEventStore


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
