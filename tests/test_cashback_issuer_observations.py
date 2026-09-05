from datetime import date, datetime
from decimal import Decimal
from unittest import TestCase

from finance_tracker.cashback import configured_programs, evaluate_card, PaymentIntent, reward_total
from finance_tracker.actual_snapshot import cashback_dashboard
from finance_tracker.models import Transaction


class IssuerObservationTests(TestCase):
    def programs(self, day=date(2026, 9, 5)):
        return {p.card: p for p in configured_programs(day)}

    def test_sc_floors_month_aggregate_not_each_bucket(self):
        old = self.programs(date(2026, 9, 4))["SC_PLATINUM_X"]
        current = self.programs()["SC_PLATINUM_X"]
        buckets = {"SC_ONLINE": Decimal("19"), "SC_WALLET": Decimal("19")}
        self.assertEqual(reward_total(old, Decimal("2500"), buckets), Decimal("1.14"))
        self.assertEqual(reward_total(current, Decimal("2500"), buckets), Decimal("1"))
        self.assertEqual(reward_total(current, Decimal("2500"), {"SC_ONLINE": Decimal("19")}), Decimal("0"))

    def test_rak_special_categories_cannot_fall_back_to_retail(self):
        current = self.programs()["RAK_WORLD"]
        for category in ("GROCERY", "DINING", "TRAVEL", "HOTEL", "AIRLINE"):
            for bucket, channel in (("RAK_STANDARD", "PHYSICAL_POS"), ("RAK_EWALLET", "APPLE_PAY_POS")):
                with self.subTest(category=category, bucket=bucket):
                    intent = PaymentIntent(category, Decimal("100"), "AED", channel)
                    self.assertIsNone(evaluate_card(current, [], intent, bucket_code=bucket))
        self.assertIsNotNone(evaluate_card(current, [], PaymentIntent("GENERAL", Decimal("100"), "AED", "ONLINE"), bucket_code="RAK_STANDARD"))

    def test_ei_unknown_is_not_zero_and_spend_is_preserved(self):
        programs = self.programs()
        ei = programs["EI_AMAZON"]
        self.assertEqual(ei.tiers[0].rates["EI_AMAZON"], Decimal("0.06"))
        self.assertIsNone(evaluate_card(ei, [], PaymentIntent("AMAZON", Decimal("100"), "AED", "ONLINE")))
        with self.assertRaisesRegex(ValueError, "eligibility is unverified"):
            reward_total(ei, Decimal("100"), {"EI_AMAZON": Decimal("100")})
        row = Transaction("ei-source", datetime(2026, 9, 5), "EI_AMAZON", "Amazon.ae", "100", category="AMAZON", channel="ONLINE", reward_bucket="EI_AMAZON")
        snapshot = cashback_dashboard(tuple(programs.values()), [row], date(2026, 9, 5), [])
        card = next(c for c in snapshot["cards"] if c["card"] == "EI_AMAZON")
        self.assertIsNone(card["expected_cashback_aed"])
        self.assertFalse(card["reward_eligibility_verified"])
        self.assertEqual(Decimal(card["total_spend_aed"]), Decimal("100"))
        self.assertNotEqual(card["position_mode"], "UNLIMITED")

    def test_historical_version_and_current_default_remain_unambiguous(self):
        old = self.programs(date(2026, 9, 4))
        current = self.programs()
        self.assertEqual(len(configured_programs()), 3)
        self.assertEqual(old["EI_AMAZON"].position_mode, "UNLIMITED")
        self.assertTrue(old["EI_AMAZON"].reward_eligibility_verified)
        for card in old:
            self.assertEqual(old[card].programme_version, "confirmed-2026-08-v1")
            self.assertEqual(current[card].programme_version, "observed-2026-09-05-v1")
            self.assertEqual(current[card].provenance_authority, "NON_AUTHORITATIVE")

    def test_current_reestimate_does_not_change_finalized_statement_evidence(self):
        import tempfile
        from pathlib import Path
        from finance_tracker.cashback_events import CashbackEventStore, build_live_dashboard
        from tests.test_cashback_events import actual_receipt, actual_receipt_digest, statement_digest

        with tempfile.TemporaryDirectory() as temporary:
            store = CashbackEventStore(Path(temporary) / "events.sqlite3")
            reference = "EI-2026-08-frozen-evidence"
            digest = statement_digest(reference)
            store.reconcile_statement({
                "statement_reference": reference, "statement_sha256": digest,
                "card_code": "EI_AMAZON", "period_start": "2026-08-01",
                "period_end": "2026-08-31", "transactions": [],
            })
            receipt = actual_receipt(reference, "2026-08-01", "2026-08-31")
            store.finalize_period({
                "statement_reference": reference, "statement_sha256": digest,
                "statement_evidence_reference": "sha256:immutable-statement",
                "statement_document_url": "Finance Evidence/ei-2026-08.pdf",
                "actual_import_receipt": receipt,
                "actual_import_receipt_sha256": actual_receipt_digest(receipt),
            })
            before = store.period_rows()
            self.assertIn("FINALIZED", {row["status"] for row in before})
            historical = build_live_dashboard(store, date(2026, 8, 31))
            current = build_live_dashboard(store, date(2026, 9, 5))
            self.assertEqual(store.period_rows(), before)
            self.assertTrue(next(c for c in historical["cards"] if c["card"] == "EI_AMAZON")["reward_eligibility_verified"])
            self.assertIsNone(next(c for c in current["cards"] if c["card"] == "EI_AMAZON")["expected_cashback_aed"])
