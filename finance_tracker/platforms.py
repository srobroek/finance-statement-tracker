from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import re
from typing import Iterable, Protocol

from .models import Transaction


class PlatformKind(str, Enum):
    ACTUAL = "actual"
    FIREFLY_III = "firefly_iii"
    SURE = "sure"
    MONEYMATTER = "moneymatter"


@dataclass(frozen=True, slots=True)
class PlatformCapabilities:
    kind: PlatformKind
    ledger: bool
    budgeting: bool
    rules: bool
    reports: bool
    net_worth: bool
    tags: bool
    documents: bool
    ai: bool
    mcp: bool
    official_api: str


PLATFORM_CAPABILITIES: dict[PlatformKind, PlatformCapabilities] = {
    PlatformKind.ACTUAL: PlatformCapabilities(
        PlatformKind.ACTUAL,
        ledger=True,
        budgeting=True,
        rules=True,
        reports=True,
        net_worth=True,
        tags=True,
        documents=False,
        ai=False,
        mcp=False,
        official_api="Node.js API, CLI, and ActualQL; no official REST API",
    ),
    PlatformKind.FIREFLY_III: PlatformCapabilities(
        PlatformKind.FIREFLY_III,
        ledger=True,
        budgeting=True,
        rules=True,
        reports=True,
        net_worth=True,
        tags=True,
        documents=False,
        ai=False,
        mcp=False,
        official_api="REST JSON API",
    ),
    PlatformKind.SURE: PlatformCapabilities(
        PlatformKind.SURE,
        ledger=True,
        budgeting=True,
        rules=False,
        reports=True,
        net_worth=True,
        tags=True,
        documents=True,
        ai=True,
        mcp=True,
        official_api="OAuth MCP and application APIs",
    ),
    PlatformKind.MONEYMATTER: PlatformCapabilities(
        PlatformKind.MONEYMATTER,
        ledger=True,
        budgeting=True,
        rules=False,
        reports=True,
        net_worth=True,
        tags=True,
        documents=False,
        ai=True,
        mcp=True,
        official_api="OAuth MCP; application APIs are project-defined",
    ),
}


@dataclass(frozen=True, slots=True)
class ImportEnvelope:
    account: str
    records: tuple[dict[str, object], ...]
    default_cleared: bool = False


def _actual_tag(value: str) -> str:
    """Return a stable Actual tag token (Actual tags cannot contain spaces)."""
    token = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-")
    return token.casefold()


class LedgerBackend(Protocol):
    """Backend boundary used after parsing, normalization, and enrichment."""

    kind: PlatformKind

    def serialize_import(self, transactions: Iterable[Transaction]) -> list[ImportEnvelope]: ...


def _actual_amount(transaction: Transaction) -> int:
    """Return Actual's signed integer minor-unit amount.

    Purchases and payments leaving an account are negative. Income and refunds
    are positive. This conversion happens only at the backend boundary; the
    canonical transaction model remains platform neutral.
    """
    units = (abs(transaction.amount_aed) * Decimal("100")).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    statement_direction = str(
        transaction.metadata.get("statement_direction") or ""
    ).upper()
    if statement_direction in {"CREDIT", "DEBIT"}:
        # A statement already supplies the authoritative account-side sign.
        # Payments, refunds, and rewards are credits to a credit-card account;
        # purchases and fees are debits. Do not infer their sign from a later
        # classification such as TRANSFER.
        positive = statement_direction == "CREDIT"
    else:
        positive = transaction.is_refund or transaction.transaction_type.upper() in {
            "INCOME",
            "REFUND",
            "CREDIT",
        }
    return int(units if positive else -units)


class ActualBudgetAdapter:
    """Serialize canonical transactions for `@actual-app/api.importTransactions`.

    The official Actual API is Node-only. The Python worker therefore emits a
    stable JSON envelope and leaves authenticated writes to the small Node
    bridge under `integrations/actual`.
    """

    kind = PlatformKind.ACTUAL

    def serialize_import(self, transactions: Iterable[Transaction]) -> list[ImportEnvelope]:
        grouped: dict[str, list[dict[str, object]]] = {}
        for transaction in transactions:
            if not transaction.account:
                raise ValueError(
                    f"transaction {transaction.transaction_id!r} has no destination account"
                )
            record: dict[str, object] = {
                "date": transaction.transaction_at.date().isoformat(),
                "amount": _actual_amount(transaction),
                "payee_name": transaction.vendor or transaction.merchant_raw,
                "imported_payee": transaction.merchant_raw,
                "imported_id": transaction.transaction_id,
                "cleared": transaction.source_type.casefold() in {
                    "browser_statement",
                    "statement",
                    "statement_pdf",
                },
            }
            if transaction.subcategory or transaction.category:
                record["category_name"] = transaction.subcategory or transaction.category
            note_parts = [f"source:{transaction.source_type}"]
            if transaction.channel != "UNKNOWN":
                note_parts.append(f"#channel-{_actual_tag(transaction.channel)}")
            if transaction.currency != "AED":
                note_parts.append(f"currency:{transaction.currency}")
            if transaction.amount_original is not None:
                note_parts.append(f"original:{transaction.amount_original}")
            if transaction.source_message_id:
                note_parts.append(f"message:{transaction.source_message_id}")
            if transaction.tags:
                note_parts.extend(
                    f"#{token}"
                    for token in sorted(_actual_tag(tag) for tag in transaction.tags)
                    if token
                )
            if transaction.owner:
                note_parts.append(f"#owner-{_actual_tag(transaction.owner)}")
            if transaction.reward_bucket:
                note_parts.append(f"#cashback-{_actual_tag(transaction.reward_bucket)}")
            if transaction.review_required:
                note_parts.append("#needs-review")
            record["notes"] = " | ".join(note_parts)
            grouped.setdefault(transaction.account, []).append(record)

        return [
            ImportEnvelope(
                account,
                tuple(records),
                default_cleared=all(bool(record["cleared"]) for record in records),
            )
            for account, records in sorted(grouped.items(), key=lambda item: item[0].casefold())
        ]
