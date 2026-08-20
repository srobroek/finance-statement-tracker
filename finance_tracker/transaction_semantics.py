from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from .models import Transaction


SOURCE_DIRECTIONS = frozenset({"CREDIT", "DEBIT"})
REFUND_TOPICS = frozenset({"REFUND", "REVERSAL"})
EXPLICIT_CREDIT_TOPICS = frozenset(
    {"INCOME", "INVESTMENT", "PAYMENT", "REWARD_CREDIT", "TRANSFER", *REFUND_TOPICS}
)


@dataclass(frozen=True, slots=True)
class TopicSemantics:
    """Economic contract for one finalized topic.

    ``actual_sign`` is the default Actual account-side sign when no source
    direction is available. Transfers and investments deliberately have no
    default because guessing their direction would create a balancing error.
    ``spend_factor`` is the contribution to consumption reporting: refunds and
    reversals reduce spend, while transfers, rewards, income, and investments
    do not represent spend.
    """

    allowed_directions: frozenset[str]
    actual_sign: int | None
    spend_factor: int
    cashback_eligible: bool = False
    topic_tag: str | None = None


TOPIC_SEMANTICS: dict[str, TopicSemantics] = {
    "PURCHASE": TopicSemantics(frozenset({"DEBIT"}), -1, 1, True),
    "REFUND": TopicSemantics(frozenset({"CREDIT"}), 1, -1, True, "refund"),
    "REVERSAL": TopicSemantics(frozenset({"CREDIT"}), 1, -1, True, "reversal"),
    "REWARD_CREDIT": TopicSemantics(frozenset({"CREDIT"}), 1, 0, False, "reward"),
    "INCOME": TopicSemantics(frozenset({"CREDIT"}), 1, 0, False, "income"),
    "TRANSFER": TopicSemantics(frozenset(SOURCE_DIRECTIONS), None, 0, False, "transfer"),
    "FEE": TopicSemantics(frozenset({"DEBIT"}), -1, 1, False, "fee"),
    "INTEREST": TopicSemantics(frozenset({"DEBIT"}), -1, 1, False, "interest"),
    "INVESTMENT": TopicSemantics(
        frozenset(SOURCE_DIRECTIONS), None, 0, False, "investment"
    ),
    # Browser acquisition uses this provisional topic until source evidence or
    # a normalization rule resolves it. It must never count as spend.
    "UNRESOLVED_CREDIT": TopicSemantics(frozenset({"CREDIT"}), 1, 0),
    # Provisional source labels are valid before normalization/finalization.
    "CREDIT": TopicSemantics(frozenset({"CREDIT"}), 1, 0),
    "PAYMENT": TopicSemantics(frozenset(SOURCE_DIRECTIONS), None, 0),
}
CASHBACK_TOPICS = frozenset(
    topic for topic, semantics in TOPIC_SEMANTICS.items() if semantics.cashback_eligible
)
TOPIC_BY_TAG = {
    semantics.topic_tag: topic
    for topic, semantics in TOPIC_SEMANTICS.items()
    if semantics.topic_tag is not None
}


def topic_semantics(topic: str) -> TopicSemantics:
    normalized = str(topic or "").strip().upper()
    try:
        return TOPIC_SEMANTICS[normalized]
    except KeyError as error:
        raise ValueError(f"Unsupported transaction topic: {normalized or '<empty>'}") from error


def spend_amount(transaction: Transaction) -> Decimal:
    """Return consumption impact while retaining refunds as negative spend."""

    topic = (
        "REFUND"
        if transaction.is_refund
        else str(transaction.transaction_type or "PURCHASE").upper()
    )
    semantics = topic_semantics(topic)
    return abs(transaction.amount_aed) * semantics.spend_factor


def actual_amount_minor(transaction: Transaction) -> int:
    """Project one canonical row to Actual's signed integer minor units.

    Source direction controls ordinary rows. Card-payment rows are the one
    issuer-export exception: their human description and account convention
    identify the account-side transfer even when the issuer labels direction
    from liability accounting perspective. No other transfer or investment row
    receives an inferred sign.
    """

    if transaction.amount_aed < 0:
        raise ValueError("Canonical amount_aed must be a non-negative magnitude")
    topic = (
        "REFUND"
        if transaction.is_refund and str(transaction.transaction_type).upper() == "PURCHASE"
        else str(transaction.transaction_type or "PURCHASE").strip().upper()
    )
    semantics = topic_semantics(topic)
    direction = _source_direction(transaction)
    description = " ".join(transaction.merchant_raw.upper().split())
    tags = {str(tag).strip().casefold() for tag in transaction.tags}
    convention = str(transaction.metadata.get("account_balance_convention") or "").strip().upper()
    card_payment = "card-payment" in tags
    payment_description = any(
        token in description
        for token in ("PAYMENT RECEIVED", "CREDIT REPAYMENT", "CARD REPAYMENT")
    )
    if card_payment and payment_description and convention in {"ASSET", "LIABILITY"}:
        positive = convention == "LIABILITY"
    elif direction:
        positive = direction == "CREDIT"
    elif semantics.actual_sign is not None:
        positive = semantics.actual_sign > 0
    else:
        raise ValueError(
            f"{topic} transaction {transaction.transaction_id!r} requires source direction"
        )
    units = (abs(transaction.amount_aed) * Decimal("100")).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return int(units if positive else -units)


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
    elif topic == "PAYMENT" or (
        topic == "PURCHASE"
        and any(
            token in description
            for token in ("PAYMENT RECEIVED", "CARD PAYMENT", "CARD PMT", "AUTOPAY PAYMENT")
        )
    ):
        topic = "TRANSFER"
        reason = "EXPLICIT_CARD_PAYMENT"
        transaction.tags.update({"transfer", "card-payment"})
    elif topic == "PURCHASE" and (
        str(transaction.vendor or "").strip().casefold() == "stake"
        or "GETSTAKE.COM" in description
        or " INVESTMENT " in f" {description} "
    ):
        topic = "INVESTMENT"
        reason = "EXPLICIT_INVESTMENT_EVIDENCE"
    elif direction == "CREDIT" and topic not in EXPLICIT_CREDIT_TOPICS:
        topic = "REFUND"
        reason = "CREDIT_DEFAULT_REFUND"
    elif direction == "DEBIT" and topic in {
        "CREDIT",
        "INCOME",
        "REFUND",
        "REVERSAL",
        "REWARD_CREDIT",
    }:
        topic = "PURCHASE"
        reason = "DEBIT_DEFAULT_PURCHASE"
    elif direction == "CREDIT" and topic in {"FEE", "INTEREST"}:
        topic = "REFUND"
        reason = "CREDIT_DEFAULT_REFUND"
    elif topic == "PURCHASE" and "INTEREST" in description:
        topic = "INTEREST"
        reason = "EXPLICIT_INTEREST"
    elif topic == "PURCHASE" and (
        "FOREIGN EXCHANGE FEE" in description
        or description.startswith("VAT ON ")
        or description.endswith((" FEE", " CHARGE", " CHARGES"))
    ):
        topic = "FEE"
        reason = "EXPLICIT_FEE"

    semantics = topic_semantics(topic)
    if direction and direction not in semantics.allowed_directions:
        raise ValueError(
            f"Topic {topic} is incompatible with source direction {direction}"
        )

    transaction.transaction_type = topic
    transaction.is_refund = topic in REFUND_TOPICS
    if transaction.is_refund:
        transaction.tags.add(semantics.topic_tag or "refund")
        if topic == "REVERSAL":
            # Keep legacy refund economics while persisting the canonical topic.
            transaction.tags.add("refund")
        transaction.tags.discard("reward")
    elif topic == "REWARD_CREDIT":
        transaction.tags.add("reward")
        transaction.tags.discard("refund")
    elif semantics.topic_tag:
        transaction.tags.add(semantics.topic_tag)
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
