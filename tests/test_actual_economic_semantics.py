from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from jsonschema import Draft202012Validator

from finance_tracker.actual_pipeline import load_compiled_rules
from finance_tracker.actual_snapshot import transactions_from_actual_snapshot
from finance_tracker.models import Transaction
from finance_tracker.platforms import ActualBudgetAdapter
from finance_tracker.rules import RuleEngine
from finance_tracker.transaction_semantics import (
    TOPIC_SEMANTICS,
    actual_amount_minor,
    finalize_transaction_topic,
)


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "actual-economic-semantics.json"
SCHEMA = ROOT / "config" / "actual-economic-semantics-fixture-schema-v1.json"
TAXONOMY = ROOT / "config" / "actual-account-taxonomy.json"
OWNERSHIP = ROOT / "integrations" / "n8n" / "generated" / "rule-ownership-manifest.json"
ACTUAL_RULES = ROOT / "integrations" / "n8n" / "generated" / "actual-rules.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ActualEconomicSemanticsTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = _read(FIXTURE)
        cls.taxonomy = _read(TAXONOMY)
        cls.rules = RuleEngine(
            load_compiled_rules(ROOT / "config" / "static-rules.seed.json")
        )
        cls.accounts = {row["account_key"]: row for row in cls.taxonomy["accounts"]}

    def test_fixture_schema_is_valid_and_references_canonical_sources(self) -> None:
        schema = _read(SCHEMA)
        errors = sorted(Draft202012Validator(schema).iter_errors(self.fixture), key=str)
        self.assertEqual(errors, [])
        self.assertEqual(self.fixture["account_taxonomy"], "config/actual-account-taxonomy.json")
        self.assertEqual(self.fixture["rules"], "config/static-rules.seed.json")
        self.assertEqual(
            len(self.fixture["cases"]),
            len({case["case_id"] for case in self.fixture["cases"]}),
        )
        replay_ids = {case["source_case_id"] for case in self.fixture["replay_cases"]}
        self.assertTrue(replay_ids <= {case["case_id"] for case in self.fixture["cases"]})
        negative_ids = {case["case_id"] for case in self.fixture["negative_cases"]}
        self.assertEqual(len(negative_ids), len(self.fixture["negative_cases"]))

    def _transaction(self, case: dict) -> Transaction:
        initial_topic = {
            "TRANSFER": "PAYMENT" if "card-payment" in case.get("tags", []) else "CREDIT",
            "REWARD_CREDIT": "REWARD_CREDIT",
            "INCOME": "INCOME",
        }.get(case["topic"], "PURCHASE")
        transaction = Transaction(
            transaction_id=f"fixture:{case['case_id']}",
            transaction_at=datetime(2026, 8, 20),
            card="FIXTURE",
            account=case["account_name"],
            merchant_raw=case["description"],
            vendor=case.get("vendor"),
            amount_aed=Decimal(case["amount_aed"]),
            source_direction=case["direction"],
            transaction_type=initial_topic,
            tags=set(case.get("tags", [])),
            metadata={
                "account_balance_convention": case.get("account_balance_convention", "ASSET")
            },
        )
        self.rules.apply_stages(transaction, ("TRANSACTION_NORMALIZATION",))
        finalize_transaction_topic(transaction)
        return transaction

    def test_full_fixture_corpus_preserves_topic_sign_and_spend(self) -> None:
        transactions = []
        expected_by_id = {}
        for case in self.fixture["cases"]:
            account = self.accounts.get(case["account_key"])
            self.assertIsNotNone(account, case["case_id"])
            self.assertEqual(account["actual"]["name"], case["account_name"])
            self.assertIn(account["balance_sign"], {"ASSET_POSITIVE", "LIABILITY_NEGATIVE"})
            transaction = self._transaction(case)
            self.assertEqual(transaction.transaction_type, case["topic"], case["case_id"])
            self.assertEqual(
                transaction.spend_aed,
                Decimal(case["expected_spend_aed"]),
                case["case_id"],
            )
            expected_by_id[transaction.transaction_id] = case["expected_actual_amount_minor"]
            transactions.append(transaction)

        envelopes = ActualBudgetAdapter().serialize_import(transactions)
        records = [record for envelope in envelopes for record in envelope.records]
        self.assertEqual(len(records), len(self.fixture["cases"]))
        self.assertEqual(len({record["imported_id"] for record in records}), len(records))
        self.assertEqual(
            {record["imported_id"]: record["amount"] for record in records},
            expected_by_id,
        )
        self.assertFalse(any(record.get("is_balancing") for record in records))

    def test_taxonomy_has_no_fabricated_provider_identity(self) -> None:
        for account in self.accounts.values():
            if account["provider_identity_status"] == "UNAVAILABLE":
                self.assertIsNone(account["provider_account_id"])
                self.assertIsNone(account["provider_identity_source"])
            else:
                self.assertIsNotNone(account["provider_account_id"])

        fixture_account_keys = {case["account_key"] for case in self.fixture["cases"]}
        self.assertTrue(fixture_account_keys <= set(self.accounts))
        self.assertTrue(all("provider_account_id" not in case for case in self.fixture["cases"]))

    def _snapshot_config(self) -> dict[str, object]:
        names = sorted({case["account_name"] for case in self.fixture["cases"]})
        return {
            "accounts": [
                {"name": name, "card_code": f"FIXTURE_{index}"}
                for index, name in enumerate(names, start=1)
            ]
        }

    def test_actual_notes_round_trip_preserves_canonical_topics(self) -> None:
        by_case = {case["case_id"]: case for case in self.fixture["cases"]}
        config = self._snapshot_config()
        source_transactions = []
        for replay in self.fixture["replay_cases"]:
            source_transactions.append(self._transaction(by_case[replay["source_case_id"]]))

        envelopes = ActualBudgetAdapter().serialize_import(source_transactions)
        snapshot_rows = [
            {
                "id": record["imported_id"],
                "imported_id": record["imported_id"],
                "account_name": envelope.account,
                "date": record["date"],
                "amount": record["amount"],
                "imported_payee": record["imported_payee"],
                "payee_name": record["payee_name"],
                "category_name": record.get("category_name"),
                "notes": record["notes"],
            }
            for envelope in envelopes
            for record in envelope.records
        ]
        notes_by_id = {row["id"]: row["notes"] for row in snapshot_rows}
        self.assertIn("#reversal", notes_by_id["fixture:merchant-reversal"])
        self.assertIn("#refund", notes_by_id["fixture:merchant-reversal"])
        for case_id, topic_tag in (
            ("bank-fee", "#fee"),
            ("interest-charge", "#interest"),
            ("investment-contribution", "#investment"),
            ("investment-distribution", "#investment"),
        ):
            self.assertIn(topic_tag, notes_by_id[f"fixture:{case_id}"])
        replayed = transactions_from_actual_snapshot(
            {"transactions": snapshot_rows}, config
        )
        expected = [replay["expected_topic"] for replay in self.fixture["replay_cases"]]
        self.assertEqual(
            {transaction.transaction_type for transaction in replayed}, set(expected)
        )
        self.assertEqual(
            {
                transaction.transaction_id: transaction.transaction_type
                for transaction in replayed
            },
            {
                f"fixture:{replay['source_case_id']}": replay["expected_topic"]
                for replay in self.fixture["replay_cases"]
            },
        )
        expected_spend = {
            case_id: Decimal(by_case[case_id]["expected_spend_aed"])
            for case_id in {
                replay["source_case_id"] for replay in self.fixture["replay_cases"]
            }
        }
        for transaction in replayed:
            case_id = transaction.transaction_id.removeprefix("fixture:")
            self.assertEqual(transaction.spend_aed, expected_spend[case_id])
            if case_id in {
                "bank-fee",
                "interest-charge",
                "investment-contribution",
                "investment-distribution",
            }:
                self.assertNotEqual(transaction.transaction_type, "PURCHASE")

    def test_snapshot_replay_preserves_signed_transfer_direction_and_amount(self) -> None:
        by_case = {case["case_id"]: case for case in self.fixture["cases"]}
        config = self._snapshot_config()
        source_transactions = [
            self._transaction(by_case[case_id])
            for case_id in (
                "liability-card-payment",
                "asset-card-payment",
                "asset-transfer-credit",
            )
        ]
        envelopes = ActualBudgetAdapter().serialize_import(source_transactions)
        snapshot_rows = [
            {
                "id": record["imported_id"],
                "imported_id": record["imported_id"],
                "account_name": envelope.account,
                "date": record["date"],
                "amount": record["amount"],
                "imported_payee": record["imported_payee"],
                "payee_name": record["payee_name"],
                "category_name": record.get("category_name"),
                "notes": record["notes"],
            }
            for envelope in envelopes
            for record in envelope.records
        ]

        replayed = transactions_from_actual_snapshot(
            {"transactions": snapshot_rows}, config
        )

        self.assertEqual(
            {
                transaction.transaction_id: transaction.source_direction
                for transaction in replayed
            },
            {
                "fixture:liability-card-payment": "CREDIT",
                "fixture:asset-card-payment": "DEBIT",
                "fixture:asset-transfer-credit": "CREDIT",
            },
        )
        replayed_records = {
            record["imported_id"]: record["amount"]
            for envelope in ActualBudgetAdapter().serialize_import(replayed)
            for record in envelope.records
        }
        self.assertEqual(
            replayed_records,
            {
                "fixture:liability-card-payment": 355,
                "fixture:asset-card-payment": -355,
                "fixture:asset-transfer-credit": 10000,
            },
        )

    def test_negative_topic_boundaries_fail_closed(self) -> None:
        for case in self.fixture["negative_cases"]:
            transaction = Transaction(
                transaction_id=f"negative:{case['case_id']}",
                transaction_at=datetime(2026, 8, 20),
                card="FIXTURE",
                merchant_raw="boundary",
                amount_aed=Decimal(case["amount_aed"]),
                transaction_type=case["transaction_type"],
                source_direction=case.get("source_direction"),
            )
            with self.assertRaisesRegex(ValueError, case["expected_error"]):
                actual_amount_minor(transaction)

        account_name = self.fixture["cases"][0]["account_name"]
        with self.assertRaisesRegex(ValueError, "Conflicting canonical Actual topic tags"):
            transactions_from_actual_snapshot(
                {
                    "transactions": [
                        {
                            "id": "negative:conflicting-tags",
                            "account_name": account_name,
                            "date": "2026-08-20",
                            "amount": -100,
                            "imported_payee": "Boundary",
                            "notes": "#fee #investment",
                        }
                    ]
                },
                self._snapshot_config(),
            )

    def test_topics_and_unsupported_actions_stay_worker_owned(self) -> None:
        ownership = {
            (row["rule_id"], row["rule_set"]): row
            for row in _read(OWNERSHIP)["ownership"]
        }
        source_rules = _read(ROOT / "config" / "static-rules.seed.json")
        for rule in source_rules:
            row = ownership[(rule["rule_id"], "FULL_LEDGER")]
            semantic_action = any(
                action.get("field") in {"transaction_type", "reward_bucket", "is_refund"}
                or action.get("action") in {"add_tag", "add_tags", "remove_tag"}
                for action in rule["actions"]
            )
            if semantic_action or rule["stage"] != "CLASSIFICATION":
                self.assertEqual(row["execution_owner"], "N8N_ONLY", rule["rule_id"])

        native = _read(ACTUAL_RULES)["rules"]
        self.assertTrue(native)
        for rule in native:
            self.assertEqual(rule["stage"], "CLASSIFICATION")
            self.assertTrue(
                all(
                    action["action"] == "set_if_empty"
                    and action["field"] in {"category", "subcategory"}
                    for action in rule["actions"]
                ),
                rule["rule_id"],
            )

    def test_topic_contract_declares_all_fixture_topics(self) -> None:
        self.assertTrue({case["topic"] for case in self.fixture["cases"]} <= set(TOPIC_SEMANTICS))
        self.assertEqual(TOPIC_SEMANTICS["TRANSFER"].actual_sign, None)
        self.assertEqual(TOPIC_SEMANTICS["INVESTMENT"].spend_factor, 0)
