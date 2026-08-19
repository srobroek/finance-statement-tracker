from __future__ import annotations

from .models import Transaction


SOURCE_DIRECTIONS = frozenset({"CREDIT", "DEBIT"})
REFUND_TOPICS = frozenset({"REFUND", "REVERSAL"})
EXPLICIT_CREDIT_TOPICS = frozenset(
    {"INCOME", "PAYMENT", "REWARD_CREDIT", "TRANSFER", *REFUND_TOPICS}
)


def _source_direction(transaction: Transaction) -> str | None:
    candidates = {
        str(value).strip().upper()
        for value in (
            transaction.source_direction,
            transaction.metadata.get("source_direction"),
            transaction.metadata.get("statement_direction"),
            transaction.metadata.get("browser_direction"),
        )
        if str(value or "").strip()
    }
    invalid = candidates - SOURCE_DIRECTIONS
    if invalid:
        raise ValueError("Invalid source direction: " + ", ".join(sorted(invalid)))
    if len(candidates) > 1:
        raise ValueError("Conflicting source directions: " + ", ".join(sorted(candidates)))
    return next(iter(candidates), None)


def finalize_transaction_topic(transaction: Transaction) -> str:
    """Resolve and lock the canonical transaction topic.

    Source direction is immutable economic evidence. Static normalization rules
    may identify an explicit transfer or reward before this function runs, but
    ordinary positive merchant credits default to refunds. Later static,
    history, and AI stages cannot change the finalized topic or source amount.
    """

    if transaction.amount_aed < 0:
        raise ValueError("Canonical amount_aed must be a non-negative magnitude")
    direction = _source_direction(transaction)
    transaction.source_direction = direction
    if direction:
        transaction.metadata["source_direction"] = direction

    description = " ".join(transaction.merchant_raw.upper().split())
    topic = str(transaction.transaction_type or "PURCHASE").strip().upper()
    reason = "SOURCE_TOPIC"

    if direction == "CREDIT" and any(
        token in description for token in ("REVERSED", "REVERSAL")
    ):
        topic = "REVERSAL"
        reason = "EXPLICIT_REVERSAL"
    elif direction == "CREDIT" and topic not in EXPLICIT_CREDIT_TOPICS:
        topic = "REFUND"
        reason = "CREDIT_DEFAULT_REFUND"
    elif direction == "DEBIT" and topic == "CREDIT":
        topic = "PURCHASE"
        reason = "DEBIT_DEFAULT_PURCHASE"
    elif topic == "PURCHASE" and (
        "FOREIGN EXCHANGE FEE" in description
        or description.startswith("VAT ON ")
        or description.endswith(" FEE")
    ):
        topic = "FEE"
        reason = "EXPLICIT_FEE"
    elif topic == "PURCHASE" and "INTEREST" in description:
        topic = "INTEREST"
        reason = "EXPLICIT_INTEREST"

    transaction.transaction_type = topic
    transaction.is_refund = topic in REFUND_TOPICS
    if transaction.is_refund:
        transaction.tags.add("refund")
        transaction.tags.discard("reward")
    elif topic == "REWARD_CREDIT":
        transaction.tags.add("reward")
        transaction.tags.discard("refund")

    locked = set(transaction.metadata.get("locked_fields", []))
    locked.update(
        {
            "amount_aed",
            "amount_original",
            "source_direction",
            "transaction_type",
            "is_refund",
        }
    )
    transaction.metadata["locked_fields"] = sorted(locked)
    transaction.metadata["transaction_topic_reason"] = reason
    return topic
