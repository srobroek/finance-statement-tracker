from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from unittest import TestCase

from finance_tracker.actual_pipeline import (
    build_actual_statement_run,
    load_actual_config,
)
from finance_tracker.statements import (
    NormalizedStatement,
    NormalizedStatementTransaction,
)

ROOT = Path(__file__).resolve().parent.parent


class ActualPipelineClassificationTests(TestCase):
    def test_unidentified_credit_uses_configured_review_category(self) -> None:
        statement = NormalizedStatement(
            bank="Example Bank",
            adapter="test",
            source_file="unidentified-credit.pdf",
            statement_date=date(2026, 8, 31),
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            payment_due_date=None,
            opening_balance_aed=Decimal(100),
            closing_balance_aed=Decimal("96.45"),
            minimum_payment_aed=None,
            total_payment_due_aed=None,
            card_last4s=("0082",),
            transactions=(
                NormalizedStatementTransaction(
                    transaction_id="unidentified-credit",
                    transaction_date=date(2026, 8, 12),
                    post_date=None,
                    card_last4="0082",
                    description="EXAMPLE MERCHANT",
                    amount_aed=Decimal("3.55"),
                    direction="CREDIT",
                    transaction_type="PURCHASE",
                ),
            ),
        )
        config = load_actual_config(ROOT / "config" / "actual-bootstrap.json")

        run = build_actual_statement_run(statement, config)

        configured_categories = {
            category
            for group in config["category_groups"]
            for category in group["categories"]
        }
        record = cast(dict[str, Any], run.envelopes[0]["records"][0])
        self.assertEqual(record["category_name"], "Needs Review")
        self.assertIn(record["category_name"], configured_categories)
        self.assertIn("#needs-review", record["notes"])
        self.assertEqual(run.review_count, 1)
