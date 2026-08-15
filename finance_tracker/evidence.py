from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Iterable

from .models import Transaction, money


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    message_id: str
    sent_at: datetime
    subject: str
    vendor: str | None = None
    amount_aed: Decimal | None = None
    attachment_name: str | None = None
    document_type: str = "document"


@dataclass(frozen=True, slots=True)
class EvidenceMatch:
    candidate: EvidenceCandidate
    score: Decimal
    reasons: tuple[str, ...]


def _tokens(value: str | None) -> set[str]:
    return {part for part in re.split(r"[^a-z0-9]+", (value or "").casefold()) if len(part) > 2}


def score_candidate(transaction: Transaction, candidate: EvidenceCandidate) -> EvidenceMatch:
    """Score an email/document without AI; AI may enrich only after a match."""
    score = Decimal("0")
    reasons: list[str] = []
    if candidate.amount_aed is not None:
        delta = abs(abs(money(candidate.amount_aed)) - abs(transaction.amount_aed))
        if delta <= Decimal("0.01"):
            score += Decimal("0.50")
            reasons.append("amount_exact")
        elif delta <= max(Decimal("1"), abs(transaction.amount_aed) * Decimal("0.01")):
            score += Decimal("0.30")
            reasons.append("amount_near")

    transaction_vendor = transaction.vendor or transaction.merchant_raw
    if _tokens(transaction_vendor) & (_tokens(candidate.vendor) | _tokens(candidate.subject)):
        score += Decimal("0.25")
        reasons.append("vendor_token")

    days = abs((candidate.sent_at.date() - transaction.transaction_at.date()).days)
    if days <= 3:
        score += Decimal("0.20")
        reasons.append("date_3d")
    elif days <= 14:
        score += Decimal("0.10")
        reasons.append("date_14d")

    evidence_terms = {"invoice", "receipt", "bill", "statement", "warranty", "order"}
    if _tokens(candidate.subject) & evidence_terms:
        score += Decimal("0.05")
        reasons.append("evidence_subject")
    return EvidenceMatch(candidate, min(score, Decimal("1")), tuple(reasons))


def best_match(
    transaction: Transaction,
    candidates: Iterable[EvidenceCandidate],
    minimum_score: Decimal = Decimal("0.80"),
) -> EvidenceMatch | None:
    ranked = sorted(
        (score_candidate(transaction, candidate) for candidate in candidates),
        key=lambda match: (match.score, match.candidate.sent_at, match.candidate.message_id),
        reverse=True,
    )
    return ranked[0] if ranked and ranked[0].score >= minimum_score else None


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "unknown-vendor"


def document_relative_path(
    transaction: Transaction,
    document_type: str,
    reference: str,
    content_digest: str | None = None,
    extension: str = "pdf",
) -> PurePosixPath:
    """Return the portable OneDrive path stored in the Notion Documents row."""
    vendor = _slug(transaction.vendor or transaction.merchant_raw)
    reference_slug = _slug(reference)[:48]
    digest = content_digest or hashlib.sha256(
        f"{transaction.transaction_id}|{document_type}|{reference}".encode("utf-8")
    ).hexdigest()
    amount = abs(transaction.amount_aed).quantize(Decimal("0.01"))
    filename = (
        f"{transaction.transaction_at:%Y-%m-%d}__{_slug(document_type)}__{vendor}__"
        f"{transaction.currency.lower()}-{amount}__{reference_slug}__{digest[:8]}.{extension.lstrip('.').lower()}"
    )
    return PurePosixPath(
        "Finance Evidence",
        f"{transaction.transaction_at:%Y}",
        f"{transaction.transaction_at:%m}",
        vendor,
        filename,
    )
