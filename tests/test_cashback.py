from datetime import date, datetime
from decimal import Decimal
from typing import cast
from unittest import TestCase

from finance_tracker.actual_snapshot import cashback_dashboard
from finance_tracker.cashback import (
    PaymentIntent,
    _refund_cashback_deduction,
    bucket_spend,
    configured_reward_bucket,
    evaluate_card,
    pace_status,
    poc_programs,
    programs_from_config,
    recommend,
    reward_total,
    statement_period,
    total_spend,
)
from finance_tracker.models import Transaction


class CashbackTests(TestCase):
    def test_statement_period_supports_card_specific_close_day(self) -> None:
        self.assertEqual(
            statement_period(date(2026, 8, 16), 15),
            (date(2026, 8, 16), date(2026, 9, 15)),
        )
        self.assertEqual(
            statement_period(date(2026, 8, 15), 15),
            (date(2026, 7, 16), date(2026, 8, 15)),
        )

    def test_empty_period_routes_match_portfolio_strategy(self) -> None:
        programs = poc_programs()
        cases = (
            (
                PaymentIntent("GROCERY", Decimal("100"), "AED", "PHYSICAL_POS"),
                "RAK_WORLD",
            ),
            (
                PaymentIntent("DINING", Decimal("100"), "AED", "PHYSICAL_POS"),
                "RAK_WORLD",
            ),
            (
                PaymentIntent("TRAVEL", Decimal("100"), "AED", "PHYSICAL_POS"),
                "RAK_WORLD",
            ),
            (
                PaymentIntent("GENERAL", Decimal("100"), "AED", "ONLINE"),
                "SC_PLATINUM_X",
            ),
            (
                PaymentIntent("GENERAL", Decimal("100"), "AED", "APPLE_PAY_POS"),
                "SC_PLATINUM_X",
            ),
            (
                PaymentIntent("GENERAL", Decimal("100"), "USD", "ONLINE"),
                "SC_PLATINUM_X",
            ),
            (PaymentIntent("AMAZON", Decimal("100"), "AED", "ONLINE"), "SC_PLATINUM_X"),
        )
        for intent, expected_card in cases:
            with self.subTest(intent=intent):
                self.assertEqual(
                    recommend(programs, [], intent).primary_card, expected_card
                )

    def test_sc_tier_jump_values_existing_spend(self) -> None:
        transactions = [
            Transaction(
                "o",
                datetime(2026, 8, 1),
                "SC_PLATINUM_X",
                "Online",
                "4000",
                channel="ONLINE",
                category="GENERAL",
                reward_bucket="SC_ONLINE",
            ),
            Transaction(
                "w",
                datetime(2026, 8, 2),
                "SC_PLATINUM_X",
                "Wallet",
                "2000",
                channel="APPLE_PAY_POS",
                category="GENERAL",
                reward_bucket="SC_WALLET",
            ),
            Transaction(
                "f",
                datetime(2026, 8, 3),
                "SC_PLATINUM_X",
                "Foreign",
                "4000",
                currency="USD",
                category="GENERAL",
                reward_bucket="SC_FOREIGN",
            ),
            Transaction(
                "x",
                datetime(2026, 8, 4),
                "SC_PLATINUM_X",
                "Other online",
                "3000",
                channel="ONLINE",
                category="GENERAL",
                reward_bucket="SC_ONLINE",
            ),
        ]
        result = recommend(
            poc_programs(),
            transactions,
            PaymentIntent("GENERAL", Decimal("2000"), "AED", "ONLINE"),
        )
        sc = next(value for value in result.ranked if value.card == "SC_PLATINUM_X")
        self.assertEqual(sc.tier_before, "TIER_5")
        self.assertEqual(sc.tier_after, "TIER_10")
        self.assertGreater(sc.marginal_reward_aed, Decimal("300"))
        self.assertEqual(result.primary_card, "SC_PLATINUM_X")
        self.assertEqual(result.urgency, "PREFER_NOW")
        self.assertIn("Standard Chartered Platinum X", result.guidance)

    def test_refund_reduces_reward(self) -> None:
        program = next(
            program for program in poc_programs() if program.card == "SC_PLATINUM_X"
        )
        before = reward_total(program, Decimal("15000"), {"SC_ONLINE": Decimal("4000")})
        after = reward_total(
            program,
            Decimal("15000"),
            {"SC_ONLINE": Decimal("4000")},
            refund_deductions=Decimal("10"),
        )
        self.assertEqual(before, Decimal("400.00"))
        self.assertEqual(after, Decimal("390.00"))
        self.assertLess(after, before)

    def test_reversal_preserves_qualifying_spend_and_cap_headroom(self) -> None:
        rows = [
            Transaction(
                "purchase",
                datetime(2026, 8, 1),
                "SC_PLATINUM_X",
                "Online",
                "2500",
                category="GENERAL",
                reward_bucket="SC_ONLINE",
            ),
            Transaction(
                "reversal",
                datetime(2026, 8, 2),
                "SC_PLATINUM_X",
                "Online purchase reversed",
                "25",
                transaction_type="REVERSAL",
                category="GENERAL",
                reward_bucket="SC_ONLINE",
            ),
        ]

        self.assertEqual(total_spend(rows, "SC_PLATINUM_X"), Decimal("2500"))
        self.assertEqual(
            bucket_spend(rows, "SC_PLATINUM_X"), {"SC_ONLINE": Decimal("2500")}
        )

    def test_unresolved_rows_are_excluded_but_resolved_general_counts(self) -> None:
        rows = [
            Transaction(
                "resolved",
                datetime(2026, 8, 1),
                "SC_PLATINUM_X",
                "Online",
                "100",
                category="GENERAL",
                reward_bucket="SC_ONLINE",
            ),
            Transaction(
                "pending",
                datetime(2026, 8, 2),
                "SC_PLATINUM_X",
                "Online",
                "200",
                category="GENERAL",
                reward_bucket="SC_ONLINE",
                review_required=True,
            ),
        ]

        self.assertEqual(total_spend(rows, "SC_PLATINUM_X"), Decimal("100"))
        self.assertEqual(
            bucket_spend(rows, "SC_PLATINUM_X"), {"SC_ONLINE": Decimal("100")}
        )

    def test_reimbursement_never_deducts_cashback(self) -> None:
        program = next(
            program for program in poc_programs() if program.card == "SC_PLATINUM_X"
        )
        purchase = Transaction(
            "purchase",
            datetime(2026, 8, 1),
            "SC_PLATINUM_X",
            "Online",
            "1000",
            category="GENERAL",
            reward_bucket="SC_ONLINE",
        )
        reimbursement = Transaction(
            "reimbursement",
            datetime(2026, 8, 2),
            "SC_PLATINUM_X",
            "Employer reimbursement",
            "100",
            transaction_type="REIMBURSEMENT",
            is_refund=True,
            category="Refunds & Reimbursements",
            reward_bucket="SC_ONLINE",
            metadata={"original_transaction_id": "purchase"},
        )
        rows = [purchase, reimbursement]
        total = total_spend(rows, program.card)
        buckets = bucket_spend(rows, program.card)

        self.assertEqual(
            _refund_cashback_deduction(program, rows, total, buckets), Decimal("0")
        )

    def test_refund_deduction_is_bucket_scoped_and_replay_safe(self) -> None:
        program = next(
            program for program in poc_programs() if program.card == "SC_PLATINUM_X"
        )
        rows = [
            Transaction(
                "purchase",
                datetime(2026, 8, 1),
                "SC_PLATINUM_X",
                "Online",
                "15000",
                category="GENERAL",
                reward_bucket="SC_ONLINE",
            ),
            Transaction(
                "refund",
                datetime(2026, 8, 2),
                "SC_PLATINUM_X",
                "Online refund",
                "100",
                transaction_type="REFUND",
                category="GENERAL",
                reward_bucket="SC_ONLINE",
            ),
            Transaction(
                "refund",
                datetime(2026, 8, 2),
                "SC_PLATINUM_X",
                "Online refund replay",
                "100",
                transaction_type="REFUND",
                category="GENERAL",
                reward_bucket="SC_ONLINE",
            ),
        ]
        total = total_spend(rows, program.card)
        buckets = bucket_spend(rows, program.card)

        self.assertEqual(
            _refund_cashback_deduction(program, rows, total, buckets), Decimal("10")
        )
        self.assertEqual(
            reward_total(program, total, buckets, refund_deductions=Decimal("10")),
            Decimal("390.00"),
        )

    def test_capped_refund_never_restores_cap_or_creates_negative_reward(self) -> None:
        program = next(
            program for program in poc_programs() if program.card == "SC_PLATINUM_X"
        )
        rows = [
            Transaction(
                "purchase",
                datetime(2026, 8, 1),
                "SC_PLATINUM_X",
                "Online",
                "15000",
                category="GENERAL",
                reward_bucket="SC_ONLINE",
            ),
            Transaction(
                "unmatched-refund",
                datetime(2026, 8, 2),
                "SC_PLATINUM_X",
                "Unmatched online refund",
                "5000",
                transaction_type="REFUND",
                category="GENERAL",
                reward_bucket="SC_ONLINE",
            ),
        ]
        total = total_spend(rows, program.card)
        buckets = bucket_spend(rows, program.card)

        self.assertEqual(buckets["SC_ONLINE"], Decimal("15000"))
        self.assertEqual(
            _refund_cashback_deduction(program, rows, total, buckets), Decimal("400")
        )
        self.assertEqual(
            reward_total(program, total, buckets, refund_deductions=Decimal("400")),
            Decimal("0.00"),
        )

    def test_sc_refund_deduction_stays_at_refund_tier_after_later_purchase(
        self,
    ) -> None:
        program = next(
            program for program in poc_programs() if program.card == "SC_PLATINUM_X"
        )
        rows = [
            Transaction(
                "online-7400",
                datetime(2026, 8, 1),
                "SC_PLATINUM_X",
                "Online purchase",
                "7400",
                channel="ONLINE",
                category="GENERAL",
                reward_bucket="SC_ONLINE",
            ),
            Transaction(
                "online-refund-100",
                datetime(2026, 8, 2),
                "SC_PLATINUM_X",
                "Online refund",
                "100",
                transaction_type="REFUND",
                channel="ONLINE",
                category="GENERAL",
                reward_bucket="SC_ONLINE",
            ),
        ]
        total = total_spend(rows, program.card)
        buckets = bucket_spend(rows, program.card)

        self.assertEqual(
            _refund_cashback_deduction(program, rows, total, buckets),
            Decimal("3"),
        )
        evaluation = evaluate_card(
            program,
            rows,
            PaymentIntent("GENERAL", Decimal("100"), "AED", "ONLINE"),
        )
        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        self.assertEqual(
            reward_total(program, total, buckets, refund_deductions=Decimal("3"))
            + evaluation.marginal_reward_aed,
            Decimal("197.00"),
        )

        later_rows = rows + [
            Transaction(
                "online-100",
                datetime(2026, 8, 3),
                "SC_PLATINUM_X",
                "Later online purchase",
                "100",
                channel="ONLINE",
                category="GENERAL",
                reward_bucket="SC_ONLINE",
            )
        ]
        later_total = total_spend(later_rows, program.card)
        later_buckets = bucket_spend(later_rows, program.card)
        later_deduction = _refund_cashback_deduction(
            program,
            later_rows,
            later_total,
            later_buckets,
        )
        self.assertEqual(later_deduction, Decimal("3"))
        self.assertEqual(
            reward_total(
                program,
                later_total,
                later_buckets,
                refund_deductions=later_deduction,
            ),
            Decimal("197.00"),
        )
        dashboard = cashback_dashboard(
            [program],
            later_rows,
            date(2026, 8, 4),
            [],
        )
        dashboard_card = cast(list[dict[str, object]], dashboard["cards"])[0]
        self.assertEqual(dashboard_card["expected_cashback_aed"], "197.00")

    def test_rak_rewards_start_at_qualifying_threshold_not_safety_buffer(self) -> None:
        program = next(
            program for program in poc_programs() if program.card == "RAK_WORLD"
        )

        self.assertEqual(
            reward_total(
                program, Decimal("9999.99"), {"RAK_GROCERY": Decimal("9999.99")}
            ),
            Decimal("0.00"),
        )
        self.assertEqual(
            reward_total(program, Decimal("10000"), {"RAK_GROCERY": Decimal("10000")}),
            Decimal("300.00"),
        )
        self.assertEqual(
            program.tier_for(Decimal("10000"), {"RAK_GROCERY": Decimal("10000")}).code,
            "QUALIFYING",
        )
        self.assertEqual(program.safety_target, Decimal("10300"))

    def test_uncertain_category_uses_basic_bucket_with_review(self) -> None:
        self.assertEqual(
            configured_reward_bucket(
                poc_programs(), "RAK_WORLD", "UNKNOWN", "UNKNOWN", "AED"
            ),
            "RAK_STANDARD",
        )

    def test_rak_cashback_is_zero_below_monthly_minimum(self) -> None:
        program = next(
            program for program in poc_programs() if program.card == "RAK_WORLD"
        )
        self.assertEqual(
            reward_total(program, Decimal("57.49"), {"RAK_STANDARD": Decimal("41.49")}),
            Decimal("0.00"),
        )

    def test_sc_uses_tier_specific_bucket_caps(self) -> None:
        program = next(
            program for program in poc_programs() if program.card == "SC_PLATINUM_X"
        )
        self.assertEqual(
            reward_total(program, Decimal("5000"), {"SC_WALLET": Decimal("4000")}),
            Decimal("100.00"),
        )
        self.assertEqual(
            reward_total(program, Decimal("10000"), {"SC_WALLET": Decimal("4000")}),
            Decimal("200.00"),
        )
        self.assertEqual(
            reward_total(program, Decimal("15000"), {"SC_WALLET": Decimal("4000")}),
            Decimal("200.00"),
        )
        self.assertEqual(
            reward_total(program, Decimal("15000"), {"SC_ONLINE": Decimal("4000")}),
            Decimal("400.00"),
        )

    def test_amazon_overflow_returns_to_ei_after_sc_online_is_full(self) -> None:
        transactions = [
            Transaction(
                "o",
                datetime(2026, 8, 1),
                "SC_PLATINUM_X",
                "Online",
                "4000",
                channel="ONLINE",
                category="GENERAL",
                reward_bucket="SC_ONLINE",
            ),
            Transaction(
                "w",
                datetime(2026, 8, 2),
                "SC_PLATINUM_X",
                "Wallet",
                "2000",
                channel="APPLE_PAY_POS",
                category="GENERAL",
                reward_bucket="SC_WALLET",
            ),
            Transaction(
                "f",
                datetime(2026, 8, 3),
                "SC_PLATINUM_X",
                "Foreign",
                "4000",
                currency="USD",
                category="GENERAL",
                reward_bucket="SC_FOREIGN",
            ),
            Transaction(
                "x",
                datetime(2026, 8, 4),
                "SC_PLATINUM_X",
                "Filler",
                "5000",
                channel="ONLINE",
                category="GENERAL",
                reward_bucket="SC_ONLINE",
            ),
        ]
        result = recommend(
            poc_programs(),
            transactions,
            PaymentIntent("AMAZON", Decimal("100"), "AED", "ONLINE"),
        )
        self.assertEqual(result.primary_card, "EI_AMAZON")

    def test_pace_status(self) -> None:
        status = pace_status(Decimal("3000"), Decimal("10300"), date(2026, 8, 21))
        self.assertEqual(status.status, "UNDER")

    def test_program_version_is_selected_by_period_without_back_application(
        self,
    ) -> None:
        source = {
            "programs": [
                {
                    "card": "CARD",
                    "name": "Old",
                    "programme_version": "v1",
                    "effective_start": "2026-01-01",
                    "effective_end": "2026-06-30",
                    "tiers": [
                        {
                            "code": "BASE",
                            "minimum_spend_aed": "0",
                            "rates": {"B": "0.01"},
                        }
                    ],
                    "buckets": [{"code": "B", "cashback_cap_aed": "10"}],
                },
                {
                    "card": "CARD",
                    "name": "New",
                    "programme_version": "v2",
                    "effective_start": "2026-07-01",
                    "effective_end": None,
                    "tiers": [
                        {
                            "code": "BASE",
                            "minimum_spend_aed": "0",
                            "rates": {"B": "0.02"},
                        }
                    ],
                    "buckets": [{"code": "B", "cashback_cap_aed": "20"}],
                },
            ]
        }

        old = programs_from_config(source, date(2026, 6, 1))[0]
        new = programs_from_config(source, date(2026, 7, 1))[0]

        self.assertEqual(old.programme_version, "v1")
        self.assertEqual(new.programme_version, "v2")
        with self.assertRaisesRegex(ValueError, "no active programs"):
            programs_from_config(source, date(2025, 12, 31))
