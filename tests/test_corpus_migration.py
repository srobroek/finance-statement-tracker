import json
from pathlib import Path
import tempfile
import unittest

from finance_tracker.corpus_migration import (
    build_guarded_migration_plan,
    classify_actual_record,
    regenerate_corpus,
    regenerate_manifest,
    validate_guarded_migration_plan,
)


class CorpusMigrationTests(unittest.TestCase):
    def _manifest(self):
        return {
            "schema_version": 1,
            "envelopes": [{
                "account": "Emirates Islamic Amazon Credit Card · 0082",
                "records": [
                    {"date": "2026-07-12", "amount": 355, "payee_name": "Amazon", "imported_payee": "AMAZON.AE DUBAI ARE", "imported_id": "ei:refund", "category_name": "Online Shopping", "notes": "source:statement | #amazon #cashback-ei_amazon #online"},
                    {"date": "2026-07-13", "amount": 1000, "payee_name": "Amazon Credit reward", "imported_id": "ei:reward", "category_name": "Online Shopping", "notes": "#amazon"},
                    {"date": "2026-07-02", "amount": 110000, "payee_name": "TRANSFER PAYMENT RECEIVED THANK YOU", "imported_id": "ei:payment", "category_name": "Online Shopping", "notes": "#primary"},
                    {"date": "2026-07-20", "amount": -2500, "payee_name": "Amazon", "imported_id": "ei:purchase", "category_name": "Online Shopping", "notes": "currency:USD | original:6.80 | #amazon #online"},
                ],
            }],
        }

    def test_semantics_distinguish_ei_refund_from_explicit_amazon_credit(self):
        refund = classify_actual_record(self._manifest()["envelopes"][0]["records"][0], "EI")
        reward = classify_actual_record(self._manifest()["envelopes"][0]["records"][1], "EI")
        self.assertEqual((refund.topic, refund.reason), ("REFUND", "POSITIVE_MERCHANT_CREDIT_DEFAULT"))
        self.assertEqual(reward.topic, "REWARD_CREDIT")
        self.assertEqual(reward.reward_medium, "AMAZON_CREDIT")
        self.assertFalse(reward.cash_equivalent)

    def test_amazon_credit_words_alone_are_not_reward_evidence(self):
        decision = classify_actual_record(
            {
                "amount": 355,
                "payee_name": "Amazon credit",
                "category_name": "Online Shopping",
            },
            "Emirates Islamic Amazon Credit Card · 0082",
        )
        self.assertEqual(decision.topic, "REFUND")
        self.assertEqual(decision.reason, "POSITIVE_MERCHANT_CREDIT_DEFAULT")

    def test_generic_positive_merchant_credit_defaults_to_refund(self):
        decision = classify_actual_record(
            {
                "amount": 1299,
                "payee_name": "NOON.COM refund",
                "category_name": "Online Shopping",
            },
            "FAB Current Account",
        )
        self.assertEqual(decision.topic, "REFUND")
        self.assertEqual(decision.tags, ("refund",))

    def test_deposit_income_and_ambiguous_credit_are_not_called_refunds(self):
        salary = classify_actual_record(
            {"amount": 100000, "payee_name": "Employer salary", "category_name": "Salary"},
            "FAB Current Account",
        )
        ambiguous = classify_actual_record(
            {"amount": 20000, "payee_name": "Incoming credit", "category_name": "Needs Review"},
            "FAB Current Account",
        )
        self.assertEqual(salary.topic, "INCOME")
        self.assertEqual(ambiguous.topic, "UNRESOLVED_CREDIT")
        self.assertEqual(ambiguous.tags, ("review",))

    def test_regeneration_cleans_notes_prepends_tags_and_never_changes_amount(self):
        source = self._manifest()
        regenerated, decisions = regenerate_manifest(source)
        records = regenerated["envelopes"][0]["records"]
        self.assertEqual([r["amount"] for r in records], [355, 1000, 110000, -2500])
        self.assertEqual(records[0]["category_name"], "Refunds & Reimbursements")
        self.assertEqual(records[0]["notes"], "#amazon #online #refund")
        self.assertEqual(records[1]["notes"], "#amazon #amazon-credit #reward")
        self.assertEqual(records[2]["notes"], "#card-payment #transfer")
        self.assertEqual(records[3]["notes"], "#amazon #online")
        self.assertEqual(len(decisions), 4)

    def test_corpus_output_is_deterministic_and_reports_every_topic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            first = root / "first"
            second = root / "second"
            source.mkdir()
            (source / "one.json").write_text(json.dumps(self._manifest()), encoding="utf-8")
            report1, desired1 = regenerate_corpus(source, first)
            report2, desired2 = regenerate_corpus(source, second)
            self.assertEqual((first / "one.json").read_bytes(), (second / "one.json").read_bytes())
            self.assertEqual(desired1, desired2)
            self.assertEqual(report1["topic_counts"], {"PURCHASE": 1, "REFUND": 1, "REWARD_CREDIT": 1, "TRANSFER": 1})
            self.assertEqual(report1["unique_imported_id_count"], 4)
            self.assertEqual(report1["amount_and_sign_checked_count"], 4)
            self.assertEqual(report1["amount_mutation_count"], 0)
            self.assertEqual(report1["note_contract_checked_count"], 4)
            self.assertEqual(report1["note_contract_violation_count"], 0)

    def test_exact_duplicate_imported_id_across_manifests_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            payload = json.dumps(self._manifest())
            (source / "one.json").write_text(payload, encoding="utf-8")
            (source / "two.json").write_text(payload, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate imported_id across corpus"):
                regenerate_corpus(source, root / "output")

    def test_guarded_plan_preserves_manual_memo_and_refuses_amount_drift(self):
        regenerated, decisions = regenerate_manifest(self._manifest())
        desired = {row["imported_id"]: row for row in decisions}
        snapshot = {
            "generated_at": "2026-08-19T00:00:00Z",
            "server": {"version": "26.8.1"},
            "transactions": [
                {"imported_id": "ei:refund", "account_name": "EI", "date": "2026-07-12", "amount": 355, "category_name": "Online Shopping", "notes": "#amazon #cashback-ei_amazon | Bought for guest"},
                {"imported_id": "ei:purchase", "account_name": "EI", "date": "2026-07-20", "amount": -2499, "category_name": "Online Shopping", "notes": "#amazon"},
            ],
        }
        plan, audit = build_guarded_migration_plan(snapshot, desired)
        self.assertEqual(len(plan["changes"]), 1)
        self.assertEqual(plan["changes"][0]["desired_notes"], "#amazon #refund | Memo: Bought for guest")
        self.assertEqual(plan["changes"][0]["expected_current_amount"], 355)
        self.assertEqual(plan["amount_mutation_count"], 0)
        self.assertEqual(plan["conflicts"][0]["reason"], "AMOUNT_OR_SIGN_DRIFT")
        self.assertEqual(audit["amount_mutation_count"], 0)
        self.assertEqual(validate_guarded_migration_plan(snapshot, plan)["status"], "PASS")

        snapshot["transactions"][0]["notes"] = "concurrent manual edit"
        with self.assertRaisesRegex(ValueError, "guard drift"):
            validate_guarded_migration_plan(snapshot, plan)

    def test_manual_category_is_preserved_and_reported(self):
        _, decisions = regenerate_manifest(self._manifest())
        desired = {row["imported_id"]: row for row in decisions}
        snapshot = {"transactions": [{
            "imported_id": "ei:refund", "account_name": "EI", "date": "2026-07-12",
            "amount": 355, "category_name": "Guest reimbursement", "notes": "#amazon",
        }]}
        plan, _ = build_guarded_migration_plan(snapshot, desired)
        self.assertEqual(plan["changes"][0]["desired_category_name"], "Guest reimbursement")
        self.assertEqual(plan["conflicts"][0]["reason"], "MANUAL_CATEGORY_PRESERVED")


if __name__ == "__main__":
    unittest.main()
