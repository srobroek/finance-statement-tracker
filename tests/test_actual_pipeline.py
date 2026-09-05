import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from finance_tracker.actual_pipeline import (
    account_maps,
    build_actual_statement_run,
    load_compiled_rules,
    load_actual_config,
    runtime_secret,
)
from finance_tracker.actual_snapshot import cashback_dashboard, transactions_from_actual_snapshot
from finance_tracker.cashback import PaymentIntent, configured_programs
from finance_tracker.models import Transaction, money
from finance_tracker.statements import parse_statement_text


class ActualStatementPipelineTests(TestCase):
    def test_actual_config_validates_only_python_consumed_fields(self) -> None:
        from tempfile import TemporaryDirectory

        valid = {
            "schema_version": 1,
            "currency": "AED",
            "accounts": [
                {
                    "name": "Card",
                    "card_code": "CARD",
                    "card_last4": ["1234"],
                    "aliases": ["Old Card"],
                    "owner": "Owner",
                    "type": "checking",
                }
            ],
            "retired_accounts": ["Retired Card"],
            "unconsumed": {"may": "change"},
            "offbudget": {"opaque": "configuration"},
        }
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "actual.json"
            path.write_text(json.dumps(valid), encoding="utf-8")
            self.assertEqual(load_actual_config(path)["accounts"][0]["name"], "Card")

            invalid_values = (
                ("currency", 3),
                ("accounts", [{"name": "Card", "card_last4": [1234]}]),
                ("accounts", [{"name": "Card", "aliases": "Old Card"}]),
                ("accounts", [{"name": "Card", "owner": False}]),
                ("accounts", [{"name": "Card", "type": {"opaque": "configuration"}}]),
                ("accounts", [{"name": "Card", "type": "crypto"}]),
                ("accounts", [{"name": "Card", "enabled": "false"}]),
                ("retired_accounts", "Retired Card"),
            )
            for field, value in invalid_values:
                with self.subTest(field=field):
                    candidate = dict(valid)
                    candidate[field] = value
                    path.write_text(json.dumps(candidate), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_actual_config(path)

    def test_actual_config_rejects_boolean_schema_version(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "actual.json"
            path.write_text(
                json.dumps({"schema_version": True, "accounts": [{"name": "Card"}]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "schema_version"):
                load_actual_config(path)

    def test_runtime_secret_file_takes_precedence_over_environment(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temporary:
            secret_path = Path(temporary) / "statement-password"
            secret_path.write_text("file-secret\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "BANK_PASSWORD": "legacy-secret",
                    "BANK_PASSWORD_FILE": str(secret_path),
                },
                clear=False,
            ):
                self.assertEqual(runtime_secret("BANK_PASSWORD"), "file-secret")

    def cashback_config(self) -> dict[str, object]:
        return json.loads(Path("config/cashback-programs.json").read_text(encoding="utf-8"))

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

    def test_historical_v2_statement_exports_ledger_without_cashback_programme(self) -> None:
        # The bundled schema-v2 cashback programmes begin in August.  A July
        # statement must still produce its Actual envelope while omitting
        # cashback reconciliation rather than applying the future programme.
        run = build_actual_statement_run(self.statement(), self.config())

        self.assertEqual(run.cashback_reconciliation, ())
        self.assertEqual(
            [record["amount"] for record in run.envelopes[0]["records"]],
            [10000, -2500],
        )

    def test_ei_positive_amazon_statement_row_is_refund_without_cashback_tag(self) -> None:
        statement = parse_statement_text(
            """Statement of Card Account
From: 1st Aug 2026
31st Aug 2026
To:
OPENING BALANCE 100.00
PRIMARY CARD NO:5424XXXXXXXX0082
12 AUG 12 AUG AMAZON.AE DUBAI ARE 3.55CR
Card Limit Available Limit Minimum Payment Due Payment Due Date Total Payment Due Profit/Other Charges (AED) Current Balance (AED)
50,000.00 49,903.55 96.45 25/08/26 96.45 0.00 96.45
""",
            "ei-refund.pdf",
        )

        run = build_actual_statement_run(
            statement,
            self.config(),
            load_compiled_rules("config/static-rules.seed.json"),
        )

        record = run.envelopes[0]["records"][0]
        self.assertEqual(record["amount"], 355)
        self.assertEqual(record["category_name"], "Online Shopping")
        self.assertIn("#refund", record["notes"])
        self.assertNotIn("#cashback-", record["notes"])
        self.assertEqual(
            run.cashback_reconciliation[0]["transactions"][0]["event_type"],
            "REFUND",
        )
        self.assertIsNone(
            run.cashback_reconciliation[0]["transactions"][0]["bucket_code"]
        )

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

    def test_wio_credit_statement_maps_card_and_account_suffix_to_one_account(self) -> None:
        statement = parse_statement_text(
            """CREDIT STATEMENT
FROM 01/07/2026 TO 01/08/2026
Wio Bank PAYMENT DUE DATE MIN. PAYMENT DUE TOTAL TO PAY
01/08/2026 0.00 0.00
ACCOUNT NUMBER 3342325009
Balance From Last Statement 0.00
Closing balance (Total to pay) -25.00
04/07/2026 P100000001 Example Merchant ****4113 -100.00
01/08/2026 P100000002 Credit Repayment +125.00
""",
            "wio.pdf",
            "wio_credit_v1",
        )
        config = {
            "accounts": [
                {
                    "name": "Wio Credit Card · 4113 / 5009",
                    "card_code": "WIO_CREDIT",
                    "card_last4": ["4113", "5009"],
                }
            ]
        }

        run = build_actual_statement_run(statement, config, load_compiled_rules("config/static-rules.seed.json"))

        self.assertEqual(len(run.envelopes), 1)
        self.assertEqual(run.envelopes[0]["account"], "Wio Credit Card · 4113 / 5009")
        self.assertEqual([row["amount"] for row in run.envelopes[0]["records"]], [-10000, 12500])
        self.assertIn("#card-payment", run.envelopes[0]["records"][1]["notes"])

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
            configured_programs(date(2026, 8, 16)),
            rows,
            date(2026, 7, 31),
            [PaymentIntent("AMAZON", money("100"), "AED", "ONLINE")],
        )

        self.assertEqual(rows[0].reward_bucket, "EI_AMAZON")
        self.assertEqual(rows[0].owner, "Owner A")
        self.assertEqual(dashboard["cards"][2]["total_spend_aed"], "100")
        self.assertEqual(dashboard["recommendations"][0]["use_card"], "SC_PLATINUM_X")
        self.assertEqual(dashboard["recommendations"][0]["decision_amount_aed"], "100")
        self.assertEqual(dashboard["recommendations"][0]["estimated_net_return_percent"], "10.00")
        preferred = dashboard["recommendations"][0]["ranked_cards"][0]
        self.assertEqual(preferred["status"], "PREFERRED")
        self.assertEqual(preferred["bucket"], "SC_ONLINE")
        self.assertEqual(preferred["bucket_cap_aed"], "4000")
        self.assertEqual(preferred["target_rate_percent"], "10.00")
        self.assertEqual(preferred["current_state_marginal_return_percent"], "0.00")
        self.assertEqual(preferred["current_tier_rate_percent"], "0")
        self.assertEqual(preferred["conditional_target_rate_percent"], "10.00")
        self.assertEqual(preferred["estimate_basis"], "CONDITIONAL_TARGET_TIER")
        self.assertEqual(dashboard["cards"][0]["routing_mode"], "TARGET_TIER")

    def test_snapshot_rejects_conflicting_cashback_bucket_tags_deterministically(self) -> None:
        snapshot = {
            "transactions": [
                {
                    "id": "actual-conflict",
                    "account_name": "Emirates Islamic Amazon Credit Card · 0082",
                    "date": "2026-07-10",
                    "amount": -10000,
                    "imported_payee": "AMAZON.AE",
                    "category_name": "Online Shopping",
                    "notes": "#cashback-sc-wallet #cashback-ei_amazon",
                }
            ]
        }
        with self.assertRaisesRegex(
            ValueError, "Conflicting cashback bucket tags: EI_AMAZON, SC_WALLET"
        ):
            transactions_from_actual_snapshot(snapshot, self.config(), self.cashback_config())

    def test_snapshot_skips_retired_accounts_but_rejects_unknown_active_accounts(self) -> None:
        retired = {
            "transactions": [
                {
                    "id": "retired",
                    "account_name": "Wio Current Account · 4113",
                    "date": "2026-07-10",
                    "amount": -100,
                }
            ]
        }
        config = self.config()
        config["retired_accounts"] = ["Wio Current Account · 4113"]
        self.assertEqual(
            transactions_from_actual_snapshot(retired, config, self.cashback_config()), []
        )

        unknown = {
            "transactions": [
                {
                    "id": "unknown",
                    "account_name": "Unexpected Active Account",
                    "date": "2026-07-10",
                    "amount": -100,
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "Unknown active Actual snapshot accounts"):
            transactions_from_actual_snapshot(unknown, config, self.cashback_config())

        non_reward = {
            "transactions": [
                {
                    "id": "non-reward",
                    "account_name": "FAB Current",
                    "date": "2026-07-10",
                    "amount": -100,
                }
            ]
        }
        non_reward_config = {
            "accounts": [{"name": "FAB Current", "card_code": "FAB_CURRENT_2001"}]
        }
        rows = transactions_from_actual_snapshot(
            non_reward, non_reward_config, self.cashback_config()
        )
        self.assertEqual(rows[0].card, "FAB_CURRENT_2001")
        self.assertIsNone(rows[0].reward_bucket)

    def test_snapshot_skips_disabled_accounts(self) -> None:
        snapshot = {
            "transactions": [
                {
                    "id": "disabled",
                    "account_name": "Disabled Account",
                    "date": "2026-07-10",
                    "amount": -100,
                }
            ]
        }
        config = {
            "accounts": [
                {"name": "Disabled Account", "card_code": "DISABLED", "enabled": False},
                {"name": "Active Account", "card_code": "ACTIVE"},
            ]
        }
        self.assertEqual(
            transactions_from_actual_snapshot(snapshot, config, self.cashback_config()), []
        )

    def test_snapshot_rejects_alias_collision_between_accounts(self) -> None:
        config = {
            "accounts": [
                {"name": "One", "card_code": "ONE", "aliases": ["Shared"]},
                {"name": "Two", "card_code": "TWO", "aliases": ["Shared"]},
            ]
        }
        with self.assertRaisesRegex(ValueError, "alias .* maps to multiple cards"):
            transactions_from_actual_snapshot(
                {"transactions": []}, config, self.cashback_config()
            )

    def test_filler_route_is_inactive_after_all_card_thresholds_are_met(self) -> None:
        rows = [
            Transaction("rak-target", datetime(2026, 8, 10), "RAK_WORLD", "Filler", "10300", category="FILLER", reward_bucket="RAK_STANDARD"),
            Transaction("sc-target", datetime(2026, 8, 10), "SC_PLATINUM_X", "Filler", "15300", category="FILLER", reward_bucket="SC_ONLINE"),
        ]

        dashboard = cashback_dashboard(
            configured_programs(date(2026, 8, 16)),
            rows,
            date(2026, 8, 16),
            [PaymentIntent("FILLER", money("100"), "AED", "PHYSICAL_POS", conditional=True)],
            routing_profiles=self.cashback_config()["routing_profiles"],
            route_policies=self.cashback_config()["route_policies"],
        )

        self.assertFalse(dashboard["recommendations"][0]["active"])
        filler = next(graph for graph in dashboard["routing_graphs"] if graph["code"] == "FILLER")
        self.assertFalse(filler["active"])

    def test_grocery_graph_considers_channels_and_reorders_after_rak_cap(self) -> None:
        profiles = self.cashback_config()["routing_profiles"]
        empty = cashback_dashboard(
            configured_programs(date(2026, 8, 16)),
            [],
            date(2026, 8, 16),
            [PaymentIntent("GROCERY", money("100"), "AED", "PHYSICAL_POS")],
            routing_profiles=profiles,
            route_policies=self.cashback_config()["route_policies"],
        )
        grocery = next(graph for graph in empty["routing_graphs"] if graph["code"] == "GROCERY")
        self.assertEqual(grocery["ranked_cards"][0]["card"], "RAK_WORLD")
        self.assertEqual(
            {candidate["payment_channel"] for candidate in grocery["ranked_cards"]},
            {"PHYSICAL_POS", "ONLINE", "APPLE_PAY_POS"},
        )

        capped = cashback_dashboard(
            configured_programs(date(2026, 8, 16)),
            [Transaction("rak-grocery-cap", datetime(2026, 8, 10), "RAK_WORLD", "Groceries", "3000", category="GROCERY", channel="PHYSICAL_POS", reward_bucket="RAK_GROCERY")],
            date(2026, 8, 16),
            [PaymentIntent("GROCERY", money("100"), "AED", "PHYSICAL_POS")],
            routing_profiles=profiles,
            route_policies=self.cashback_config()["route_policies"],
        )
        grocery = next(graph for graph in capped["routing_graphs"] if graph["code"] == "GROCERY")
        self.assertEqual(grocery["ranked_cards"][0]["card"], "SC_PLATINUM_X")
        self.assertNotIn("RAK_GROCERY", {candidate["bucket"] for candidate in grocery["ranked_cards"]})
        self.assertIn(
            ("RAK_WORLD", "RAK_STANDARD", "THRESHOLD_FILLER"),
            {(candidate["card"], candidate["bucket"], candidate["purpose"]) for candidate in grocery["ranked_cards"]},
        )

    def test_grocery_graph_prioritizes_under_pace_sc_over_rak_tier_unlock(self) -> None:
        dashboard = cashback_dashboard(
            configured_programs(date(2026, 8, 16)),
            [
                Transaction("rak-grocery-cap", datetime(2026, 8, 10), "RAK_WORLD", "Groceries", "3000", category="GROCERY", channel="PHYSICAL_POS", reward_bucket="RAK_GROCERY"),
                Transaction("rak-dining-cap", datetime(2026, 8, 11), "RAK_WORLD", "Dining", "3000", category="DINING", channel="PHYSICAL_POS", reward_bucket="RAK_DINING"),
                Transaction("rak-travel-near-cap", datetime(2026, 8, 12), "RAK_WORLD", "Travel", "3950", category="TRAVEL", channel="PHYSICAL_POS", reward_bucket="RAK_TRAVEL"),
            ],
            date(2026, 8, 16),
            [PaymentIntent("GROCERY", money("100"), "AED", "PHYSICAL_POS")],
            routing_profiles=self.cashback_config()["routing_profiles"],
            route_policies=self.cashback_config()["route_policies"],
        )

        grocery = next(graph for graph in dashboard["routing_graphs"] if graph["code"] == "GROCERY")
        preferred = grocery["ranked_cards"][0]
        self.assertEqual((preferred["card"], preferred["bucket"]), ("SC_PLATINUM_X", "SC_ONLINE"))
        self.assertEqual(preferred["pace_status"], "UNDER")
        rak_unlock = next(
            candidate
            for candidate in grocery["ranked_cards"]
            if (candidate["card"], candidate["bucket"]) == ("RAK_WORLD", "RAK_EWALLET")
        )
        self.assertEqual((rak_unlock["tier_before"], rak_unlock["tier_after"]), ("BASE", "ENHANCED"))
        self.assertEqual(rak_unlock["pace_status"], "OVER")
        self.assertGreater(
            Decimal(rak_unlock["estimated_net_value_aed"]),
            Decimal(preferred["estimated_net_value_aed"]),
        )
        self.assertEqual(grocery["avoid_cards"], ["RAK_WORLD"])
        travel = next(graph for graph in dashboard["routing_graphs"] if graph["code"] == "TRAVEL")
        self.assertEqual(travel["ranked_cards"][0]["card"], "SC_PLATINUM_X")
        self.assertNotIn("RAK_TRAVEL", {candidate["bucket"] for candidate in travel["ranked_cards"]})
        self.assertIn(
            ("RAK_WORLD", "RAK_STANDARD", "THRESHOLD_FILLER"),
            {(candidate["card"], candidate["bucket"], candidate["purpose"]) for candidate in travel["ranked_cards"]},
        )
        self.assertEqual(travel["avoid_cards"], ["RAK_WORLD"])

    def test_grocery_graph_uses_sc_filler_when_reward_buckets_are_full(self) -> None:
        dashboard = cashback_dashboard(
            configured_programs(date(2026, 8, 16)),
            [
                Transaction("rak-grocery-cap", datetime(2026, 8, 10), "RAK_WORLD", "Groceries", "3000", category="GROCERY", channel="PHYSICAL_POS", reward_bucket="RAK_GROCERY"),
                Transaction("rak-dining-cap", datetime(2026, 8, 11), "RAK_WORLD", "Dining", "3000", category="DINING", channel="PHYSICAL_POS", reward_bucket="RAK_DINING"),
                Transaction("rak-travel-near-cap", datetime(2026, 8, 12), "RAK_WORLD", "Travel", "3950", category="TRAVEL", channel="PHYSICAL_POS", reward_bucket="RAK_TRAVEL"),
                Transaction("sc-online-cap", datetime(2026, 8, 13), "SC_PLATINUM_X", "Online", "4000", category="GENERAL", channel="ONLINE", reward_bucket="SC_ONLINE"),
                Transaction("sc-wallet-cap", datetime(2026, 8, 14), "SC_PLATINUM_X", "Wallet", "2000", category="GENERAL", channel="APPLE_PAY_POS", reward_bucket="SC_WALLET"),
            ],
            date(2026, 8, 16),
            [PaymentIntent("GROCERY", money("100"), "AED", "PHYSICAL_POS")],
            routing_profiles=self.cashback_config()["routing_profiles"],
            route_policies=self.cashback_config()["route_policies"],
        )

        grocery = next(graph for graph in dashboard["routing_graphs"] if graph["code"] == "GROCERY")
        preferred = grocery["ranked_cards"][0]
        self.assertEqual((preferred["card"], preferred["bucket"]), ("SC_PLATINUM_X", "SC_FILLER"))
        self.assertEqual(preferred["pace_status"], "UNDER")

    def test_grocery_graph_returns_to_rak_after_sc_target_is_secured(self) -> None:
        dashboard = cashback_dashboard(
            configured_programs(date(2026, 8, 16)),
            [
                Transaction("rak-grocery-cap", datetime(2026, 8, 10), "RAK_WORLD", "Groceries", "3000", category="GROCERY", channel="PHYSICAL_POS", reward_bucket="RAK_GROCERY"),
                Transaction("rak-dining-cap", datetime(2026, 8, 11), "RAK_WORLD", "Dining", "3000", category="DINING", channel="PHYSICAL_POS", reward_bucket="RAK_DINING"),
                Transaction("rak-travel-near-cap", datetime(2026, 8, 12), "RAK_WORLD", "Travel", "3950", category="TRAVEL", channel="PHYSICAL_POS", reward_bucket="RAK_TRAVEL"),
                Transaction("sc-online-cap", datetime(2026, 8, 13), "SC_PLATINUM_X", "Online", "4000", category="GENERAL", channel="ONLINE", reward_bucket="SC_ONLINE"),
                Transaction("sc-wallet-cap", datetime(2026, 8, 14), "SC_PLATINUM_X", "Wallet", "2000", category="GENERAL", channel="APPLE_PAY_POS", reward_bucket="SC_WALLET"),
                Transaction("sc-filler", datetime(2026, 8, 15), "SC_PLATINUM_X", "Filler", "9300", category="FILLER", channel="PHYSICAL_POS", reward_bucket="SC_FILLER"),
            ],
            date(2026, 8, 16),
            [PaymentIntent("GROCERY", money("100"), "AED", "PHYSICAL_POS")],
            routing_profiles=self.cashback_config()["routing_profiles"],
            route_policies=self.cashback_config()["route_policies"],
        )

        grocery = next(graph for graph in dashboard["routing_graphs"] if graph["code"] == "GROCERY")
        self.assertEqual(
            (grocery["ranked_cards"][0]["card"], grocery["ranked_cards"][0]["bucket"]),
            ("RAK_WORLD", "RAK_EWALLET"),
        )
        self.assertNotIn("SC_FILLER", {candidate["bucket"] for candidate in grocery["ranked_cards"]})

    def test_sc_online_cap_routes_wallet_amazon_and_filler_to_open_buckets(self) -> None:
        dashboard = cashback_dashboard(
            configured_programs(date(2026, 8, 16)),
            [
                Transaction("rak-grocery-cap", datetime(2026, 8, 10), "RAK_WORLD", "Groceries", "3000", category="GROCERY", channel="PHYSICAL_POS", reward_bucket="RAK_GROCERY"),
                Transaction("rak-dining-cap", datetime(2026, 8, 11), "RAK_WORLD", "Dining", "3000", category="DINING", channel="PHYSICAL_POS", reward_bucket="RAK_DINING"),
                Transaction("rak-travel-near-cap", datetime(2026, 8, 12), "RAK_WORLD", "Travel", "3950", category="TRAVEL", channel="PHYSICAL_POS", reward_bucket="RAK_TRAVEL"),
                Transaction("sc-online-cap", datetime(2026, 8, 13), "SC_PLATINUM_X", "Online", "4000", category="GENERAL", channel="ONLINE", reward_bucket="SC_ONLINE"),
            ],
            date(2026, 8, 16),
            [PaymentIntent("GROCERY", money("100"), "AED", "PHYSICAL_POS")],
            routing_profiles=self.cashback_config()["routing_profiles"],
            route_policies=self.cashback_config()["route_policies"],
        )

        graphs = {graph["code"]: graph for graph in dashboard["routing_graphs"]}
        self.assertEqual(
            (graphs["GROCERY"]["ranked_cards"][0]["card"], graphs["GROCERY"]["ranked_cards"][0]["bucket"]),
            ("SC_PLATINUM_X", "SC_WALLET"),
        )
        self.assertNotIn("SC_ONLINE", {candidate["bucket"] for candidate in graphs["GROCERY"]["ranked_cards"]})
        self.assertEqual(graphs["AMAZON"]["ranked_cards"][0]["card"], "RAK_WORLD")
        self.assertEqual(
            (graphs["FILLER"]["ranked_cards"][0]["card"], graphs["FILLER"]["ranked_cards"][0]["bucket"]),
            ("SC_PLATINUM_X", "SC_WALLET"),
        )

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

    def test_snapshot_preserves_tagged_reversal_topic(self) -> None:
        snapshot = {
            "transactions": [
                {
                    "id": "actual-reversal",
                    "account_name": "Emirates Islamic Amazon Credit Card · 0082",
                    "date": "2026-07-03",
                    "amount": 355,
                    "imported_payee": "AMAZON.AE DUBAI ARE",
                    "payee_name": "Amazon",
                    "category_name": "Online Shopping",
                    "notes": "source:statement | #reversal | #refund",
                    "cleared": True,
                    "reconciled": False,
                    "transfer_id": None,
                }
            ]
        }

        row = transactions_from_actual_snapshot(snapshot, self.config())[0]

        self.assertEqual(row.transaction_type, "REVERSAL")
        self.assertTrue(row.is_refund)

    def test_late_cycle_keeps_target_for_routing_unavoidable_spend(self) -> None:
        dashboard = cashback_dashboard(
            configured_programs(date(2026, 8, 16)),
            [],
            date(2026, 8, 28),
            [PaymentIntent("GENERAL", money("100"), "AED", "ONLINE")],
        )

        sc = next(card for card in dashboard["cards"] if card["card"] == "SC_PLATINUM_X")
        self.assertEqual(sc["routing_mode"], "TARGET_TIER")
        self.assertEqual(dashboard["recommendations"][0]["use_card"], "SC_PLATINUM_X")
        preferred = dashboard["recommendations"][0]["ranked_cards"][0]
        self.assertEqual(preferred["current_state_marginal_reward_aed"], "0.00")
        self.assertEqual(preferred["conditional_target_reward_aed"], "10.00")
        self.assertEqual(preferred["estimate_basis"], "CONDITIONAL_TARGET_TIER")
