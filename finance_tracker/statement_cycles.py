"""Contracts for statement-email receipts and independent bank-cycle state."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

BANK_CLOSED = "BANK_CLOSED"
BOUNDS_UNKNOWN = "UNKNOWN"
BOUNDS_KNOWN = "KNOWN"
UNFINALIZED = "UNFINALIZED"
FINALIZED = "FINALIZED"

PROCESSING_STATES = frozenset(
    {
        "PENDING",
        "PROCESSING",
        "DECRYPTING",
        "PARSED",
        "FAILED",
        "DECRYPT_FAILED",
        "PARSE_FAILED",
        "QUARANTINED",
    }
)
RECONCILIATION_STATES = frozenset(
    {"PENDING", "PROCESSING", "RECONCILED", "VARIANCE", "FAILED", "NOT_APPLICABLE"}
)
STATEMENT_RECEIPT_CONTRACT = {
    "method": "POST",
    "path": "/api/statement-receipts",
    "auth": "Authorization: Bearer <CASHBACK_INGEST_TOKEN>",
    "schema_version": 1,
    "required": ["source", "source_message_id", "received_at", "card_code"],
    "optional": [
        "statement_reference",
        "period_start",
        "period_end",
        "source_attachment_id",
        "statement_sha256",
        "evidence_reference",
        "document_url",
        "subject",
    ],
    "response_state": {
        "state": BANK_CLOSED,
        "processing_state": "PENDING",
        "reconciliation_state": "PENDING",
    },
    "bounds": "period_start and period_end are both present or both null",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _text(
    value: object,
    field: str,
    *,
    required: bool = False,
    upper: bool = False,
) -> str | None:
    result = str(value or "").strip()
    if not result:
        if required:
            raise ValueError(f"{field} is required")
        return None
    return result.upper() if upper else result


def _alias(payload: dict[str, Any], *names: str) -> object:
    for name in names:
        if name in payload and payload[name] not in (None, ""):
            return payload[name]
    return None


def _timestamp(value: object, field: str) -> str:
    raw = _text(value, field, required=True)
    assert raw is not None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.isoformat()


def _iso_date(value: object, field: str) -> str | None:
    raw = _text(value, field)
    if raw is None:
        return None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO date") from error
    if parsed.isoformat() != raw:
        raise ValueError(f"{field} must be an ISO date")
    return raw


def _digest(value: object, field: str) -> str | None:
    raw = _text(value, field)
    if raw is None:
        return None
    raw = raw.removeprefix("sha256:").casefold()
    if not _SHA256.fullmatch(raw):
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    return raw


def receipt_id(source: str, source_message_id: str) -> str:
    """Return a stable server-owned key; caller idempotency keys are not identity."""
    return (
        "statement-receipt:"
        + hashlib.sha256(f"{source}\x00{source_message_id}".encode("utf-8")).hexdigest()
    )


def normalize_statement_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize the narrow n8n email-arrival contract without deriving dates."""
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a statement receipt object")
    schema_version = payload.get("schema_version", 1)
    if isinstance(schema_version, bool) or schema_version != 1:
        raise ValueError("Unsupported statement receipt schema version")
    if any(field in payload for field in ("transactions", "statement_transactions")):
        raise ValueError("statement receipts cannot contain statement purchases")

    source = _text(
        _alias(payload, "source", "source_system", "mailbox"),
        "source",
        required=True,
    )
    assert source is not None
    source_message_id = _text(
        _alias(
            payload,
            "source_message_id",
            "message_id",
            "id",
            "internet_message_id",
            "email_reference",
            "email_id",
        ),
        "source_message_id",
        required=True,
    )
    assert source_message_id is not None
    received_at = _timestamp(
        _alias(
            payload,
            "received_at",
            "receivedDateTime",
            "received_datetime",
            "received_timestamp",
        ),
        "received_at",
    )
    card_code = _text(
        _alias(payload, "card_code", "card", "account_code", "account_id", "bank"),
        "card_code",
        required=True,
        upper=True,
    )
    period_start = _iso_date(
        _alias(payload, "period_start", "statement_period_start"),
        "period_start",
    )
    period_end = _iso_date(
        _alias(payload, "period_end", "statement_period_end"),
        "period_end",
    )
    if (period_start is None) != (period_end is None):
        raise ValueError(
            "period_start and period_end must be supplied together or both omitted"
        )
    if (
        period_start is not None
        and period_end is not None
        and period_end < period_start
    ):
        raise ValueError("period_end cannot be before period_start")

    processing_state = (
        _text(payload.get("processing_state"), "processing_state", upper=True)
        or "PENDING"
    )
    reconciliation_state = (
        _text(
            _alias(payload, "reconciliation_state", "reconcile_state"),
            "reconciliation_state",
            upper=True,
        )
        or "PENDING"
    )
    if processing_state not in PROCESSING_STATES:
        raise ValueError(f"Unsupported processing_state: {processing_state}")
    if reconciliation_state not in RECONCILIATION_STATES:
        raise ValueError(f"Unsupported reconciliation_state: {reconciliation_state}")

    immutable = {
        "schema_version": 1,
        "source": source,
        "source_message_id": source_message_id,
        "received_at": received_at,
        "card_code": card_code,
        "statement_reference": _text(
            _alias(
                payload, "statement_reference", "statement_id", "document_reference"
            ),
            "statement_reference",
        ),
        "period_start": period_start,
        "period_end": period_end,
        "source_attachment_id": _text(
            _alias(payload, "source_attachment_id", "attachment_id"),
            "source_attachment_id",
        ),
        "statement_sha256": _digest(
            _alias(payload, "statement_sha256", "statement_digest", "document_sha256"),
            "statement_sha256",
        ),
        "evidence_reference": _text(
            _alias(payload, "evidence_reference", "statement_evidence_reference"),
            "evidence_reference",
        ),
        "document_url": _text(
            _alias(payload, "document_url", "statement_document_url"),
            "document_url",
        ),
        "subject": _text(payload.get("subject"), "subject"),
    }
    canonical_digest = hashlib.sha256(
        json.dumps(
            immutable,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        **immutable,
        "receipt_id": receipt_id(source, source_message_id),
        "receipt_sha256": canonical_digest,
        "state": BANK_CLOSED,
        "processing_state": processing_state,
        "reconciliation_state": reconciliation_state,
    }


def receipt_period_bounds(
    period_start: object,
    period_end: object,
) -> tuple[datetime, datetime]:
    """Return an inclusive receipt's exact UTC half-open day interval."""
    start = _iso_date(period_start, "period_start")
    end = _iso_date(period_end, "period_end")
    if start is None or end is None:
        raise ValueError("period_start and period_end are required")
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    if end_date < start_date:
        raise ValueError("period_end cannot be before period_start")
    return (
        datetime.combine(start_date, datetime.min.time(), tzinfo=UTC),
        datetime.combine(
            end_date + timedelta(days=1),
            datetime.min.time(),
            tzinfo=UTC,
        ),
    )


def receipt_view(row: dict[str, Any]) -> dict[str, Any]:
    """Expose receipt state without exposing raw email or mutable source payload."""
    start = row.get("period_start")
    end = row.get("period_end")
    bounds_known = bool(start and end)
    finalized = str(row.get("settlement_state") or "").upper() == FINALIZED
    processing_state = row.get("processing_state") or "PENDING"
    reconciliation_state = row.get("reconciliation_state") or "PENDING"
    if row.get("statement_reference"):
        cycle_id = f"{row.get('card_code')}:{row.get('statement_reference')}"
    elif bounds_known:
        cycle_id = f"{row.get('card_code')}:{start}:{end}"
    else:
        cycle_id = str(row.get("receipt_id") or "")
    return {
        "receipt_id": row.get("receipt_id"),
        "cycle_id": cycle_id,
        "idempotency_key": row.get("receipt_id"),
        "state": row.get("state") or BANK_CLOSED,
        "bank_state": row.get("state") or BANK_CLOSED,
        "status": row.get("state") or BANK_CLOSED,
        "processing_state": processing_state,
        "processing_status": processing_state,
        "reconciliation_state": reconciliation_state,
        "reconciliation_status": reconciliation_state,
        "source": row.get("source"),
        "source_message_id": row.get("source_message_id"),
        "message_id": row.get("source_message_id"),
        "email_reference": row.get("source_message_id"),
        "received_at": row.get("received_at"),
        "card_code": row.get("card_code"),
        "statement_reference": row.get("statement_reference"),
        "source_attachment_id": row.get("source_attachment_id"),
        "statement_sha256": row.get("statement_sha256"),
        "evidence_reference": row.get("evidence_reference"),
        "document_url": row.get("document_url"),
        "subject": row.get("subject"),
        "period_start": start,
        "period_end": end,
        "bounds_state": BOUNDS_KNOWN if bounds_known else BOUNDS_UNKNOWN,
        "settlement_state": FINALIZED if finalized else UNFINALIZED,
        "receipt_sha256": row.get("receipt_sha256"),
    }
