from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Iterable, Protocol

from .actual_notes import format_actual_notes, normalize_actual_tag
from .classification_audit import enforce_transaction_invariants
from .models import Transaction
from .transaction_semantics import actual_amount_minor, topic_semantics


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
    return normalize_actual_tag(value)


class LedgerBackend(Protocol):
    """Backend boundary used after parsing, normalization, and enrichment."""

    kind: PlatformKind

    def serialize_import(self, transactions: Iterable[Transaction]) -> list[ImportEnvelope]: ...


def _actual_amount(transaction: Transaction) -> int:
    """Return Actual's signed integer minor-unit amount."""

    return actual_amount_minor(transaction)


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
            enforce_transaction_invariants(transaction)
            if not transaction.account:
                raise ValueError(
                    f"transaction {transaction.transaction_id!r} has no destination account"
                )
            transaction_date = transaction.transaction_at.date().isoformat()
            post_date = transaction.metadata.get("statement_post_date") if transaction.source_type in {"statement", "statement_pdf"} else None
            ledger_date = date.fromisoformat(str(post_date)).isoformat() if post_date else transaction_date
            if ledger_date < transaction_date:
                raise ValueError("statement posting date precedes transaction date")
            record: dict[str, object] = {
                "date": ledger_date,
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
            # Provenance and Outlook IDs are durable in imported_id and the
            # ingestion manifest. Actual notes stay human-facing: tags first,
            # then only compact facts that are useful in the ledger.
            semantic_tags: list[str] = []
            topic_tag = topic_semantics(transaction.transaction_type).topic_tag
            if topic_tag:
                semantic_tags.append(topic_tag)
                if transaction.transaction_type.upper() == "REVERSAL":
                    semantic_tags.append("refund")
            if transaction.channel != "UNKNOWN":
                semantic_tags.append(f"channel-{_actual_tag(transaction.channel)}")
            if transaction.tags:
                semantic_tags.extend(_actual_tag(tag) for tag in transaction.tags)
            if transaction.owner:
                semantic_tags.append(f"owner-{_actual_tag(transaction.owner)}")
            if transaction.review_required:
                semantic_tags.append("needs-review")
            record["notes"] = format_actual_notes(tags=semantic_tags, memos=[f"Transaction date {transaction_date}; posted {ledger_date}"] if ledger_date != transaction_date else [])
            grouped.setdefault(transaction.account, []).append(record)

        return [
            ImportEnvelope(
                account,
                tuple(records),
                default_cleared=all(bool(record["cleared"]) for record in records),
            )
            for account, records in sorted(grouped.items(), key=lambda item: item[0].casefold())
        ]
