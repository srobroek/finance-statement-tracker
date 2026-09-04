from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Iterable, Protocol

from .ai_rules import AIEnrichmentEngine
from .history import HistoryDecision, apply_history_match
from .models import Transaction
from .rules import RuleEngine, StaticRule
from .cashback import (
    channel_from_config,
    configured_reward_bucket,
    load_program_configuration,
    programs_from_config,
    purchase_type_from_config,
)


_ADCB_OTP = re.compile(
    r"OTP\s+for\s+transaction\s+at\s+(?P<merchant>.+?)\s+for\s+"
    r"(?P<currency>[A-Z]{3})\s+(?P<amount>[0-9,]+(?:\.[0-9]{1,2})?)\s+"
    r"on\s+your\s+ADCB\s+Credit\s+Card\s+XXX(?P<last4>[0-9]{4})",
    re.IGNORECASE,
)

_RAKBANK_CARD_TRANSACTION = re.compile(
    r"You\s+spent\s+(?P<currency>[A-Z]{3})\s+"
    r"(?P<amount>[0-9,]+(?:\.[0-9]{1,2})?)\s+at\s+"
    r"(?P<merchant>.+?)\s+on\s+your\s+Credit\s+Card\s+"
    r"[0-9*\s]*?(?P<last4>[0-9]{4})\s+on\s+"
    r"(?P<day>[0-9]{1,2})/(?P<month>[0-9]{1,2})(?:\.|\s|$)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class NotificationFact:
    adapter: str
    institution: str
    merchant: str
    amount: Decimal
    currency: str
    card_last4: str
    occurred_at: datetime
    channel: str
    confidence: float
    requires_review: bool


@dataclass(frozen=True, slots=True)
class NotificationBatch:
    events: tuple[dict[str, Any], ...]
    scanned_count: int
    accepted_count: int
    skipped: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class NotificationAdapter(Protocol):
    code: str

    def detect(self, message: dict[str, Any]) -> bool: ...

    def parse(self, message: dict[str, Any]) -> NotificationFact: ...


def _sender_address(message: dict[str, Any]) -> str:
    sender = message.get("sender") or {}
    if isinstance(sender, dict):
        email = sender.get("emailAddress") or {}
        if isinstance(email, dict):
            return str(email.get("address") or "").strip().casefold()
    return ""


def _message_text(message: dict[str, Any]) -> str:
    parts = [str(message.get("bodyPreview") or "")]
    body = message.get("body")
    if isinstance(body, dict):
        parts.append(str(body.get("content") or ""))
    elif body:
        parts.append(str(body))
    return "\n".join(parts)


def _received_datetime(message: dict[str, Any]) -> datetime:
    raw = str(message.get("receivedDateTime") or "").strip()
    if not raw:
        raise ValueError("Outlook receivedDateTime is required")
    try:
        received = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Outlook receivedDateTime must be an ISO timestamp") from error
    if received.tzinfo is None:
        raise ValueError("Outlook receivedDateTime must include a timezone")
    return received


class ADCBOTPNotificationAdapter:
    """Parse amount-bearing ADCB authorization emails conservatively.

    These messages prove an authorization attempt, not settlement. They are
    therefore live operational evidence until a statement reconciles
    them.
    """

    code = "adcb_card_otp_v1"
    senders = frozenset({"adcbalert@adcb.com"})
    subjects = frozenset({"adcb card transaction otp generated"})

    def detect(self, message: dict[str, Any]) -> bool:
        return (
            _sender_address(message) in self.senders
            and str(message.get("subject") or "").strip().casefold() in self.subjects
        )

    def parse(self, message: dict[str, Any]) -> NotificationFact:
        match = _ADCB_OTP.search(_message_text(message))
        if not match:
            raise ValueError("ADCB authorization email does not expose merchant, amount, currency, and card suffix")
        occurred = _received_datetime(message)
        return NotificationFact(
            adapter=self.code,
            institution="ADCB",
            merchant=match.group("merchant").strip(),
            amount=Decimal(match.group("amount").replace(",", "")),
            currency=match.group("currency").upper(),
            card_last4=match.group("last4"),
            occurred_at=occurred,
            channel="ONLINE",
            confidence=0.75,
            requires_review=True,
        )


def _resolve_notification_date(day: int, month: int, received: datetime) -> datetime:
    candidates: list[datetime] = []
    for year in (received.year - 1, received.year, received.year + 1):
        try:
            candidates.append(datetime(year, month, day, tzinfo=received.tzinfo))
        except ValueError:
            continue
    if not candidates:
        raise ValueError("RAKBANK transaction date is invalid")
    occurred = min(candidates, key=lambda candidate: abs(candidate - received))
    if abs((occurred.date() - received.date()).days) > 7:
        raise ValueError("RAKBANK transaction date is not close to the email receipt date")
    return occurred


class RakbankCardTransactionNotificationAdapter:
    """Parse RAKBANK card-spend notifications as live operational evidence."""

    code = "rakbank_card_transaction_v1"
    senders = frozenset({"alerts@rakbank.ae"})
    subjects = frozenset({"an update on your card transaction"})

    def detect(self, message: dict[str, Any]) -> bool:
        return (
            _sender_address(message) in self.senders
            and str(message.get("subject") or "").strip().casefold() in self.subjects
        )

    def parse(self, message: dict[str, Any]) -> NotificationFact:
        match = _RAKBANK_CARD_TRANSACTION.search(_message_text(message))
        if not match:
            raise ValueError(
                "RAKBANK transaction email does not expose merchant, amount, currency, card suffix, and date"
            )
        received = _received_datetime(message)
        occurred = _resolve_notification_date(
            int(match.group("day")),
            int(match.group("month")),
            received,
        )
        return NotificationFact(
            adapter=self.code,
            institution="RAKBANK",
            merchant=" ".join(match.group("merchant").split()),
            amount=Decimal(match.group("amount").replace(",", "")),
            currency=match.group("currency").upper(),
            card_last4=match.group("last4"),
            occurred_at=occurred,
            channel="UNKNOWN",
            confidence=0.95,
            requires_review=False,
        )


DEFAULT_NOTIFICATION_ADAPTERS: tuple[NotificationAdapter, ...] = (
    ADCBOTPNotificationAdapter(),
    RakbankCardTransactionNotificationAdapter(),
)


def parse_outlook_notifications(
    messages: Iterable[dict[str, Any]],
    card_by_last4: dict[str, str],
    rules: Iterable[StaticRule] = (),
    *,
    adapters: Iterable[NotificationAdapter] = DEFAULT_NOTIFICATION_ADAPTERS,
    history_index: dict[str, HistoryDecision] | None = None,
    ai_engine: AIEnrichmentEngine | None = None,
    ai_resolver: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    cashback_config: dict[str, Any] | None = None,
) -> NotificationBatch:
    """Convert evidence-backed Outlook notifications into minimal cashback events."""
    if (ai_engine is None) != (ai_resolver is None):
        raise ValueError("ai_engine and ai_resolver must be supplied together")
    engine = RuleEngine(rules)
    cashback_source = cashback_config or load_program_configuration()
    base_currency = str(cashback_source.get("currency") or "AED").upper()
    adapter_list = tuple(adapters)
    events: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    rows = list(messages)
    seen_message_ids: set[str] = set()
    for message in rows:
        if not isinstance(message, dict):
            raise ValueError("Outlook messages must be objects")
        message_id = str(message.get("id") or "").strip()
        if not message_id:
            skipped.append({"message_id": "", "reason": "MISSING_MESSAGE_ID"})
            continue
        if message_id in seen_message_ids:
            raise ValueError(f"Duplicate Outlook message id in scan batch: {message_id}")
        seen_message_ids.add(message_id)
        adapter = next((item for item in adapter_list if item.detect(message)), None)
        if adapter is None:
            skipped.append({"message_id": message_id, "reason": "UNSUPPORTED_NOTIFICATION"})
            continue
        try:
            fact = adapter.parse(message)
        except (ValueError, ArithmeticError) as error:
            skipped.append({"message_id": message_id, "reason": f"PARSE_ERROR:{error}"})
            continue
        card_code = card_by_last4.get(fact.card_last4)
        if not card_code:
            skipped.append({"message_id": message_id, "reason": "UNMAPPED_CARD_SUFFIX"})
            continue
        if fact.currency != base_currency:
            skipped.append({
                "message_id": message_id,
                "reason": f"MISSING_{base_currency}_EQUIVALENT",
            })
            continue

        transaction = Transaction(
            transaction_id=f"{message_id}:0",
            transaction_at=fact.occurred_at,
            card=card_code,
            account_last4=fact.card_last4,
            institution=fact.institution,
            merchant_raw=fact.merchant,
            amount_aed=fact.amount,
            amount_original=fact.amount,
            currency=fact.currency,
            channel=fact.channel,
            source_type="OUTLOOK_CARD_NOTIFICATION",
            source_message_id=message_id,
            review_required=fact.requires_review,
            tags={"card-notification", "purchase"},
            metadata={"notification_adapter": fact.adapter},
        )
        if transaction.channel == "UNKNOWN":
            transaction.channel = channel_from_config(
                cashback_source,
                transaction.tags,
                transaction.merchant_raw,
                transaction.card,
            )
            if transaction.channel != "UNKNOWN":
                transaction.tags.add("channel-config-default")
        static_trace = engine.apply(transaction)
        history_trace = apply_history_match(transaction, history_index or {})
        ai_trace = []
        if ai_engine and ai_resolver:
            ai_trace = ai_engine.enrich(transaction, ai_resolver)
        purchase_type = purchase_type_from_config(
            cashback_source,
            transaction.category,
            transaction.vendor or transaction.merchant_raw,
        )
        if transaction.reward_bucket is None:
            active_programs = programs_from_config(
                cashback_source,
                transaction.transaction_at.date(),
            )
            transaction.reward_bucket = configured_reward_bucket(
                active_programs,
                transaction.card,
                purchase_type,
                transaction.channel,
                transaction.currency,
            )
        event = {
            "source_event_id": transaction.transaction_id,
            "occurred_at": fact.occurred_at.isoformat(),
            "card_code": transaction.card,
            "amount_aed": str(fact.amount),
            "currency": fact.currency,
            "purchase_type": purchase_type,
            "channel": transaction.channel,
            "merchant": transaction.vendor or transaction.merchant_raw,
            "bucket_code": transaction.reward_bucket,
            "event_type": "PURCHASE",
            "source": "outlook",
            "status": "ACTIVE",
            "tags": sorted(transaction.tags),
            "confidence": fact.confidence,
            "review_required": transaction.review_required or transaction.channel == "UNKNOWN",
            "reconciliation_status": "UNMATCHED",
            "email_reference": str(message.get("web_link") or message.get("display_url") or "") or None,
            "decision_trace": [asdict(item) for item in static_trace]
            + ([] if history_trace is None else [asdict(history_trace)]),
            "ai_trace": [asdict(item) for item in ai_trace],
        }
        events.append(event)
    return NotificationBatch(
        events=tuple(events),
        scanned_count=len(rows),
        accepted_count=len(events),
        skipped=tuple(skipped),
    )
