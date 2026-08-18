from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Mapping

from .models import Transaction
from .statements import NormalizedStatement


@dataclass(frozen=True, slots=True)
class StatementStageBatch:
    """A portable staging batch; it never implies ledger reconciliation."""

    statement: NormalizedStatement
    transactions: tuple[Transaction, ...]
    status: str
    balance_tied: bool
    ledger_reconciled: bool = False

    @property
    def review_count(self) -> int:
        return sum(transaction.review_required for transaction in self.transactions)


def stage_statement(
    statement: NormalizedStatement,
    card_by_last4: Mapping[str, str],
    source_message_id: str | None = None,
) -> StatementStageBatch:
    """Map a normalized statement to staged transactions using repository account config."""
    staged: list[Transaction] = []
    for row in statement.transactions:
        configured_card = card_by_last4.get(row.card_last4 or "")
        review_required = row.review_required or configured_card is None or not statement.balance_tied
        staged.append(
            Transaction(
                transaction_id=f"statement:{statement.adapter}:{row.transaction_id}",
                transaction_at=datetime.combine(row.transaction_date, time.min),
                card=configured_card or "UNMAPPED_CARD",
                institution=statement.bank,
                account_last4=row.card_last4,
                merchant_raw=row.description,
                amount_aed=row.amount_aed,
                currency=row.currency_original,
                amount_original=row.amount_original,
                source_type="statement",
                source_message_id=source_message_id,
                transaction_type=row.transaction_type,
                review_required=review_required,
                is_refund=row.transaction_type == "REFUND",
                metadata={
                    "import_status": "STAGED",
                    "statement_adapter": statement.adapter,
                    "statement_source_file": statement.source_file,
                    "statement_transaction_id": row.transaction_id,
                    "statement_card_last4": row.card_last4,
                    "statement_direction": row.direction,
                    "account_balance_convention": "LIABILITY",
                    "statement_post_date": row.post_date.isoformat() if row.post_date else None,
                    "statement_exchange_rate": None if row.exchange_rate is None else str(row.exchange_rate),
                    "statement_balance_tied": statement.balance_tied,
                    "ledger_reconciled": False,
                    "locked_fields": [
                        "transaction_id",
                        "amount_aed",
                        "amount_original",
                        "source_message_id",
                    ],
                },
            )
        )
    status = (
        "READY_FOR_LEDGER_MATCH"
        if statement.balance_tied and not any(row.review_required for row in staged)
        else "REVIEW_REQUIRED"
    )
    return StatementStageBatch(
        statement=statement,
        transactions=tuple(staged),
        status=status,
        balance_tied=statement.balance_tied,
    )
