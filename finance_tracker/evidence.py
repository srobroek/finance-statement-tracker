from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Iterable

from .models import Transaction, money


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    message_id: str
    sent_at: datetime
    subject: str
    vendor: str | None = None
    amount_aed: Decimal | None = None
    currency: str | None = None
    amount_original: Decimal | None = None
    currency_original: str | None = None
    reference: str | None = None
    order_reference: str | None = None
    account_reference: str | None = None
    property_code: str | None = None
    attachment_name: str | None = None
    document_type: str = "document"


@dataclass(frozen=True, slots=True)
class EvidenceMatch:
    candidate: EvidenceCandidate
    score: Decimal
    reasons: tuple[str, ...]

    @property
    def strong_fact_count(self) -> int:
        return sum(
            reason
            in {
                "amount_exact",
                "original_amount_exact",
                "aggregate_original_amount_near",
                "vendor_exact",
                "reference_exact",
                "account_exact",
                "property_exact",
                "date_buffer",
                "message_id_exact",
            }
            for reason in self.reasons
        )


@dataclass(frozen=True, slots=True)
class ArchivedEvidence:
    transaction_id: str
    document_type: str
    relative_path: str
    sha256: str
    size_bytes: int
    message_id: str | None = None
    attachment_id: str | None = None


@dataclass(frozen=True, slots=True)
class ArchivedStatementEvidence:
    relative_path: str
    sha256: str
    size_bytes: int


def evidence_catalogue_record(
    archived: ArchivedEvidence,
    transaction: Transaction,
    *,
    reference: str,
    warranty_expiry: str | None = None,
    web_url: str | None = None,
) -> dict[str, object]:
    """Build a portable row for the OneDrive JSON evidence catalogue."""
    return {
        "schema_version": 1,
        "evidence_id": f"sha256:{archived.sha256}",
        "entity_type": "TRANSACTION",
        "entity_id": transaction.transaction_id,
        "transaction_id": transaction.transaction_id,
        "document_type": archived.document_type,
        "vendor": transaction.vendor or transaction.merchant_raw,
        "transaction_date": transaction.transaction_at.date().isoformat(),
        "amount_aed": str(abs(transaction.amount_aed)),
        "currency": "AED",
        "original_currency": transaction.currency if transaction.currency != "AED" else None,
        "amount_original": (
            None if transaction.amount_original is None else str(abs(transaction.amount_original))
        ),
        "reference": reference,
        "account": transaction.account,
        "property_code": transaction.property_code,
        "rental_unit": transaction.rental_unit,
        "warranty_expiry": warranty_expiry,
        "message_id": archived.message_id,
        "attachment_id": archived.attachment_id,
        "relative_path": archived.relative_path,
        "web_url": web_url,
        "sha256": archived.sha256,
        "size_bytes": archived.size_bytes,
    }


def evidence_group_catalogue_record(
    archived: ArchivedEvidence,
    transactions: Iterable[Transaction],
    *,
    reference: str,
    web_url: str | None = None,
) -> dict[str, object]:
    """Build one catalogue row linking a document to a transaction group."""
    rows = tuple(transactions)
    if len(rows) < 2:
        raise ValueError("Grouped evidence requires at least two transactions")
    vendors = {_identity(row.vendor or row.merchant_raw) for row in rows}
    if len(vendors) != 1:
        raise ValueError("Grouped evidence transactions must have one normalized vendor")
    return {
        "schema_version": 1,
        "evidence_id": f"sha256:{archived.sha256}",
        "entity_type": "TRANSACTION_GROUP",
        "entity_id": f"transaction-group:{reference}",
        "transaction_id": None,
        "transaction_ids": sorted(row.transaction_id for row in rows),
        "document_type": archived.document_type,
        "vendor": rows[0].vendor or rows[0].merchant_raw,
        "transaction_date": min(row.transaction_at.date() for row in rows).isoformat(),
        "amount_aed": str(sum((abs(row.amount_aed) for row in rows), Decimal("0"))),
        "currency": "AED",
        "reference": reference,
        "accounts": sorted({row.account for row in rows if row.account}),
        "cards": sorted({row.card for row in rows}),
        "message_id": archived.message_id,
        "attachment_id": archived.attachment_id,
        "relative_path": archived.relative_path,
        "web_url": web_url,
        "sha256": archived.sha256,
        "size_bytes": archived.size_bytes,
    }


def statement_catalogue_record(
    archived_path: str | Path,
    *,
    bank: str,
    card_code: str,
    statement_date: str,
    period_start: str,
    period_end: str,
    reference: str,
    closing_balance_aed: str,
    payment_due_date: str | None = None,
    message_id: str | None = None,
    attachment_id: str | None = None,
    web_url: str | None = None,
) -> dict[str, object]:
    """Build a searchable catalogue row for card/account statement evidence."""
    source = Path(archived_path)
    if not source.is_file():
        raise ValueError(f"Statement evidence does not exist: {source}")
    digest = _sha256_file(source)
    try:
        relative = source.relative_to(Path.cwd()).as_posix()
    except ValueError:
        relative = source.as_posix()
    return {
        "schema_version": 1,
        "evidence_id": f"sha256:{digest}",
        "entity_type": "CARD_PERIOD",
        "entity_id": f"{card_code}:{period_start}:{period_end}",
        "transaction_id": None,
        "document_type": "statement",
        "bank": bank,
        "card_code": card_code,
        "statement_date": statement_date,
        "period_start": period_start,
        "period_end": period_end,
        "payment_due_date": payment_due_date,
        "closing_balance_aed": closing_balance_aed,
        "currency": "AED",
        "reference": reference,
        "message_id": message_id,
        "attachment_id": attachment_id,
        "relative_path": relative,
        "web_url": web_url,
        "sha256": digest,
        "size_bytes": source.stat().st_size,
    }


def statement_relative_path(
    *,
    statement_date: str,
    bank: str,
    closing_balance_aed: str | Decimal,
    reference: str,
    content_digest: str,
    extension: str = "pdf",
) -> PurePosixPath:
    """Return the canonical content-addressed path for a statement original."""
    parsed_date = datetime.strptime(statement_date, "%Y-%m-%d")
    bank_slug = _slug(bank)
    reference_slug = _slug(reference)[:48]
    amount = abs(money(closing_balance_aed)).quantize(Decimal("0.01"))
    filename = (
        f"{statement_date}__statement__{bank_slug}__aed-{amount}__"
        f"{reference_slug}__{content_digest[:8]}.{extension.lstrip('.').lower()}"
    )
    return PurePosixPath(
        "Finance Evidence",
        f"{parsed_date:%Y}",
        f"{parsed_date:%m}",
        bank_slug,
        filename,
    )


def archive_statement_evidence(
    source_path: str | Path,
    evidence_root: str | Path,
    *,
    statement_date: str,
    bank: str,
    closing_balance_aed: str | Decimal,
    reference: str,
) -> ArchivedStatementEvidence:
    """Archive a statement original idempotently without modifying its bytes."""
    source = Path(source_path)
    if not source.is_file():
        raise ValueError(f"Statement evidence source does not exist: {source}")
    sha256 = _sha256_file(source)
    relative = statement_relative_path(
        statement_date=statement_date,
        bank=bank,
        closing_balance_aed=closing_balance_aed,
        reference=reference,
        content_digest=sha256,
        extension=source.suffix or ".bin",
    )
    destination = Path(evidence_root).joinpath(*relative.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if _sha256_file(destination) != sha256:
            raise ValueError(f"Statement evidence filename collision at {destination}")
    else:
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        shutil.copyfile(source, temporary)
        temporary.replace(destination)
    return ArchivedStatementEvidence(
        relative_path=relative.as_posix(),
        sha256=sha256,
        size_bytes=destination.stat().st_size,
    )


def update_evidence_catalogue(
    catalogue_path: str | Path,
    record: dict[str, object],
) -> dict[str, int]:
    """Idempotently upsert a searchable evidence record into a JSON catalogue."""
    destination = Path(catalogue_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    if destination.exists():
        parsed = json.loads(destination.read_text(encoding="utf-8"))
        if not isinstance(parsed, list):
            raise ValueError("Evidence catalogue must be a JSON array")
        rows = parsed
    evidence_id = str(record.get("evidence_id") or "").strip()
    entity_id = str(record.get("entity_id") or record.get("transaction_id") or "").strip()
    if not evidence_id or not entity_id:
        raise ValueError("Evidence catalogue records require evidence_id and entity_id or transaction_id")
    key = (evidence_id, entity_id)
    index = next(
        (
            position
            for position, row in enumerate(rows)
            if (
                str(row.get("evidence_id")),
                str(row.get("entity_id") or row.get("transaction_id") or ""),
            ) == key
        ),
        None,
    )
    if index is None:
        rows.append(dict(record))
        outcome = {"inserted": 1, "updated": 0}
    else:
        rows[index] = dict(record)
        outcome = {"inserted": 0, "updated": 1}
    rows.sort(key=lambda row: (str(row.get("transaction_date") or ""), str(row.get("evidence_id"))))
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return outcome


def _tokens(value: str | None) -> set[str]:
    return {part for part in re.split(r"[^a-z0-9]+", (value or "").casefold()) if len(part) > 2}


def _identity(value: str | None) -> str:
    return " ".join(sorted(_tokens(value)))


def _account_references(transaction: Transaction) -> set[str]:
    values = (
        transaction.account_last4,
        transaction.account,
        transaction.metadata.get("statement_card_last4"),
        transaction.metadata.get("account_reference"),
    )
    return {
        token
        for value in values
        if value
        for token in re.findall(r"[a-z0-9]+", str(value).casefold())
    }


def score_candidate(
    transaction: Transaction,
    candidate: EvidenceCandidate,
    *,
    date_buffer_days: int = 7,
) -> EvidenceMatch:
    """Score an email/document without AI; AI may enrich only after a match."""
    score = Decimal("0")
    reasons: list[str] = []
    if candidate.currency and candidate.currency.upper() != transaction.currency.upper():
        return EvidenceMatch(candidate, Decimal("0"), ("currency_mismatch",))
    if candidate.currency:
        score += Decimal("0.05")
        reasons.append("currency_exact")
    if candidate.amount_aed is not None:
        delta = abs(abs(money(candidate.amount_aed)) - abs(transaction.amount_aed))
        if delta <= Decimal("0.01"):
            score += Decimal("0.45")
            reasons.append("amount_exact")
        elif delta <= max(Decimal("1"), abs(transaction.amount_aed) * Decimal("0.01")):
            score += Decimal("0.25")
            reasons.append("amount_near")

    if candidate.amount_original is not None:
        candidate_currency = (candidate.currency_original or "").upper()
        if candidate_currency and transaction.currency != candidate_currency:
            return EvidenceMatch(candidate, Decimal("0"), ("original_currency_mismatch",))
        if candidate_currency:
            score += Decimal("0.05")
            reasons.append("original_currency_exact")
        if transaction.amount_original is not None:
            original_delta = abs(abs(money(candidate.amount_original)) - abs(transaction.amount_original))
            if original_delta <= Decimal("0.01"):
                score += Decimal("0.45")
                reasons.append("original_amount_exact")
            elif original_delta <= max(Decimal("1"), abs(transaction.amount_original) * Decimal("0.01")):
                score += Decimal("0.25")
                reasons.append("original_amount_near")

    transaction_vendor = transaction.vendor or transaction.merchant_raw
    if candidate.vendor and _identity(transaction_vendor) == _identity(candidate.vendor):
        score += Decimal("0.25")
        reasons.append("vendor_exact")
    elif _tokens(transaction_vendor) & (_tokens(candidate.vendor) | _tokens(candidate.subject)):
        score += Decimal("0.20")
        reasons.append("vendor_token")

    transaction_references = {
        str(value).casefold()
        for value in (
            transaction.metadata.get("reference"),
            transaction.metadata.get("order_reference"),
            transaction.metadata.get("account_reference"),
        )
        if value
    }
    candidate_references = {
        str(value).casefold()
        for value in (candidate.reference, candidate.order_reference, candidate.account_reference)
        if value
    }
    if transaction_references & candidate_references:
        score += Decimal("0.20")
        reasons.append("reference_exact")

    candidate_account = set(re.findall(r"[a-z0-9]+", (candidate.account_reference or "").casefold()))
    if candidate_account and _account_references(transaction) & candidate_account:
        score += Decimal("0.20")
        reasons.append("account_exact")

    transaction_property = (transaction.property_code or transaction.rental_unit or "").casefold()
    if transaction_property and transaction_property == (candidate.property_code or "").casefold():
        score += Decimal("0.15")
        reasons.append("property_exact")

    if transaction.source_message_id and transaction.source_message_id == candidate.message_id:
        score += Decimal("0.20")
        reasons.append("message_id_exact")

    days = abs((candidate.sent_at.date() - transaction.transaction_at.date()).days)
    if days <= date_buffer_days:
        score += Decimal("0.15")
        reasons.append("date_buffer")
    elif days <= 14:
        score += Decimal("0.08")
        reasons.append("date_14d")

    evidence_terms = {"invoice", "receipt", "bill", "statement", "warranty", "order"}
    if _tokens(candidate.subject) & evidence_terms:
        score += Decimal("0.05")
        reasons.append("evidence_subject")
    return EvidenceMatch(candidate, min(score, Decimal("1")), tuple(reasons))


def best_match(
    transaction: Transaction,
    candidates: Iterable[EvidenceCandidate],
    minimum_score: Decimal = Decimal("0.50"),
    minimum_strong_facts: int = 2,
    date_buffer_days: int = 7,
) -> EvidenceMatch | None:
    ranked = sorted(
        (
            score_candidate(transaction, candidate, date_buffer_days=date_buffer_days)
            for candidate in candidates
        ),
        key=lambda match: (match.score, match.candidate.sent_at, match.candidate.message_id),
        reverse=True,
    )
    return (
        ranked[0]
        if ranked
        and ranked[0].score >= minimum_score
        and ranked[0].strong_fact_count >= minimum_strong_facts
        else None
    )


def score_group_candidate(
    transactions: Iterable[Transaction],
    candidate: EvidenceCandidate,
    *,
    date_buffer_days: int = 7,
) -> EvidenceMatch:
    """Score one document against several statement rows from the same purchase.

    This supports issuer layouts that split a booking into passenger, segment,
    or ancillary rows while the merchant email contains one aggregate total.
    Approximate issuer exchange rates are used only as a corroborating fact.
    """
    rows = tuple(transactions)
    if not rows:
        return EvidenceMatch(candidate, Decimal("0"), ("empty_group",))
    vendors = {_identity(row.vendor or row.merchant_raw) for row in rows}
    if len(vendors) != 1 or not candidate.vendor or _identity(candidate.vendor) not in vendors:
        return EvidenceMatch(candidate, Decimal("0"), ("vendor_mismatch",))
    reasons = ["vendor_exact"]
    score = Decimal("0.25")
    if all(abs((candidate.sent_at.date() - row.transaction_at.date()).days) <= date_buffer_days for row in rows):
        score += Decimal("0.15")
        reasons.append("date_buffer")
    else:
        return EvidenceMatch(candidate, Decimal("0"), ("date_outside_buffer",))

    currency = (candidate.currency_original or "").upper()
    if candidate.amount_original is not None and currency:
        if any(row.currency != currency for row in rows):
            return EvidenceMatch(candidate, Decimal("0"), ("original_currency_mismatch",))
        estimated = Decimal("0")
        for row in rows:
            if row.amount_original is not None:
                estimated += abs(row.amount_original)
                continue
            raw_rate = row.metadata.get("statement_exchange_rate")
            if not raw_rate or money(raw_rate) <= 0:
                return EvidenceMatch(candidate, Decimal("0"), ("missing_exchange_rate",))
            estimated += abs(row.amount_aed) / money(raw_rate)
        delta = abs(estimated - abs(money(candidate.amount_original)))
        # Issuer PDFs commonly round the displayed FX rate to two decimals and
        # may split one booking across passengers or ancillary charges.  A 1%
        # aggregate tolerance is narrow enough to corroborate the document
        # without pretending the displayed rate can reproduce settlement to
        # the cent.
        tolerance = max(Decimal("1"), abs(money(candidate.amount_original)) * Decimal("0.01"))
        if delta <= tolerance:
            score += Decimal("0.45")
            reasons.append("aggregate_original_amount_near")
        else:
            return EvidenceMatch(candidate, Decimal("0"), ("aggregate_amount_mismatch",))

    candidate_account = set(re.findall(r"[a-z0-9]+", (candidate.account_reference or "").casefold()))
    if candidate_account and all(_account_references(row) & candidate_account for row in rows):
        score += Decimal("0.20")
        reasons.append("account_exact")
    return EvidenceMatch(candidate, min(score, Decimal("1")), tuple(reasons))


def best_group_match(
    transactions: Iterable[Transaction],
    candidates: Iterable[EvidenceCandidate],
    *,
    minimum_score: Decimal = Decimal("0.50"),
    minimum_strong_facts: int = 2,
    date_buffer_days: int = 7,
) -> EvidenceMatch | None:
    rows = tuple(transactions)
    ranked = sorted(
        (
            score_group_candidate(rows, candidate, date_buffer_days=date_buffer_days)
            for candidate in candidates
        ),
        key=lambda match: (match.score, match.candidate.sent_at, match.candidate.message_id),
        reverse=True,
    )
    return (
        ranked[0]
        if ranked
        and ranked[0].score >= minimum_score
        and ranked[0].strong_fact_count >= minimum_strong_facts
        else None
    )


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "unknown-vendor"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_outlook_evidence_snapshot(
    message: dict[str, object],
    destination: str | Path,
) -> Path:
    """Write a deterministic, human-readable email snapshot without secrets.

    The Outlook web link and message ID are retained for traceability. Embedded
    and external body URLs are removed because booking links commonly contain
    bearer-like authentication keys. PINs, OTPs, verification codes, and
    passwords are redacted from visible text.
    """
    message_id = str(message.get("id") or "").strip()
    subject = str(message.get("subject") or "").strip()
    if not message_id or not subject:
        raise ValueError("Outlook evidence requires message id and subject")
    sender_value = message.get("sender") or {}
    if isinstance(sender_value, dict):
        email_address = sender_value.get("emailAddress") or {}
        if isinstance(email_address, dict):
            sender = str(email_address.get("address") or email_address.get("name") or "").strip()
        else:
            sender = str(email_address).strip()
    else:
        sender = str(sender_value).strip()
    body_value = message.get("body") or {}
    body = str(body_value.get("content") or "") if isinstance(body_value, dict) else str(body_value)
    body = re.sub(r"<\s*(script|style)\b.*?</\s*\1\s*>", " ", body, flags=re.I | re.S)
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", body)
    body = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", body)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"https?://\S+", "[link removed]", body, flags=re.I)
    body = html.unescape(body)
    secret_label = r"(?:PIN(?:\s+code)?|OTP|one[- ]time(?:\s+password)?|verification\s+code|password)"
    body = re.sub(
        rf"(?i)\b({secret_label})\s*[:#=-]?\s*[A-Za-z0-9_-]+",
        r"\1: [REDACTED]",
        body,
    )
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    rendered = "\n".join(
        (
            "OUTLOOK EVIDENCE SNAPSHOT (SANITIZED)",
            f"Message ID: {message_id}",
            f"Subject: {subject}",
            f"From: {sender or 'unknown'}",
            f"Received: {str(message.get('receivedDateTime') or '').strip()}",
            f"Outlook link: {str(message.get('web_link') or message.get('webLink') or '').strip()}",
            "",
            body,
            "",
        )
    )
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(output)
    return output


def document_relative_path(
    transaction: Transaction,
    document_type: str,
    reference: str,
    content_digest: str | None = None,
    extension: str = "pdf",
) -> PurePosixPath:
    """Return the portable OneDrive path stored in the evidence catalogue."""
    vendor = _slug(transaction.vendor or transaction.merchant_raw)
    reference_slug = _slug(reference)[:48]
    digest = content_digest or hashlib.sha256(
        f"{transaction.transaction_id}|{document_type}|{reference}".encode("utf-8")
    ).hexdigest()
    amount = abs(transaction.amount_aed).quantize(Decimal("0.01"))
    filename = (
        f"{transaction.transaction_at:%Y-%m-%d}__{_slug(document_type)}__{vendor}__"
        f"aed-{amount}__{reference_slug}__{digest[:8]}.{extension.lstrip('.').lower()}"
    )
    return PurePosixPath(
        "Finance Evidence",
        f"{transaction.transaction_at:%Y}",
        f"{transaction.transaction_at:%m}",
        vendor,
        filename,
    )


def archive_evidence(
    source_path: str | Path,
    evidence_root: str | Path,
    transaction: Transaction,
    document_type: str,
    reference: str,
    *,
    message_id: str | None = None,
    attachment_id: str | None = None,
) -> ArchivedEvidence:
    """Archive evidence idempotently under the structured OneDrive hierarchy."""
    source = Path(source_path)
    if not source.is_file():
        raise ValueError(f"Evidence source does not exist: {source}")
    sha256 = _sha256_file(source)
    relative = document_relative_path(
        transaction,
        document_type,
        reference,
        content_digest=sha256,
        extension=source.suffix or ".bin",
    )
    destination = Path(evidence_root).joinpath(*relative.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        existing = _sha256_file(destination)
        if existing != sha256:
            raise ValueError(f"Evidence filename collision at {destination}")
    else:
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        shutil.copyfile(source, temporary)
        temporary.replace(destination)
    return ArchivedEvidence(
        transaction_id=transaction.transaction_id,
        document_type=document_type,
        relative_path=relative.as_posix(),
        sha256=sha256,
        size_bytes=destination.stat().st_size,
        message_id=message_id,
        attachment_id=attachment_id,
    )
