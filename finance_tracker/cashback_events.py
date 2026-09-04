from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from .actual_snapshot import cashback_dashboard
from .cashback import (
    PaymentIntent,
    configured_reward_bucket,
    load_program_configuration,
    payment_intents_from_config,
    programs_from_config,
    statement_period,
)
from .models import Transaction, money
from .transaction_semantics import CASHBACK_TOPICS

ACTIVE_STATUSES = frozenset({"ACTIVE"})
VALID_STATUSES = ACTIVE_STATUSES | {"IGNORED", "REVERSED"}
VALID_EVENT_TYPES = CASHBACK_TOPICS
VALID_RECONCILIATION_STATUSES = frozenset({"UNMATCHED", "MATCHED", "VARIANCE", "RECONCILED"})
# An event's canonical economics are immutable observations from an upstream
# source.  Enrichment and reconciliation metadata may change independently, so
# they are intentionally excluded from replay identity.  A source replay must
# never overwrite those fields either; intentional economic changes go through
# ``correct_event`` and its audit row.
EVENT_CANONICAL_FIELDS = (
    "occurred_at",
    "card_code",
    "amount_aed_minor",
    "currency",
    "merchant",
    "event_type",
    "reversal_of",
)
CORRECTABLE_EVENT_FIELDS = frozenset(
    {
        "occurred_at",
        "card_code",
        "amount_aed",
        "currency",
        "purchase_type",
        "channel",
        "merchant",
        "bucket_code",
        "event_type",
        "status",
        "tags",
        "confidence",
        "review_required",
        "email_reference",
        "document_url",
        "ai_trace",
    }
)
AI_CORRECTABLE_EVENT_FIELDS = frozenset(
    {
        "purchase_type",
        "channel",
        "merchant",
        "bucket_code",
        "tags",
        "confidence",
        "review_required",
        "email_reference",
        "document_url",
        "ai_trace",
    }
)
# Once a statement has established authoritative evidence for an event, its
# matching facts cannot be edited in place.  A new statement reconciliation is
# required so the receipt and rows continue to describe the same economics.
RECONCILIATION_FACT_FIELDS = frozenset(
    {
        "occurred_at",
        "card_code",
        "amount_aed",
        "currency",
        "merchant",
        "event_type",
        "status",
    }
)
_MERCHANT_TOKEN = re.compile(r"[^A-Z0-9]+")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")
_MINOR_UNIT = Decimal("0.01")
_STATEMENT_DIGEST_FIELDS = (
    "statement_sha256",
    "statement_digest",
    "document_sha256",
)
_STATEMENT_CONTENT_DIGEST_FIELDS = (
    "statement_content_sha256",
    "statement_content_digest",
    "canonical_statement_sha256",
)
_ACTUAL_RECEIPT_DIGEST_FIELDS = (
    "actual_import_receipt_sha256",
    "actual_verification_sha256",
    "actual_import_receipt_digest",
    "actual_receipt_sha256",
)
_ACTUAL_RECEIPT_DIGEST_KEYS = frozenset(
    {
        "receipt_sha256",
        "actual_import_receipt_sha256",
        "actual_verification_sha256",
        "actual_import_receipt_digest",
        "actual_receipt_sha256",
    }
)


class IngestCursorConflict(ValueError):
    """Raised when a cursor commit cannot be proven to be the next commit."""


def _date_range_bounds(start: date, end: date) -> tuple[str, str]:
    """Return lexically indexable bounds for ISO timestamps preserving local dates."""
    if end < start:
        raise ValueError("date range end cannot be before start")
    return f"{start.isoformat()}T00:00:00", f"{(end + timedelta(days=1)).isoformat()}T00:00:00"


def _json_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _sha256_field(value: object, field_name: str) -> str:
    """Normalize a SHA-256 field while keeping the persisted form unambiguous."""
    digest = str(value or "").strip().casefold()
    if digest.startswith("sha256:"):
        digest = digest[7:]
    if not _SHA256.fullmatch(digest):
        raise ValueError(f"{field_name} must be a SHA-256 hex digest")
    return digest


def _payload_sha256(payload: dict[str, Any], fields: tuple[str, ...], label: str) -> str:
    """Read one digest under its supported contract aliases and reject conflicts."""
    values = {
        _sha256_field(payload[field], field)
        for field in fields
        if field in payload and payload[field] not in (None, "")
    }
    if not values:
        raise ValueError(f"{label} is required")
    if len(values) != 1:
        raise ValueError(f"{label} fields disagree")
    return values.pop()


def _close_identifier(card_code: str, period_start: str, period_end: str) -> str:
    """Return the server-owned stable identifier for a finalized card period."""
    return f"cashback-close:{card_code}:{period_start}:{period_end}"


def _trusted_actual_receipt(payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Validate and hash the independently read-back Actual verification receipt.

    The receipt is deliberately stronger than a caller supplied boolean or hash:
    it must carry the writer identity, period, payload read-back hashes, and a
    successful invariant check.  The digest is calculated from that receipt,
    excluding only an optional embedded digest field, and is compared with the
    top-level close proof when one is supplied.
    """
    receipt = payload.get("actual_import_receipt")
    if not isinstance(receipt, dict):
        raise ValueError(  # noqa: TRY004 - payload errors are HTTP 400s
            "actual_import_receipt readback object and actual_import_receipt_sha256 are required"
        )
    required = (
        "outbox_id",
        "verification_version",
        "actual_file_id",
        "account_id",
        "card_code",
        "period_start",
        "period_end",
        "state",
        "writer_release_verified",
        "invariants_passed",
        "verified_at",
    )
    missing = [field for field in required if field not in receipt]
    if missing:
        raise ValueError(
            "actual_import_receipt readback is missing required fields: "
            + ", ".join(missing)
        )
    for field in ("outbox_id", "actual_file_id", "account_id", "card_code"):
        if not isinstance(receipt[field], str) or not receipt[field].strip():
            raise ValueError(f"actual_import_receipt.{field} must be a non-empty identity")
    version = receipt["verification_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("actual_import_receipt.verification_version must be a positive integer")
    if receipt["invariants_passed"] is not True:
        raise ValueError("actual_import_receipt invariants must pass")
    if receipt["state"] != "COMMITTED":
        raise ValueError("actual_import_receipt state must be COMMITTED")
    if receipt["writer_release_verified"] is not True:
        raise ValueError("actual_import_receipt writer release must be verified")
    for field in ("period_start", "period_end"):
        try:
            date.fromisoformat(str(receipt[field]))
        except ValueError as error:
            raise ValueError(f"actual_import_receipt.{field} must be an ISO date") from error
    if date.fromisoformat(str(receipt["period_end"])) < date.fromisoformat(
        str(receipt["period_start"])
    ):
        raise ValueError("actual_import_receipt period_end cannot be before period_start")
    verified_at = receipt["verified_at"]
    if not isinstance(verified_at, str) or not verified_at.strip():
        raise ValueError("actual_import_receipt.verified_at must be an ISO timestamp")
    try:
        parsed_verified_at = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("actual_import_receipt.verified_at must be an ISO timestamp") from error
    if parsed_verified_at.tzinfo is None:
        raise ValueError("actual_import_receipt.verified_at must include a timezone")

    expected_values = {
        _sha256_field(receipt[field], field)
        for field in ("expected_payload_sha256", "expected_sha256")
        if field in receipt and receipt[field] not in (None, "")
    }
    observed_values = {
        _sha256_field(receipt[field], field)
        for field in ("observed_payload_sha256", "observed_sha256")
        if field in receipt and receipt[field] not in (None, "")
    }
    if not expected_values or not observed_values:
        raise ValueError(
            "actual_import_receipt expected and observed payload digests are required"
        )
    if len(expected_values) != 1 or len(observed_values) != 1:
        raise ValueError("actual_import_receipt payload digest aliases disagree")
    expected_digest = expected_values.pop()
    observed_digest = observed_values.pop()
    if expected_digest != observed_digest:
        raise ValueError("actual_import_receipt expected and observed payload digests differ")

    embedded_digest = None
    if "receipt_sha256" in receipt and receipt["receipt_sha256"] not in (None, ""):
        embedded_digest = _sha256_field(receipt["receipt_sha256"], "receipt_sha256")
    top_level_digest = None
    if any(
        field in payload and payload[field] not in (None, "")
        for field in _ACTUAL_RECEIPT_DIGEST_FIELDS
    ):
        top_level_digest = _payload_sha256(
            payload,
            _ACTUAL_RECEIPT_DIGEST_FIELDS,
            "actual_import_receipt_sha256",
        )
    if top_level_digest is None and embedded_digest is None:
        raise ValueError("actual_import_receipt_sha256 is required")
    canonical_receipt = {
        key: value for key, value in receipt.items() if key not in _ACTUAL_RECEIPT_DIGEST_KEYS
    }
    computed_digest = _json_digest(canonical_receipt)
    for supplied_digest in (top_level_digest, embedded_digest):
        if supplied_digest is not None and supplied_digest != computed_digest:
            raise ValueError("Actual import receipt digest does not match its readback content")
    return receipt, computed_digest


def _legacy_recovery_digest(row: sqlite3.Row | dict[str, Any]) -> str:
    """Return the deterministic proof key for a pre-digest reconciliation row."""
    return _json_digest({
        "statement_reference": str(row["statement_reference"]),
        "card_code": str(row["card_code"]),
        "period_start": str(row["period_start"]),
        "period_end": str(row["period_end"]),
        "matched_count": int(row["matched_count"]),
        "statement_only_count": int(row["statement_only_count"]),
        "notification_only_count": int(row["notification_only_count"]),
    })


def _legacy_statement_content_digest(
    connection: sqlite3.Connection,
    *,
    statement_reference: str,
    card_code: str,
    period_start: date,
    period_end: date,
) -> str | None:
    """Rebuild a legacy content digest from persisted statement rows when possible."""
    rows = connection.execute(
        """
        SELECT * FROM cashback_events
        WHERE source = 'statement' AND statement_reference = ?
        ORDER BY source_event_id
        """,
        (statement_reference,),
    ).fetchall()
    if not rows:
        return None
    statement_events = []
    transaction_ids = []
    prefix = f"statement:{statement_reference}:"
    for row in rows:
        source_event_id = str(row["source_event_id"])
        if not source_event_id.startswith(prefix):
            raise ValueError("legacy statement rows have an invalid transaction identity")
        transaction_ids.append(source_event_id.removeprefix(prefix))
        statement_events.append(_normalize_event({
            "source_event_id": source_event_id,
            "occurred_at": row["occurred_at"],
            "card_code": row["card_code"],
            "amount": str(Decimal(int(row["amount_aed_minor"])) / Decimal(100)),
            "currency": row["currency"],
            "purchase_type": row["purchase_type"],
            "channel": row["channel"],
            "merchant": row["merchant"],
            "bucket_code": row["bucket_code"],
            "event_type": row["event_type"],
            "source": row["source"],
            "status": row["status"],
            "tags": json.loads(str(row["tags_json"] or "[]")),
            "confidence": row["confidence"],
            "review_required": row["review_required"],
            "reconciliation_status": row["reconciliation_status"],
            "statement_reference": row["statement_reference"],
            "email_reference": row["email_reference"],
            "document_url": row["document_url"],
            "reversal_of": row["reversal_of"],
            "decision_trace": json.loads(str(row["decision_trace_json"] or "[]")),
            "ai_trace": json.loads(str(row["ai_trace_json"] or "[]")),
        }))
    return _statement_content_digest(
        statement_events,
        transaction_ids,
        statement_reference=statement_reference,
        card_code=card_code,
        period_start=period_start,
        period_end=period_end,
    )


def _statement_content_digest(
    statement_events: Iterable[dict[str, Any]],
    transaction_ids: Iterable[str],
    *,
    statement_reference: str,
    card_code: str,
    period_start: date,
    period_end: date,
) -> str:
    """Hash normalized statement content in a stable transaction-id order."""
    content = []
    for transaction_id, event in zip(transaction_ids, statement_events, strict=True):
        content.append({
            "statement_transaction_id": transaction_id,
            "event": {
                key: value
                for key, value in event.items()
                if key not in {"source_event_id", "identity_key"}
            },
        })
    content.sort(key=lambda item: str(item["statement_transaction_id"]))
    return _json_digest({
        "statement_reference": statement_reference,
        "card_code": card_code,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "transactions": content,
    })

def _canonical_statement_events(
    payload: dict[str, Any],
    *,
    statement_reference: str,
    card_code: str,
    period_start: date,
    period_end: date,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows = payload.get("transactions")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("transactions must be a list of statement transaction objects")

    statement_events = []
    statement_transaction_ids = []
    transaction_ids: set[str] = set()
    for row in rows:
        transaction_id = str(row.get("statement_transaction_id") or "").strip()
        if not transaction_id:
            raise ValueError("statement_transaction_id is required for every statement transaction")
        if transaction_id in transaction_ids:
            raise ValueError(f"Duplicate statement_transaction_id: {transaction_id}")
        transaction_ids.add(transaction_id)
        event = _normalize_event({
            **row,
            "source_event_id": f"statement:{statement_reference}:{transaction_id}",
            "card_code": card_code,
            "source": "statement",
            "status": "ACTIVE",
            "confidence": 1,
            "reconciliation_status": "RECONCILED",
            "statement_reference": statement_reference,
        })
        occurred = date.fromisoformat(event["occurred_at"][:10])
        if occurred < period_start or occurred > period_end:
            raise ValueError(f"statement transaction {transaction_id} falls outside the statement period")
        statement_events.append(event)
        statement_transaction_ids.append(transaction_id)

    return statement_events, statement_transaction_ids


def _rank_statement_candidates(
    event: dict[str, Any],
    candidates: Iterable[dict[str, Any]],
) -> list[tuple[int, int, str]]:
    event_date = date.fromisoformat(event["occurred_at"][:10])
    ranked = []
    for candidate in candidates:
        if candidate["amount_aed_minor"] != event["amount_aed_minor"]:
            continue
        if (
            str(candidate["currency"] or "").strip().upper()
            != event["currency"]
        ):
            continue
        if _event_polarity(candidate["event_type"]) != _event_polarity(
            event["event_type"]
        ):
            continue
        candidate_date = date.fromisoformat(
            str(candidate["occurred_at"])[:10]
        )
        day_gap = abs((event_date - candidate_date).days)
        if day_gap > 3:
            continue
        merchant_score = _merchant_match_score(
            candidate["merchant"], event["merchant"]
        )
        if merchant_score == 0:
            continue
        ranked.append(
            (
                merchant_score,
                -day_gap,
                str(candidate["source_event_id"]),
            )
        )
    ranked.sort(reverse=True)
    return ranked


def _cursor_order(left: str, right: str) -> int:
    """Compare timestamp cursors when possible, otherwise their source ordering."""
    try:
        left_value = datetime.fromisoformat(_iso_datetime(left))
        right_value = datetime.fromisoformat(_iso_datetime(right))
    except ValueError:
        left_value = left
        right_value = right
    return (left_value > right_value) - (left_value < right_value)


def default_payment_intents() -> tuple[PaymentIntent, ...]:
    return payment_intents_from_config(load_program_configuration())


def _iso_datetime(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("occurred_at is required")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("occurred_at must include a timezone")
    return parsed.isoformat()


def _amount_minor(value: object) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("amount must be numeric") from error
    if not amount.is_finite():
        raise ValueError("amount must be finite")
    if amount <= 0:
        raise ValueError("amount must be greater than zero; use event_type REFUND for credits")
    try:
        if amount != amount.quantize(_MINOR_UNIT):
            raise ValueError("amount must have no more than two decimal places")
    except InvalidOperation as error:
        raise ValueError("amount must have no more than two decimal places") from error
    return int(amount * 100)


def _event_amount(source: dict[str, Any]) -> object:
    canonical = source.get("amount")
    legacy = source.get("amount_aed")
    if canonical in (None, "") and legacy in (None, ""):
        raise ValueError("amount is required")
    if canonical not in (None, "") and legacy not in (None, ""):
        try:
            canonical_amount = money(canonical)
            legacy_amount = money(legacy)
        except (InvalidOperation, ValueError) as error:
            raise ValueError("amount and legacy amount_aed must be numeric") from error
        if canonical_amount != legacy_amount:
            raise ValueError("amount and legacy amount_aed disagree")
    return canonical if canonical not in (None, "") else legacy


def _confidence(value: object) -> float:
    if value in (None, ""):
        return 1.0
    try:
        confidence = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("confidence must be numeric") from error
    if not math.isfinite(confidence) or confidence < 0 or confidence > 1:
        raise ValueError("confidence must be between 0 and 1")
    return confidence


def _boolean(value: object, *, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError("review_required must be a boolean")


def _trace_json(value: object, field_name: str) -> str:
    if value in (None, ""):
        return "[]"
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{field_name} must be a list of objects")
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _normalize_event(source: dict[str, Any]) -> dict[str, Any]:
    source_event_id = str(source.get("source_event_id") or "").strip()
    card_code = str(source.get("card_code") or "").strip().upper()
    if not source_event_id:
        raise ValueError("source_event_id is required")
    if not card_code:
        raise ValueError("card_code is required")
    status = str(source.get("status") or "ACTIVE").strip().upper()
    # Accept legacy envelopes while storing one user-facing transaction state.
    if status in {"PROVISIONAL", "CONFIRMED"}:
        status = "ACTIVE"
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {sorted(VALID_STATUSES)}")
    event_type = str(source.get("event_type") or "PURCHASE").strip().upper()
    if event_type not in VALID_EVENT_TYPES:
        raise ValueError(f"event_type must be one of {sorted(VALID_EVENT_TYPES)}")
    reversal_of = str(source.get("reversal_of") or "").strip() or None
    if event_type == "REVERSAL" and not reversal_of:
        raise ValueError("reversal_of is required for REVERSAL events")
    reconciliation_status = str(source.get("reconciliation_status") or "UNMATCHED").strip().upper()
    if reconciliation_status not in VALID_RECONCILIATION_STATUSES:
        raise ValueError(
            f"reconciliation_status must be one of {sorted(VALID_RECONCILIATION_STATUSES)}"
        )
    confidence = _confidence(source.get("confidence"))
    review_required = _boolean(source.get("review_required"), default=confidence < 0.8)
    tags = source.get("tags") or []
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("tags must be a list of strings")
    currency = str(source.get("currency") or "AED").strip().upper()
    if not _CURRENCY_CODE.fullmatch(currency):
        raise ValueError("currency must be a three-letter ISO code")
    normalized = {
        "source_event_id": source_event_id,
        "occurred_at": _iso_datetime(source.get("occurred_at")),
        "card_code": card_code,
        "amount_aed_minor": _amount_minor(_event_amount(source)),
        "currency": currency,
        "purchase_type": str(source.get("purchase_type") or "GENERAL").strip().upper() or "GENERAL",
        "channel": str(source.get("channel") or "UNKNOWN").strip().upper() or "UNKNOWN",
        "merchant": str(source.get("merchant") or "Unknown").strip() or "Unknown",
        "bucket_code": str(source.get("bucket_code") or "").strip().upper() or None,
        "event_type": event_type,
        "source": str(source.get("source") or "email").strip() or "email",
        "status": status,
        "tags_json": json.dumps(sorted(set(tags))),
        "confidence": confidence,
        "review_required": int(review_required),
        "reconciliation_status": reconciliation_status,
        "statement_reference": str(source.get("statement_reference") or "").strip() or None,
        "email_reference": str(source.get("email_reference") or "").strip() or None,
        "document_url": str(source.get("document_url") or "").strip() or None,
        "reversal_of": reversal_of,
        "decision_trace_json": _trace_json(source.get("decision_trace"), "decision_trace"),
        "ai_trace_json": _trace_json(source.get("ai_trace"), "ai_trace"),
    }
    identity = "|".join(
        (
            normalized["occurred_at"],
            normalized["card_code"],
            str(normalized["amount_aed_minor"]),
            normalized["currency"],
            normalized["event_type"],
            _merchant_key(normalized["merchant"]),
        )
    )
    normalized["identity_key"] = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return normalized


def _merchant_key(value: object) -> str:
    return _MERCHANT_TOKEN.sub(" ", str(value or "").upper()).strip()


def _merchant_match_score(candidate: object, statement: object) -> int:
    candidate_key = _merchant_key(candidate)
    statement_key = _merchant_key(statement)
    if not candidate_key or not statement_key:
        return 0
    if candidate_key == statement_key:
        return 2
    if candidate_key in statement_key or statement_key in candidate_key:
        return 1
    return 0


def _statement_collision_identity(event: dict[str, Any]) -> str:
    return hashlib.sha256(
        f"{event['identity_key']}|statement:{event['source_event_id']}".encode("utf-8")
    ).hexdigest()


def _event_polarity(value: object) -> str:
    return "CREDIT" if str(value).upper() in {"REFUND", "REVERSAL"} else "DEBIT"


class CashbackEventStore:
    """Small operational store for live reward events, not a finance ledger."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cashback_events (
                        source_event_id TEXT PRIMARY KEY,
                        occurred_at TEXT NOT NULL,
                        card_code TEXT NOT NULL,
                        amount_aed_minor INTEGER NOT NULL,
                        currency TEXT NOT NULL,
                        purchase_type TEXT NOT NULL,
                        channel TEXT NOT NULL,
                        merchant TEXT NOT NULL,
                        bucket_code TEXT,
                        event_type TEXT NOT NULL,
                        source TEXT NOT NULL,
                        status TEXT NOT NULL,
                        tags_json TEXT NOT NULL,
                        confidence REAL NOT NULL DEFAULT 1,
                        review_required INTEGER NOT NULL DEFAULT 0,
                        reconciliation_status TEXT NOT NULL DEFAULT 'UNMATCHED',
                        statement_reference TEXT,
                        email_reference TEXT,
                        document_url TEXT,
                        reversal_of TEXT,
                        identity_key TEXT,
                        decision_trace_json TEXT NOT NULL DEFAULT '[]',
                        ai_trace_json TEXT NOT NULL DEFAULT '[]',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS card_periods (
                        card_code TEXT NOT NULL,
                        period_start TEXT NOT NULL,
                        period_end TEXT NOT NULL,
                        statement_reference TEXT,
                        statement_sha256 TEXT NOT NULL DEFAULT '',
                        statement_content_sha256 TEXT NOT NULL DEFAULT '',
                        statement_evidence_reference TEXT,
                        statement_document_url TEXT,
                        actual_import_receipt_sha256 TEXT,
                        actual_verification_sha256 TEXT,
                        actual_import_verified INTEGER NOT NULL DEFAULT 0,
                        reconciliation_status TEXT NOT NULL DEFAULT 'PENDING',
                        status TEXT NOT NULL DEFAULT 'OPEN',
                        finalized_at TEXT,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(card_code, period_start, period_end)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ingest_state (
                        source TEXT PRIMARY KEY,
                        last_success_at TEXT NOT NULL,
                        scanned_count INTEGER NOT NULL,
                        accepted_count INTEGER NOT NULL,
                        cursor TEXT,
                        cursor_version INTEGER NOT NULL DEFAULT 0,
                        receipt_id TEXT,
                        receipt_sha256 TEXT,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ingest_receipts (
                        receipt_id TEXT PRIMARY KEY,
                        receipt_sha256 TEXT NOT NULL UNIQUE,
                        source TEXT NOT NULL,
                        completed_at TEXT NOT NULL,
                        cursor TEXT NOT NULL,
                        scanned_count INTEGER NOT NULL,
                        accepted_count INTEGER NOT NULL,
                        event_ids_json TEXT NOT NULL,
                        event_digests_json TEXT NOT NULL,
                        state TEXT NOT NULL DEFAULT 'READY',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        committed_at TEXT,
                        CHECK (state IN ('READY', 'COMMITTED'))
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS alert_acknowledgements (
                        alert_key TEXT PRIMARY KEY,
                        acknowledged INTEGER NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS reconciliation_runs (
                        statement_reference TEXT PRIMARY KEY,
                        card_code TEXT NOT NULL,
                        period_start TEXT NOT NULL,
                        period_end TEXT NOT NULL,
                        statement_sha256 TEXT NOT NULL DEFAULT '',
                        statement_content_sha256 TEXT NOT NULL DEFAULT '',
                        matched_count INTEGER NOT NULL,
                        statement_only_count INTEGER NOT NULL,
                        notification_only_count INTEGER NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS event_corrections (
                        correction_id TEXT PRIMARY KEY,
                        source_event_id TEXT NOT NULL,
                        changes_json TEXT NOT NULL,
                        reason TEXT,
                        correction_source TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(source_event_id) REFERENCES cashback_events(source_event_id)
                    )
                    """
                )
                self._migrate_event_columns(connection)
                self._migrate_ingest_state_columns(connection)
                self._migrate_period_columns(connection)
                self._migrate_reconciliation_columns(connection)
                connection.execute(
                    "UPDATE cashback_events SET status='ACTIVE' WHERE status IN ('PROVISIONAL', 'CONFIRMED')"
                )
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_cashback_events_identity ON cashback_events(identity_key) WHERE identity_key IS NOT NULL"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_cashback_events_status_occurred ON cashback_events(status, occurred_at, source_event_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_cashback_events_reconcile ON cashback_events(card_code, status, source, occurred_at, source_event_id)"
                )

    @staticmethod
    def _migrate_event_columns(connection: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(cashback_events)").fetchall()
        }
        additions = {
            "confidence": "REAL NOT NULL DEFAULT 1",
            "review_required": "INTEGER NOT NULL DEFAULT 0",
            "reconciliation_status": "TEXT NOT NULL DEFAULT 'UNMATCHED'",
            "statement_reference": "TEXT",
            "email_reference": "TEXT",
            "document_url": "TEXT",
            "reversal_of": "TEXT",
            "identity_key": "TEXT",
            "decision_trace_json": "TEXT NOT NULL DEFAULT '[]'",
            "ai_trace_json": "TEXT NOT NULL DEFAULT '[]'",
        }
        for column, definition in additions.items():
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE cashback_events ADD COLUMN {column} {definition}"
                )
        seen: set[str] = set()
        for row in connection.execute(
            "SELECT source_event_id, occurred_at, card_code, amount_aed_minor, currency, event_type, merchant FROM cashback_events WHERE identity_key IS NULL ORDER BY created_at, source_event_id"
        ).fetchall():
            canonical = "|".join(
                (
                    str(row["occurred_at"]),
                    str(row["card_code"]),
                    str(row["amount_aed_minor"]),
                    str(row["currency"]),
                    str(row["event_type"]),
                    _merchant_key(row["merchant"]),
                )
            )
            identity = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if identity in seen:
                identity = hashlib.sha256(
                    f"{canonical}|legacy:{row['source_event_id']}".encode("utf-8")
                ).hexdigest()
            seen.add(identity)
            connection.execute(
                "UPDATE cashback_events SET identity_key = ? WHERE source_event_id = ?",
                (identity, row["source_event_id"]),
            )

    @staticmethod
    def _migrate_ingest_state_columns(connection: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(ingest_state)").fetchall()
        }
        additions = {
            "cursor_version": "INTEGER NOT NULL DEFAULT 0",
            "receipt_id": "TEXT",
            "receipt_sha256": "TEXT",
        }
        for column, definition in additions.items():
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE ingest_state ADD COLUMN {column} {definition}"
                )

    @staticmethod
    def _migrate_period_columns(connection: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(card_periods)").fetchall()
        }
        additions = {
            "statement_sha256": "TEXT NOT NULL DEFAULT ''",
            "statement_content_sha256": "TEXT NOT NULL DEFAULT ''",
            "actual_import_receipt_sha256": "TEXT",
            "actual_verification_sha256": "TEXT",
        }
        for column, definition in additions.items():
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE card_periods ADD COLUMN {column} {definition}"
                )

    @staticmethod
    def _migrate_reconciliation_columns(connection: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(reconciliation_runs)").fetchall()
        }
        additions = {
            "statement_sha256": "TEXT NOT NULL DEFAULT ''",
            "statement_content_sha256": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in additions.items():
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE reconciliation_runs ADD COLUMN {column} {definition}"
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def upsert(self, events: Iterable[dict[str, Any]]) -> dict[str, int]:
        normalized = [_normalize_event(event) for event in events]
        if not normalized:
            raise ValueError("At least one event is required")
        inserted = 0
        updated = 0
        unchanged = 0
        duplicates = 0
        with closing(self._connect()) as connection:
            with connection:
                for event in normalized:
                    existing = connection.execute(
                        "SELECT * FROM cashback_events WHERE source_event_id = ?",
                        (event["source_event_id"],),
                    ).fetchone()
                    if existing:
                        differences = [
                            field
                            for field in EVENT_CANONICAL_FIELDS
                            if existing[field] != event[field]
                        ]
                        if differences:
                            raise ValueError(
                                "source_event_id already exists with different event fields: "
                                + ", ".join(differences)
                                + "; use the corrections path for intentional changes"
                            )
                        unchanged += 1
                        continue
                    identity_owner = connection.execute(
                        "SELECT source_event_id FROM cashback_events WHERE identity_key = ?",
                        (event["identity_key"],),
                    ).fetchone()
                    if identity_owner and identity_owner["source_event_id"] != event["source_event_id"]:
                        duplicates += 1
                        continue
                    columns = tuple(event)
                    placeholders = ", ".join("?" for _ in columns)
                    connection.execute(
                        f"""
                        INSERT INTO cashback_events ({', '.join(columns)})
                        VALUES ({placeholders})
                        """,
                        tuple(event[column] for column in columns),
                    )
                    inserted += 1
        return {
            "inserted": inserted,
            # ``updated`` is retained as an explicit, backwards-compatible
            # counter.  Source replays never update rows; callers can inspect
            # ``unchanged`` for exact idempotent replays.
            "updated": updated,
            "unchanged": unchanged,
            "duplicates": duplicates,
        }

    def validate(self, events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """Validate events without persisting them or exposing normalized payloads."""
        normalized = [_normalize_event(event) for event in events]
        if not normalized:
            raise ValueError("At least one event is required")
        return normalized

    @staticmethod
    def _ingest_fields(source: dict[str, Any]) -> tuple[str, str, int, int, str]:
        source_name = str(source.get("source") or "mailbox").strip()
        if not source_name:
            raise ValueError("source is required")
        completed_at = _iso_datetime(source.get("completed_at"))
        scanned_value = source.get("scanned_count")
        accepted_value = source.get("accepted_count")
        if (
            isinstance(scanned_value, bool)
            or not isinstance(scanned_value, int)
            or isinstance(accepted_value, bool)
            or not isinstance(accepted_value, int)
        ):
            raise ValueError("scanned_count and accepted_count must be integers")
        scanned_count = scanned_value
        accepted_count = accepted_value
        if scanned_count < 0 or accepted_count < 0 or accepted_count > scanned_count:
            raise ValueError("ingest counts must be non-negative and accepted cannot exceed scanned")
        cursor = str(source.get("cursor") or "").strip()
        if not cursor:
            raise ValueError("cursor is required")
        return source_name, completed_at, scanned_count, accepted_count, cursor

    def create_ingest_receipt(
        self,
        source: dict[str, Any],
        *,
        event_ids: Iterable[str] = (),
        event_digests: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Persist the exact service result that is eligible for one cursor commit."""
        source_name, completed_at, scanned_count, accepted_count, cursor = self._ingest_fields(source)
        raw_ids = [str(event_id).strip() for event_id in event_ids]
        raw_digests = [str(digest).strip() for digest in event_digests]
        if len(raw_ids) != len(raw_digests):
            raise ValueError("event ids and event digests must have equal cardinality")
        if any(not event_id for event_id in raw_ids) or any(not digest for digest in raw_digests):
            raise ValueError("event ids and event digests must be non-empty")
        if len(set(raw_ids)) != len(raw_ids):
            raise ValueError("event ids must be unique within an ingest receipt")
        if len(raw_ids) != accepted_count:
            raise ValueError("accepted_count must equal the number of event ids in the receipt")
        pairs = sorted(zip(raw_ids, raw_digests), key=lambda pair: pair[0])
        ids = [event_id for event_id, _ in pairs]
        digests = [digest for _, digest in pairs]
        payload = {
            "source": source_name,
            "completed_at": completed_at,
            "cursor": cursor,
            "scanned_count": scanned_count,
            "accepted_count": accepted_count,
            "event_ids": ids,
            "event_digests": digests,
        }
        receipt_sha256 = _json_digest(payload)
        receipt_id = f"cashback-ingest:{receipt_sha256}"
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM ingest_receipts WHERE receipt_id = ?",
                    (receipt_id,),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO ingest_receipts (
                            receipt_id, receipt_sha256, source, completed_at, cursor,
                            scanned_count, accepted_count, event_ids_json, event_digests_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            receipt_id,
                            receipt_sha256,
                            source_name,
                            completed_at,
                            cursor,
                            scanned_count,
                            accepted_count,
                            json.dumps(ids, separators=(",", ":")),
                            json.dumps(digests, separators=(",", ":")),
                        ),
                    )
                    state = "READY"
                else:
                    if existing["receipt_sha256"] != receipt_sha256:
                        raise IngestCursorConflict("service receipt identity collision")
                    state = str(existing["state"])
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "receipt_id": receipt_id,
            "receipt_sha256": receipt_sha256,
            **payload,
            "state": state,
        }

    def record_ingest_success(self, source: dict[str, Any]) -> dict[str, Any]:
        """Commit one registered service receipt and its cursor atomically."""
        source_name, completed_at, scanned_count, accepted_count, cursor = self._ingest_fields(source)
        service_receipt = source.get("service_receipt")
        if isinstance(service_receipt, dict):
            receipt_id = str(service_receipt.get("receipt_id") or "").strip()
            receipt_sha256 = str(service_receipt.get("receipt_sha256") or "").strip()
        else:
            receipt_id = str(
                source.get("service_receipt_id") or source.get("receipt_id") or ""
            ).strip()
            receipt_sha256 = str(
                source.get("service_receipt_sha256") or source.get("receipt_sha256") or ""
            ).strip()
        if not receipt_id or not receipt_sha256:
            raise IngestCursorConflict("exact service receipt is required")

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = connection.execute(
                    "SELECT * FROM ingest_receipts WHERE receipt_id = ?",
                    (receipt_id,),
                ).fetchone()
                if receipt is None or receipt["receipt_sha256"] != receipt_sha256:
                    raise IngestCursorConflict("service receipt is unknown or mismatched")
                expected = {
                    "source": receipt["source"],
                    "completed_at": receipt["completed_at"],
                    "cursor": receipt["cursor"],
                    "scanned_count": int(receipt["scanned_count"]),
                    "accepted_count": int(receipt["accepted_count"]),
                }
                observed = {
                    "source": source_name,
                    "completed_at": completed_at,
                    "cursor": cursor,
                    "scanned_count": scanned_count,
                    "accepted_count": accepted_count,
                }
                if observed != expected:
                    raise IngestCursorConflict("service receipt is not bound to the commit payload")

                current = connection.execute(
                    "SELECT * FROM ingest_state WHERE source = ?",
                    (source_name,),
                ).fetchone()
                if current is not None:
                    current_receipt_id = str(current["receipt_id"] or "")
                    if current_receipt_id == receipt_id and str(current["receipt_sha256"] or "") == receipt_sha256:
                        connection.commit()
                        return {
                            "source": source_name,
                            "last_success_at": completed_at,
                            "scanned_count": scanned_count,
                            "accepted_count": accepted_count,
                            "cursor": cursor,
                            "cursor_version": int(current["cursor_version"]),
                            "receipt_id": receipt_id,
                            "receipt_sha256": receipt_sha256,
                            "idempotent_replay": True,
                        }
                    current_completed = str(current["last_success_at"])
                    if _cursor_order(current_completed, completed_at) > 0:
                        raise IngestCursorConflict("completed_at is stale or regressive")
                    current_cursor = str(current["cursor"] or "")
                    if _cursor_order(current_completed, completed_at) == 0:
                        raise IngestCursorConflict("completed_at is already committed with another receipt")
                    if current_cursor and _cursor_order(current_cursor, cursor) >= 0:
                        raise IngestCursorConflict("cursor is stale or regressive")
                    next_version = int(current["cursor_version"]) + 1
                else:
                    next_version = 1

                connection.execute(
                    """
                    INSERT INTO ingest_state (
                        source, last_success_at, scanned_count, accepted_count, cursor,
                        cursor_version, receipt_id, receipt_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source) DO UPDATE SET
                        last_success_at=excluded.last_success_at,
                        scanned_count=excluded.scanned_count,
                        accepted_count=excluded.accepted_count,
                        cursor=excluded.cursor,
                        cursor_version=excluded.cursor_version,
                        receipt_id=excluded.receipt_id,
                        receipt_sha256=excluded.receipt_sha256,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        source_name,
                        completed_at,
                        scanned_count,
                        accepted_count,
                        cursor,
                        next_version,
                        receipt_id,
                        receipt_sha256,
                    ),
                )
                connection.execute(
                    """
                    UPDATE ingest_receipts
                    SET state='COMMITTED', committed_at=CURRENT_TIMESTAMP
                    WHERE receipt_id = ?
                    """,
                    (receipt_id,),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "source": source_name,
            "last_success_at": completed_at,
            "scanned_count": scanned_count,
            "accepted_count": accepted_count,
            "cursor": cursor,
            "cursor_version": next_version,
            "receipt_id": receipt_id,
            "receipt_sha256": receipt_sha256,
            "idempotent_replay": False,
        }

    def ingest_state(self, source: str = "outlook") -> dict[str, Any]:
        source_name = str(source or "outlook").strip()
        if not source_name:
            raise ValueError("source is required")
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT source, last_success_at, scanned_count, accepted_count, cursor,
                       cursor_version, receipt_id, receipt_sha256
                FROM ingest_state
                WHERE source = ?
                """,
                (source_name,),
            ).fetchone()
        if row is None:
            return {
                "source": source_name,
                "last_success_at": None,
                "scanned_count": 0,
                "accepted_count": 0,
                "cursor": None,
                "cursor_version": 0,
                "receipt_id": None,
                "receipt_sha256": None,
            }
        return dict(row)

    def set_alert_acknowledgement(self, alert_key: object, acknowledged: object) -> dict[str, Any]:
        key = str(alert_key or "").strip()
        if not key or len(key) > 160:
            raise ValueError("alert_key must be between 1 and 160 characters")
        is_acknowledged = _boolean(acknowledged)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO alert_acknowledgements (alert_key, acknowledged)
                    VALUES (?, ?)
                    ON CONFLICT(alert_key) DO UPDATE SET
                        acknowledged=excluded.acknowledged,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (key, int(is_acknowledged)),
                )
        return {"alert_key": key, "acknowledged": is_acknowledged}

    def alert_acknowledgements(self) -> list[str]:
        with closing(self._connect()) as connection:
            records = connection.execute(
                """
                SELECT alert_key
                FROM alert_acknowledgements
                WHERE acknowledged = 1
                ORDER BY alert_key
                """
            ).fetchall()
        return [str(row["alert_key"]) for row in records]

    def reconcile_statement(self, payload: dict[str, Any]) -> dict[str, Any]:
        statement_reference = str(payload.get("statement_reference") or "").strip()
        card_code = str(payload.get("card_code") or "").strip().upper()
        if not statement_reference or not card_code:
            raise ValueError("statement_reference and card_code are required")
        statement_sha256 = _payload_sha256(
            payload,
            _STATEMENT_DIGEST_FIELDS,
            "statement_sha256",
        )
        try:
            period_start = date.fromisoformat(str(payload.get("period_start")))
            period_end = date.fromisoformat(str(payload.get("period_end")))
        except ValueError as error:
            raise ValueError("period_start and period_end must be ISO dates") from error
        if period_end < period_start:
            raise ValueError("period_end cannot be before period_start")
        statement_events, statement_transaction_ids = _canonical_statement_events(
            payload,
            statement_reference=statement_reference,
            card_code=card_code,
            period_start=period_start,
            period_end=period_end,
        )

        statement_content_sha256 = _statement_content_digest(
            statement_events,
            statement_transaction_ids,
            statement_reference=statement_reference,
            card_code=card_code,
            period_start=period_start,
            period_end=period_end,
        )
        if any(
            field in payload and payload[field] not in (None, "")
            for field in _STATEMENT_CONTENT_DIGEST_FIELDS
        ):
            supplied_content_sha256 = _payload_sha256(
                payload,
                _STATEMENT_CONTENT_DIGEST_FIELDS,
                "statement_content_sha256",
            )
            if supplied_content_sha256 != statement_content_sha256:
                raise ValueError("statement content digest does not match canonical content")

        range_start, range_end = _date_range_bounds(period_start, period_end)
        with closing(self._connect()) as connection:
            with connection:
                prior = connection.execute(
                    "SELECT * FROM reconciliation_runs WHERE statement_reference = ?",
                    (statement_reference,),
                ).fetchone()
                if prior:
                    if (
                        prior["card_code"] != card_code
                        or prior["period_start"] != period_start.isoformat()
                        or prior["period_end"] != period_end.isoformat()
                    ):
                        raise ValueError("statement_reference was already used for a different card or period")
                    prior_statement_sha256 = str(prior["statement_sha256"] or "")
                    prior_content_sha256 = str(prior["statement_content_sha256"] or "")
                    if not prior_statement_sha256 and not prior_content_sha256:
                        recovery_digest = payload.get("legacy_recovery_digest")
                        if recovery_digest in (None, ""):
                            raise ValueError(
                                "legacy reconciliation digest recovery proof is required"
                            )
                        if _sha256_field(
                            recovery_digest, "legacy_recovery_digest"
                        ) != _legacy_recovery_digest(prior):
                            raise ValueError("legacy reconciliation digest recovery proof is invalid")
                        persisted_content_sha256 = _legacy_statement_content_digest(
                            connection,
                            statement_reference=statement_reference,
                            card_code=card_code,
                            period_start=period_start,
                            period_end=period_end,
                        )
                        if persisted_content_sha256 is None:
                            if (
                                prior["matched_count"]
                                or prior["statement_only_count"]
                                or prior["notification_only_count"]
                                or statement_events
                            ):
                                raise ValueError(
                                    "legacy reconciliation statement content is unavailable"
                                )
                        elif persisted_content_sha256 != statement_content_sha256:
                            raise ValueError(
                                "legacy reconciliation statement content does not match persisted rows"
                            )
                        connection.execute(
                            """
                            UPDATE reconciliation_runs
                            SET statement_sha256 = ?, statement_content_sha256 = ?
                            WHERE statement_reference = ?
                            """,
                            (statement_sha256, statement_content_sha256, statement_reference),
                        )
                        return {
                            "statement_reference": statement_reference,
                            "card_code": prior["card_code"],
                            "statement_sha256": statement_sha256,
                            "statement_content_sha256": statement_content_sha256,
                            "matched": prior["matched_count"],
                            "statement_only": prior["statement_only_count"],
                            "notification_only": prior["notification_only_count"],
                            "idempotent_replay": True,
                            "legacy_digest_backfilled": True,
                        }
                    if not prior_statement_sha256 or not prior_content_sha256:
                        raise ValueError("legacy reconciliation digest columns are incomplete")
                    if (
                        prior_statement_sha256 != statement_sha256
                        or prior_content_sha256 != statement_content_sha256
                    ):
                        raise ValueError(
                            "statement_reference was already used for different statement content or digest"
                        )
                    return {
                        "statement_reference": statement_reference,
                        "card_code": prior["card_code"],
                        "statement_sha256": prior["statement_sha256"],
                        "statement_content_sha256": prior["statement_content_sha256"],
                        "matched": prior["matched_count"],
                        "statement_only": prior["statement_only_count"],
                        "notification_only": prior["notification_only_count"],
                        "idempotent_replay": True,
                    }

                candidates = [
                    dict(row)
                    for row in connection.execute(
                        """
                        SELECT * FROM cashback_events
                        WHERE card_code = ? AND status = 'ACTIVE' AND source != 'statement'
                          AND occurred_at >= ? AND occurred_at < ?
                        ORDER BY occurred_at, source_event_id
                        """,
                        (card_code, range_start, range_end),
                    ).fetchall()
                ]
                remaining = {row["source_event_id"]: row for row in candidates}
                matched = 0
                statement_only = 0
                for event in statement_events:
                    ranked = _rank_statement_candidates(event, remaining.values())
                    unique_match = bool(
                        len(ranked) == 1
                        and ranked[0][0] > 0
                    )
                    if unique_match:
                        source_event_id = ranked[0][2]
                        connection.execute(
                            """
                            UPDATE cashback_events
                            SET status='ACTIVE', reconciliation_status='RECONCILED',
                                statement_reference=?, updated_at=CURRENT_TIMESTAMP
                            WHERE source_event_id=?
                            """,
                            (statement_reference, source_event_id),
                        )
                        remaining.pop(source_event_id)
                        matched += 1
                    else:
                        existing = connection.execute(
                            "SELECT source_event_id FROM cashback_events WHERE identity_key = ?",
                            (event["identity_key"],),
                        ).fetchone()
                        # A duplicate identity cannot override a compatible-candidate
                        # collision.
                        if existing and not ranked:
                            connection.execute(
                                "UPDATE cashback_events SET status='ACTIVE', reconciliation_status='RECONCILED', statement_reference=?, updated_at=CURRENT_TIMESTAMP WHERE source_event_id=?",
                                (statement_reference, existing["source_event_id"]),
                            )
                            matched += 1
                            continue
                        statement_event = (
                            {
                                **event,
                                "identity_key": _statement_collision_identity(event),
                            }
                            if existing
                            else event
                        )
                        columns = tuple(statement_event)
                        connection.execute(
                            f"INSERT INTO cashback_events ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                            tuple(statement_event[column] for column in columns),
                        )
                        statement_only += 1

                for source_event_id in remaining:
                    connection.execute(
                        """
                        UPDATE cashback_events
                        SET status='IGNORED', reconciliation_status='VARIANCE',
                            statement_reference=?, review_required=1, updated_at=CURRENT_TIMESTAMP
                        WHERE source_event_id=?
                        """,
                        (statement_reference, source_event_id),
                    )
                notification_only = len(remaining)
                connection.execute(
                    """
                    INSERT INTO reconciliation_runs (
                        statement_reference, card_code, period_start, period_end,
                        statement_sha256, statement_content_sha256,
                        matched_count, statement_only_count, notification_only_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        statement_reference,
                        card_code,
                        period_start.isoformat(),
                        period_end.isoformat(),
                        statement_sha256,
                        statement_content_sha256,
                        matched,
                        statement_only,
                        notification_only,
                    ),
                )
        return {
            "statement_reference": statement_reference,
            "card_code": card_code,
            "statement_sha256": statement_sha256,
            "statement_content_sha256": statement_content_sha256,
            "matched": matched,
            "statement_only": statement_only,
            "notification_only": notification_only,
            "idempotent_replay": False,
        }

    def correct_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        correction_id = str(payload.get("correction_id") or "").strip()
        source_event_id = str(payload.get("source_event_id") or "").strip()
        changes = payload.get("changes")
        if not correction_id or not source_event_id:
            raise ValueError("correction_id and source_event_id are required")
        if not isinstance(changes, dict) or not changes:
            raise ValueError("changes must be a non-empty object")
        invalid = set(changes) - CORRECTABLE_EVENT_FIELDS
        if invalid:
            raise ValueError("unsupported correction fields: " + ", ".join(sorted(invalid)))
        reason = str(payload.get("reason") or "").strip() or None
        correction_source = str(payload.get("source") or "manual").strip() or "manual"
        if correction_source.casefold().startswith(("ai", "codex")):
            protected = set(changes) - AI_CORRECTABLE_EVENT_FIELDS
            if protected:
                raise ValueError(
                    "AI corrections cannot modify protected event fields: "
                    + ", ".join(sorted(protected))
                )
        canonical_changes = json.dumps(changes, sort_keys=True)
        with closing(self._connect()) as connection:
            with connection:
                prior = connection.execute(
                    "SELECT * FROM event_corrections WHERE correction_id = ?",
                    (correction_id,),
                ).fetchone()
                if prior:
                    if prior["source_event_id"] != source_event_id or prior["changes_json"] != canonical_changes:
                        raise ValueError("correction_id was already used for different changes")
                    return {
                        "correction_id": correction_id,
                        "source_event_id": source_event_id,
                        "idempotent_replay": True,
                    }
                row = connection.execute(
                    "SELECT * FROM cashback_events WHERE source_event_id = ?",
                    (source_event_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Unknown source_event_id: {source_event_id}")
                if (
                    (
                        row["statement_reference"]
                        or row["reconciliation_status"] in {"MATCHED", "RECONCILED"}
                    )
                    and set(changes) & RECONCILIATION_FACT_FIELDS
                ):
                    raise ValueError(
                        "authoritative reconciliation facts cannot be corrected in place; "
                        "submit a new statement reconciliation"
                    )
                source = {
                    "source_event_id": source_event_id,
                    "occurred_at": row["occurred_at"],
                    "card_code": row["card_code"],
                    "amount_aed": Decimal(row["amount_aed_minor"]) / Decimal("100"),
                    "currency": row["currency"],
                    "purchase_type": row["purchase_type"],
                    "channel": row["channel"],
                    "merchant": row["merchant"],
                    "bucket_code": row["bucket_code"],
                    "event_type": row["event_type"],
                    "source": row["source"],
                    "status": row["status"],
                    "tags": json.loads(row["tags_json"]),
                    "confidence": row["confidence"],
                    "review_required": bool(row["review_required"]),
                    "reconciliation_status": row["reconciliation_status"],
                    "statement_reference": row["statement_reference"],
                    "email_reference": row["email_reference"],
                    "document_url": row["document_url"],
                    "reversal_of": row["reversal_of"],
                    "decision_trace": json.loads(str(row["decision_trace_json"] or "[]")),
                    "ai_trace": json.loads(str(row["ai_trace_json"] or "[]")),
                }
                normalized = _normalize_event({**source, **changes})
                identity_owner = connection.execute(
                    "SELECT source_event_id FROM cashback_events WHERE identity_key = ?",
                    (normalized["identity_key"],),
                ).fetchone()
                if identity_owner and identity_owner["source_event_id"] != source_event_id:
                    raise ValueError(
                        "correction would collide with an existing source_event_id"
                    )
                assignments = ", ".join(
                    f"{column} = ?" for column in normalized if column != "source_event_id"
                )
                connection.execute(
                    f"UPDATE cashback_events SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE source_event_id = ?",
                    tuple(
                        normalized[column]
                        for column in normalized
                        if column != "source_event_id"
                    )
                    + (source_event_id,),
                )
                connection.execute(
                    """
                    INSERT INTO event_corrections (
                        correction_id, source_event_id, changes_json, reason, correction_source
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (correction_id, source_event_id, canonical_changes, reason, correction_source),
                )
        return {
            "correction_id": correction_id,
            "source_event_id": source_event_id,
            "idempotent_replay": False,
        }

    def finalize_period(
        self,
        payload: dict[str, Any],
        *,
        program_config_path: Path | None = None,
    ) -> dict[str, Any]:
        statement_reference = str(payload.get("statement_reference") or "").strip()
        evidence_reference = str(payload.get("statement_evidence_reference") or "").strip()
        document_url = str(payload.get("statement_document_url") or "").strip()
        if not statement_reference or not evidence_reference or not document_url:
            raise ValueError(
                "statement_reference, statement_evidence_reference, and statement_document_url are required"
            )
        statement_sha256 = _payload_sha256(
            payload,
            _STATEMENT_DIGEST_FIELDS,
            "statement_sha256",
        )
        supplied_content_sha256 = None
        if any(
            field in payload and payload[field] not in (None, "")
            for field in _STATEMENT_CONTENT_DIGEST_FIELDS
        ):
            supplied_content_sha256 = _payload_sha256(
                payload,
                _STATEMENT_CONTENT_DIGEST_FIELDS,
                "statement_content_sha256",
            )
        actual_import_receipt, actual_import_receipt_sha256 = _trusted_actual_receipt(payload)
        if "actual_import_verified" in payload and not _boolean(
            payload.get("actual_import_verified")
        ):
            raise ValueError("actual_import_verified cannot replace an Actual import receipt digest")
        acknowledge_variances = _boolean(payload.get("acknowledge_variances"), default=False)
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                run = connection.execute(
                    "SELECT * FROM reconciliation_runs WHERE statement_reference = ?",
                    (statement_reference,),
                ).fetchone()
                if run is None:
                    raise ValueError("A successful statement reconciliation is required before finalization")
                if str(run["statement_sha256"] or "") != statement_sha256:
                    raise ValueError("statement digest does not match the reconciliation receipt")
                if (
                    str(actual_import_receipt["period_start"]) != str(run["period_start"])
                    or str(actual_import_receipt["period_end"]) != str(run["period_end"])
                ):
                    raise ValueError(
                        "actual_import_receipt period does not match the reconciliation receipt"
                    )
                receipt_card = str(actual_import_receipt.get("card_code") or "").strip().upper()
                if not receipt_card:
                    raise ValueError("actual_import_receipt.card_code is required")
                if receipt_card != str(run["card_code"]).strip().upper():
                    raise ValueError(
                        "actual_import_receipt account identity/card does not match the reconciled card"
                    )
                if supplied_content_sha256 is not None and supplied_content_sha256 != str(
                    run["statement_content_sha256"] or ""
                ):
                    raise ValueError(
                        "statement content digest does not match the reconciliation receipt"
                    )
                if run["notification_only_count"] and not acknowledge_variances:
                    raise ValueError(
                        "Unresolved notification variances require explicit acknowledgement before finalization"
                    )
                existing = connection.execute(
                    "SELECT * FROM card_periods WHERE card_code=? AND period_start=? AND period_end=?",
                    (run["card_code"], run["period_start"], run["period_end"]),
                ).fetchone()
                if existing and existing["status"] == "FINALIZED":
                    if (
                        existing["statement_reference"] != statement_reference
                        or str(existing["statement_sha256"] or "") != statement_sha256
                        or str(existing["statement_content_sha256"] or "")
                        != str(run["statement_content_sha256"] or "")
                        or existing["statement_evidence_reference"] != evidence_reference
                        or existing["statement_document_url"] != document_url
                        or str(existing["actual_import_receipt_sha256"] or "")
                        != actual_import_receipt_sha256
                        or str(existing["actual_verification_sha256"] or "")
                        != actual_import_receipt_sha256
                    ):
                        raise ValueError(
                            "finalized statement reference was already used for different content, digest, or evidence"
                        )
                    return {
                        "close_id": _close_identifier(
                            str(existing["card_code"]),
                            str(existing["period_start"]),
                            str(existing["period_end"]),
                        ),
                        "card_code": existing["card_code"],
                        "period_start": existing["period_start"],
                        "period_end": existing["period_end"],
                        "status": existing["status"],
                        "statement_reference": existing["statement_reference"],
                        "statement_sha256": existing["statement_sha256"],
                        "statement_content_sha256": existing["statement_content_sha256"],
                        "statement_evidence_reference": existing["statement_evidence_reference"],
                        "statement_document_url": existing["statement_document_url"],
                        "actual_import_receipt_sha256": existing["actual_import_receipt_sha256"],
                        "actual_verification_sha256": existing["actual_verification_sha256"],
                        "idempotent_replay": True,
                    }
                reconciliation_status = (
                    "RECONCILED_WITH_ACKNOWLEDGED_VARIANCES"
                    if run["notification_only_count"]
                    else "RECONCILED"
                )
                connection.execute(
                    """
                    INSERT INTO card_periods (
                        card_code, period_start, period_end, statement_reference,
                        statement_sha256, statement_content_sha256,
                        statement_evidence_reference, statement_document_url,
                        actual_import_receipt_sha256, actual_verification_sha256,
                        actual_import_verified,
                        reconciliation_status, status, finalized_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'FINALIZED', ?)
                    ON CONFLICT(card_code, period_start, period_end) DO UPDATE SET
                        statement_reference=excluded.statement_reference,
                        statement_sha256=excluded.statement_sha256,
                        statement_content_sha256=excluded.statement_content_sha256,
                        statement_evidence_reference=excluded.statement_evidence_reference,
                        statement_document_url=excluded.statement_document_url,
                        actual_import_receipt_sha256=excluded.actual_import_receipt_sha256,
                        actual_verification_sha256=excluded.actual_verification_sha256,
                        actual_import_verified=1,
                        reconciliation_status=excluded.reconciliation_status,
                        status='FINALIZED', finalized_at=excluded.finalized_at,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        run["card_code"], run["period_start"], run["period_end"],
                        statement_reference, statement_sha256, run["statement_content_sha256"],
                        evidence_reference, document_url,
                        actual_import_receipt_sha256, actual_import_receipt_sha256,
                        reconciliation_status, now,
                    ),
                )
                next_start = date.fromisoformat(run["period_end"]) + timedelta(days=1)
                configuration = load_program_configuration(program_config_path)
                next_program = next(
                    (
                        item
                        for item in programs_from_config(configuration, next_start)
                        if item.card == run["card_code"]
                    ),
                    None,
                )
                if next_program is None:
                    raise ValueError(f"No active cashback programme for {run['card_code']} on {next_start}")
                calculated_start, next_end = statement_period(
                    next_start,
                    next_program.statement_close_day,
                )
                if calculated_start != next_start:
                    raise ValueError("Configured statement cycle does not continue from the reconciled period")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO card_periods (
                        card_code, period_start, period_end, status
                    ) VALUES (?, ?, ?, 'OPEN')
                    """,
                    (run["card_code"], next_start.isoformat(), next_end.isoformat()),
                )
        return {
            "close_id": _close_identifier(
                str(run["card_code"]),
                str(run["period_start"]),
                str(run["period_end"]),
            ),
            "card_code": run["card_code"],
            "period_start": run["period_start"],
            "period_end": run["period_end"],
            "status": "FINALIZED",
            "statement_reference": statement_reference,
            "statement_sha256": statement_sha256,
            "statement_content_sha256": run["statement_content_sha256"],
            "statement_evidence_reference": evidence_reference,
            "statement_document_url": document_url,
            "actual_import_receipt_sha256": actual_import_receipt_sha256,
            "actual_verification_sha256": actual_import_receipt_sha256,
            "reconciliation_status": reconciliation_status,
            "idempotent_replay": False,
        }

    def period_rows(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM card_periods ORDER BY period_end DESC, card_code"
            ).fetchall()
        return [dict(row) for row in rows]

    def rows(self, start: date, end: date) -> list[dict[str, Any]]:
        range_start, range_end = _date_range_bounds(start, end)
        with closing(self._connect()) as connection:
            records = connection.execute(
                """
                SELECT * FROM cashback_events
                WHERE occurred_at >= ? AND occurred_at < ?
                  AND status = 'ACTIVE'
                ORDER BY occurred_at, source_event_id
                """,
                (range_start, range_end),
            ).fetchall()
        return [dict(row) for row in records]

    def review_queue(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return unresolved active events for a separate Codex enrichment job."""
        if limit < 1 or limit > 200:
            raise ValueError("review queue limit must be between 1 and 200")
        with closing(self._connect()) as connection:
            records = connection.execute(
                """
                SELECT * FROM cashback_events
                WHERE status = 'ACTIVE'
                  AND review_required = 1
                ORDER BY occurred_at, source_event_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "source_event_id": row["source_event_id"],
                "occurred_at": row["occurred_at"],
                "card_code": row["card_code"],
                "amount_aed": str(Decimal(row["amount_aed_minor"]) / Decimal("100")),
                "currency": row["currency"],
                "purchase_type": row["purchase_type"],
                "channel": row["channel"],
                "merchant": row["merchant"],
                "bucket_code": row["bucket_code"],
                "event_type": row["event_type"],
                "tags": json.loads(row["tags_json"]),
                "confidence": row["confidence"],
                "review_required": bool(row["review_required"]),
                "email_reference": row["email_reference"],
                "document_url": row["document_url"],
                "decision_trace": json.loads(str(row["decision_trace_json"] or "[]")),
                "ai_trace": json.loads(str(row["ai_trace_json"] or "[]")),
            }
            for row in records
        ]

    def stats(self, source: str | None = None) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS event_count,
                       SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END) AS live_event_count,
                       SUM(CASE WHEN reconciliation_status = 'VARIANCE' THEN 1 ELSE 0 END) AS variance_count
                FROM cashback_events
                """
            ).fetchone()
            last_event = connection.execute(
                """
                SELECT occurred_at
                FROM cashback_events
                ORDER BY julianday(occurred_at) DESC, occurred_at DESC
                LIMIT 1
                """
            ).fetchone()
            ingest_query = """
                SELECT source, last_success_at, scanned_count, accepted_count, cursor
                FROM ingest_state
            """
            ingest_parameters: tuple[object, ...] = ()
            if source:
                ingest_query += " WHERE source = ?"
                ingest_parameters = (source,)
            ingest_query += " ORDER BY julianday(last_success_at) DESC, last_success_at DESC LIMIT 1"
            ingest = connection.execute(ingest_query, ingest_parameters).fetchone()
            correction_count = connection.execute(
                "SELECT COUNT(*) AS count FROM event_corrections"
            ).fetchone()["count"]
        result = dict(row)
        result["last_event_at"] = last_event["occurred_at"] if last_event else None
        result["live_event_count"] = result["live_event_count"] or 0
        result["variance_count"] = result["variance_count"] or 0
        result["last_successful_ingest_at"] = ingest["last_success_at"] if ingest else None
        result["last_ingest_source"] = ingest["source"] if ingest else None
        result["last_scan_count"] = ingest["scanned_count"] if ingest else 0
        result["last_accepted_count"] = ingest["accepted_count"] if ingest else 0
        result["last_ingest_cursor"] = ingest["cursor"] if ingest else None
        result["acknowledged_alerts"] = self.alert_acknowledgements()
        result["correction_count"] = correction_count
        result["card_periods"] = self.period_rows()
        return result


def events_to_transactions(
    rows: Iterable[dict[str, Any]],
    programs: Iterable[Any],
) -> list[Transaction]:
    transactions = []
    for row in rows:
        occurred_at = datetime.fromisoformat(str(row["occurred_at"]))
        event_type = str(row["event_type"])
        transaction_type = "REFUND" if event_type == "REVERSAL" else event_type
        purchase_type = str(row["purchase_type"])
        channel = str(row["channel"])
        currency = str(row["currency"])
        card = str(row["card_code"])
        transactions.append(
            Transaction(
                transaction_id=str(row["source_event_id"]),
                transaction_at=occurred_at,
                card=card,
                account=card,
                merchant_raw=str(row["merchant"]),
                vendor=str(row["merchant"]),
                amount_aed=Decimal(int(row["amount_aed_minor"])) / Decimal("100"),
                currency=currency,
                channel=channel,
                source_type=str(row["source"]),
                category=purchase_type,
                transaction_type=transaction_type,
                reward_bucket=(
                    row["bucket_code"]
                    or configured_reward_bucket(programs, card, purchase_type, channel, currency)
                ),
                tags=set(json.loads(str(row["tags_json"]))),
                review_required=bool(row["review_required"]),
                is_refund=event_type in {"REFUND", "REVERSAL"},
                metadata={
                    "cashback_status": row["status"],
                    "cashback_event_type": event_type,
                    "confidence": row["confidence"],
                    "reconciliation_status": row["reconciliation_status"],
                    "statement_reference": row["statement_reference"],
                    "email_reference": row["email_reference"],
                    "document_url": row["document_url"],
                    "reversal_of": row["reversal_of"],
                    "decision_trace": json.loads(str(row["decision_trace_json"] or "[]")),
                    "ai_trace": json.loads(str(row["ai_trace_json"] or "[]")),
                },
            )
        )
    return transactions


def build_live_dashboard(
    store: CashbackEventStore,
    as_of: date,
    *,
    stale_after_minutes: int = 90,
    program_config_path: Path | None = None,
    ingest_source: str | None = None,
) -> dict[str, Any]:
    if stale_after_minutes <= 0:
        raise ValueError("stale_after_minutes must be positive")
    configuration = load_program_configuration(program_config_path, as_of=as_of)
    programs = programs_from_config(configuration, as_of, as_of=as_of)
    periods = {
        program.card: statement_period(as_of, program.statement_close_day)
        for program in programs
    }
    event_rows = []
    for card, (period_start, period_end) in periods.items():
        event_rows.extend(
            row
            for row in store.rows(period_start, min(as_of, period_end))
            if row["card_code"] == card
        )
    transactions = events_to_transactions(event_rows, programs)
    result = cashback_dashboard(
        programs,
        transactions,
        as_of,
        payment_intents_from_config(configuration),
        periods_by_card=periods,
        routing_profiles=configuration.get("routing_profiles") or (),
        route_policies=configuration.get("route_policies") or None,
        base_currency=str(configuration.get("currency") or "AED"),
    )
    result["profile"] = configuration.get("profile") or {}
    stats = store.stats(ingest_source)
    last_ingest = stats.get("last_successful_ingest_at")
    stale = True
    if last_ingest:
        parsed = datetime.fromisoformat(str(last_ingest).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        age_seconds = (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds()
        stale = age_seconds > stale_after_minutes * 60
    result["data_status"] = {
        "mode": "LIVE_TRANSACTION_EVENTS",
        "generated_at": datetime.now(UTC).isoformat(),
        "stale_after_minutes": stale_after_minutes,
        "is_stale": stale,
        **stats,
    }
    return result


def write_dashboard(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)
