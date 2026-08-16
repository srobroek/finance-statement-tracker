import json
from datetime import date
from unittest import TestCase

from finance_tracker.actual_pipeline import (
    account_maps,
    build_actual_statement_run,
    load_compiled_rules,
)
from finance_tracker.actual_snapshot import cashback_dashboard, transactions_from_actual_snapshot
from finance_tracker.cashback import PaymentIntent, poc_programs
from finance_tracker.models import money
from finance_tracker.statements import parse_statement_text


class ActualStatementPipelineTests(TestCase):
    def config(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "accounts": [
                {
                    "name": "Emirates Islamic Amazon Credit Card · 0082",
                    "card_code": "EI_AMAZON",
                    "card_last4": ["0082"],
                    "owner": "Owner A",
                }
            ],
        }

    def statement(self):
        return parse_statement_text(
            """Statement of Card Account
From: 1st Jul 2026
31st Jul 2026
To:
OPENING BALANCE 100.00
PRIMARY CARD NO:5424XXXXXXXX0082
02 JUL 02 JUL TRANSFER PAYMENT RECEIVED THANK YOU 100.00CR
10 JUL 09 JUL AMAZON.AE DUBAI ARE 25.00
Card Limit Available Limit Minimum Payment Due Payment Due Date Total Payment Due Profit/Other Charges (AED) Current Balance (AED)
50,000.00 49,975.00 25.00 25/08/26 25.00 0.00 25.00
""",
            "ei.pdf",
        )

    def test_builds_auditable_actual_manifest(self) -> None:
        run = build_actual_statement_run(self.statement(), self.config())

        self.assertTrue(run.statement["balance_tied"])
        self.assertEqual(run.statement["transaction_count"], 2)
        self.assertEqual(run.envelopes[0]["account"], "Emirates Islamic Amazon Credit Card · 0082")
        self.assertTrue(run.envelopes[0]["default_cleared"])
        self.assertTrue(run.envelopes[0]["records"][0]["cleared"])
        self.assertIn("#owner-owner-a", run.envelopes[0]["records"][0]["notes"])
        self.assertEqual(run.cashback_reconciliation, ())

    def test_rejects_unmapped_card_suffix(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unmapped card suffixes: 0082"):
            build_actual_statement_run(
                self.statement(),
                {"schema_version": 1, "accounts": [{"name": "Other", "card_last4": []}]},
            )

    def test_account_map_rejects_duplicate_suffixes(self) -> None:
        config = {
            "accounts": [
                {"name": "One", "card_code": "ONE", "card_last4": ["1234"]},
                {"name": "Two", "card_code": "TWO", "card_last4": ["1234"]},
            ]
        }
        with self.assertRaisesRegex(ValueError, "mapped more than once"):
            account_maps(config)

    def test_loads_canonical_rule_json(self) -> None:
        from tempfile import TemporaryDirectory
        from pathlib import Path

        rule = {
            "schema_version": 1,
            "rule_id": "amazon",
            "name": "Amazon",
            "stage": "VENDOR_NORMALIZATION",
            "priority": 10,
            "match": {"any": [{"all": [{"field": "merchant_raw", "operator": "contains", "value": "AMAZON"}]}]},
            "actions": [{"action": "set", "field": "vendor", "value": "Amazon", "sequence": 10}],
            "stop_on_match": True,
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            path.write_text(json.dumps(rule), encoding="utf-8")
            loaded = load_compiled_rules(path)
        self.assertEqual(loaded[0].rule_id, "amazon")
        self.assertEqual(loaded[0].conditions[0].group, 1)

    def test_snapshot_drives_cashback_without_a_second_ledger(self) -> None:
        snapshot = {
            "transactions": [
                {
                    "id": "actual-1",
                    "account_name": "Emirates Islamic Amazon Credit Card · 0082",
                    "date": "2026-07-10",
                    "amount": -10000,
                    "imported_payee": "AMAZON.AE",
                    "payee_name": "Amazon",
                    "category_name": "Online Shopping",
                    "notes": "source:statement #channel-online",
                    "cleared": True,
                    "reconciled": False,
                    "transfer_id": None,
                }
            ]
        }
        rows = transactions_from_actual_snapshot(snapshot, self.config())
        dashboard = cashback_dashboard(
            poc_programs(),
            rows,
            date(2026, 7, 31),
            [PaymentIntent("AMAZON", money("100"), "AED", "ONLINE")],
        )

        self.assertEqual(rows[0].reward_bucket, "EI_AMAZON")
        self.assertEqual(rows[0].owner, "Owner A")
        self.assertEqual(dashboard["cards"][2]["total_spend_aed"], "100")
        self.assertEqual(dashboard["recommendations"][0]["use_card"], "EI_AMAZON")
        self.assertEqual(dashboard["cards"][0]["routing_mode"], "CURRENT_TIER")

    def test_snapshot_treats_tagged_card_payment_as_transfer(self) -> None:
        snapshot = {
            "transactions": [
                {
                    "id": "actual-payment",
                    "account_name": "Emirates Islamic Amazon Credit Card · 0082",
                    "date": "2026-07-02",
                    "amount": -10000,
                    "imported_payee": "PAYMENT RECEIVED",
                    "payee_name": "Payment Received",
                    "category_name": "Card Payments",
                    "notes": "source:statement | #card-payment | #transfer | #owner-owner-a",
                    "cleared": True,
                    "reconciled": False,
                    "transfer_id": None,
                }
            ]
        }

        row = transactions_from_actual_snapshot(snapshot, self.config())[0]

        self.assertEqual(row.transaction_type, "TRANSFER")
        self.assertEqual(row.owner, "Owner A")
        self.assertNotIn("owner-owner-a", row.tags)

    def test_late_cycle_does_not_assume_an_unreachable_target_tier(self) -> None:
        dashboard = cashback_dashboard(
            poc_programs(),
            [],
            date(2026, 8, 28),
            [PaymentIntent("GENERAL", money("100"), "AED", "ONLINE")],
        )

        sc = next(card for card in dashboard["cards"] if card["card"] == "SC_PLATINUM_X")
        self.assertEqual(sc["routing_mode"], "CURRENT_TIER")
        self.assertEqual(dashboard["recommendations"][0]["use_card"], "RAK_WORLD")
