from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from .actual_snapshot import cashback_dashboard, eligible_card_codes
from .cashback import (
    PaymentIntent,
    configured_reward_bucket,
    load_program_configuration,
    payment_intents_from_config,
    programs_from_config,
    statement_period,
)
from .models import StatementReceipt, Transaction, money
from .sync_health import scheduled_sync_health
from .transaction_semantics import CASHBACK_TOPICS
from .statement_cycles import (
    BANK_CLOSED,
    PROCESSING_STATES,
    RECONCILIATION_STATES,
    receipt_period_bounds,
    normalize_statement_receipt,
    receipt_view,
)

ACTIVE_STATUSES = frozenset({"ACTIVE"})
VALID_STATUSES = ACTIVE_STATUSES | {"IGNORED", "REVERSED"}
VALID_EVENT_TYPES = CASHBACK_TOPICS
VALID_RECONCILIATION_STATUSES = frozenset(
    {"UNMATCHED", "MATCHED", "VARIANCE", "RECONCILED"}
)
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
_MERCHANT_TOKEN = re.compile(r"[^A-Z0-9]+")
_LEGACY_DATE_PERIOD = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
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


def _json_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()


def _sha256_field(value: object, field_name: str) -> str:
    """Normalize a SHA-256 field while keeping the persisted form unambiguous."""
    digest = str(value or "").strip().casefold()
    if digest.startswith("sha256:"):
        digest = digest[7:]
    if not _SHA256.fullmatch(digest):
        raise ValueError(f"{field_name} must be a SHA-256 hex digest")
    return digest


def _payload_sha256(
    payload: dict[str, Any], fields: tuple[str, ...], label: str
) -> str:
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
            raise ValueError(
                f"actual_import_receipt.{field} must be a non-empty identity"
            )
    version = receipt["verification_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError(
            "actual_import_receipt.verification_version must be a positive integer"
        )
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
            raise ValueError(
                f"actual_import_receipt.{field} must be an ISO date"
            ) from error
    if date.fromisoformat(str(receipt["period_end"])) < date.fromisoformat(
        str(receipt["period_start"])
    ):
        raise ValueError(
            "actual_import_receipt period_end cannot be before period_start"
        )
    verified_at = receipt["verified_at"]
    if not isinstance(verified_at, str) or not verified_at.strip():
        raise ValueError("actual_import_receipt.verified_at must be an ISO timestamp")
    try:
        datetime.fromisoformat(verified_at)
    except ValueError as error:
        raise ValueError(
            "actual_import_receipt.verified_at must be an ISO timestamp"
        ) from error

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
        raise ValueError(
            "actual_import_receipt expected and observed payload digests differ"
        )

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
        key: value
        for key, value in receipt.items()
        if key not in _ACTUAL_RECEIPT_DIGEST_KEYS
    }
    computed_digest = _json_digest(canonical_receipt)
    for supplied_digest in (top_level_digest, embedded_digest):
        if supplied_digest is not None and supplied_digest != computed_digest:
            raise ValueError(
                "Actual import receipt digest does not match its readback content"
            )
    return receipt, computed_digest


def _legacy_recovery_digest(row: sqlite3.Row | dict[str, Any]) -> str:
    """Return the deterministic proof key for a pre-digest reconciliation row."""
    return _json_digest(
        {
            "statement_reference": str(row["statement_reference"]),
            "card_code": str(row["card_code"]),
            "period_start": str(row["period_start"]),
            "period_end": str(row["period_end"]),
            "matched_count": int(row["matched_count"]),
            "statement_only_count": int(row["statement_only_count"]),
            "notification_only_count": int(row["notification_only_count"]),
        }
    )


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
            raise ValueError(
                "legacy statement rows have an invalid transaction identity"
            )
        transaction_ids.append(source_event_id.removeprefix(prefix))
        statement_events.append(
            _normalize_event(
                {
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
                    "decision_trace": json.loads(
                        str(row["decision_trace_json"] or "[]")
                    ),
                    "ai_trace": json.loads(str(row["ai_trace_json"] or "[]")),
                }
            )
        )
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
        content.append(
            {
                "statement_transaction_id": transaction_id,
                "event": {
                    key: value
                    for key, value in event.items()
                    if key not in {"source_event_id", "identity_key"}
                },
            }
        )
    content.sort(key=lambda item: str(item["statement_transaction_id"]))
    return _json_digest(
        {
            "statement_reference": statement_reference,
            "card_code": card_code,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "transactions": content,
        }
    )


def _legacy_statement_content_digest_from_payload(
    payload: dict[str, Any],
    statement_events: list[dict[str, Any]],
    statement_transaction_ids: list[str],
    *,
    statement_reference: str,
    card_code: str,
    period_start: date,
    period_end: date,
) -> str:
    """Hash legacy statement content while retaining source timestamp spelling."""
    rows = payload.get("transactions")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("transactions must be a list of statement transaction objects")
    raw_timestamps = {
        str(row.get("statement_transaction_id") or "").strip(): row.get("occurred_at")
        for row in rows
    }
    legacy_events = []
    for transaction_id, event in zip(
        statement_transaction_ids, statement_events, strict=True
    ):
        raw_timestamp = raw_timestamps.get(transaction_id)
        if raw_timestamp in (None, ""):
            raise ValueError(
                f"statement transaction {transaction_id} is missing occurred_at"
            )
        try:
            legacy_timestamp = datetime.fromisoformat(
                str(raw_timestamp).strip().replace("Z", "+00:00")
            ).isoformat()
        except ValueError as error:
            raise ValueError(
                f"statement transaction {transaction_id} has invalid occurred_at"
            ) from error
        legacy_events.append({**event, "occurred_at": legacy_timestamp})
    return _statement_content_digest(
        legacy_events,
        statement_transaction_ids,
        statement_reference=statement_reference,
        card_code=card_code,
        period_start=period_start,
        period_end=period_end,
    )


def _statement_period_is_valid(
    payload: dict[str, Any],
    *,
    period_start: date,
    period_end: date,
    legacy_local_date: bool,
) -> None:
    rows = payload.get("transactions")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("transactions must be a list of statement transaction objects")
    for row in rows:
        transaction_id = str(row.get("statement_transaction_id") or "").strip()
        raw_timestamp = row.get("occurred_at")
        try:
            parsed = datetime.fromisoformat(
                str(raw_timestamp).strip().replace("Z", "+00:00")
            )
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"statement transaction {transaction_id} has invalid occurred_at"
            ) from error
        occurred = parsed.date() if legacy_local_date else parsed.astimezone(UTC).date()
        if occurred < period_start or occurred > period_end:
            raise ValueError(
                f"statement transaction {transaction_id} falls outside the statement period"
            )


def _canonical_statement_events(
    payload: dict[str, Any],
    *,
    statement_reference: str,
    card_code: str,
    period_start: date,
    period_end: date,
    validate_period: bool = True,
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
            raise ValueError(
                "statement_transaction_id is required for every statement transaction"
            )
        if transaction_id in transaction_ids:
            raise ValueError(f"Duplicate statement_transaction_id: {transaction_id}")
        transaction_ids.add(transaction_id)
        event = _normalize_event(
            {
                **row,
                "source_event_id": f"statement:{statement_reference}:{transaction_id}",
                "card_code": card_code,
                "source": "statement",
                "status": "ACTIVE",
                "confidence": 1,
                "reconciliation_status": "RECONCILED",
                "statement_reference": statement_reference,
            },
            allow_statement_only=True,
        )
        occurred = date.fromisoformat(event["occurred_at"][:10])
        if validate_period and (occurred < period_start or occurred > period_end):
            raise ValueError(
                f"statement transaction {transaction_id} falls outside the statement period"
            )
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
        if str(candidate["currency"] or "").strip().upper() != event["currency"]:
            continue
        if _event_polarity(candidate["event_type"]) != _event_polarity(
            event["event_type"]
        ):
            continue
        candidate_date = date.fromisoformat(str(candidate["occurred_at"])[:10])
        day_gap = abs((event_date - candidate_date).days)
        if day_gap > 3:
            continue
        merchant_score = _merchant_match_score(candidate["merchant"], event["merchant"])
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
        raise ValueError("occurred_at must include a UTC offset")
    return parsed.astimezone(UTC).isoformat()


def _utc_datetime(value: object) -> datetime:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _period_iso_datetime(value: object, field_name: str) -> str:
    """Return one canonical UTC spelling for a cashback period bound."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{field_name} is required")
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(raw.replace("Z", "+00:00"))
        )
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return (
        parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def _legacy_date_period_bounds(
    period_start: object, period_end: object
) -> tuple[str, str] | None:
    """Convert one legacy inclusive date period to fixed UTC half-open bounds."""
    start_raw = str(period_start or "").strip()
    end_raw = str(period_end or "").strip()
    if not (
        _LEGACY_DATE_PERIOD.fullmatch(start_raw)
        and _LEGACY_DATE_PERIOD.fullmatch(end_raw)
    ):
        return None
    try:
        start = date.fromisoformat(start_raw)
        end = date.fromisoformat(end_raw)
    except ValueError as exc:
        raise ValueError("legacy cashback period bounds must be ISO dates") from exc
    if end < start:
        raise ValueError("legacy cashback period_end cannot be before period_start")
    return (
        _period_iso_datetime(
            datetime.combine(start, datetime.min.time(), tzinfo=UTC), "period_start"
        ),
        _period_iso_datetime(
            datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=UTC),
            "period_end",
        ),
    )


def _same_instant(left: object, right: object) -> bool:
    """Compare persisted timestamps without making timezone spelling significant."""

    return _utc_datetime(left) == _utc_datetime(right)


def _amount_minor(value: object) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("amount must be numeric") from error
    if amount <= 0:
        raise ValueError(
            "amount must be greater than zero; use event_type REFUND for credits"
        )
    return int((amount * Decimal("100")).quantize(Decimal("1")))


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
    if confidence < 0 or confidence > 1:
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


def _economic_identity(
    occurred_at: object,
    card_code: object,
    amount_aed_minor: object,
    currency: object,
    event_type: object,
    merchant: object,
) -> str:
    canonical = "|".join(
        (
            str(occurred_at),
            str(card_code),
            str(amount_aed_minor),
            str(currency),
            str(event_type),
            _merchant_key(merchant),
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _legacy_collision_identity(identity_key: str, source_event_id: str) -> str:
    return hashlib.sha256(
        f"{identity_key}|legacy:{source_event_id}".encode("utf-8")
    ).hexdigest()


def _normalize_event(
    source: dict[str, Any],
    *,
    statement_only_cards: frozenset[str] = frozenset(),
    allow_statement_only: bool = False,
) -> dict[str, Any]:
    source_event_id = str(source.get("source_event_id") or "").strip()
    card_code = str(source.get("card_code") or "").strip().upper()
    if not source_event_id:
        raise ValueError("source_event_id is required")
    if not card_code:
        raise ValueError("card_code is required")
    if card_code in statement_only_cards and not allow_statement_only:
        raise ValueError(
            f"{card_code} uses STATEMENT_ONLY tracking; "
            "use statement evidence instead of a live event"
        )
    status = str(source.get("status") or "ACTIVE").upper()
    # Accept legacy envelopes while storing one user-facing transaction state.
    if status in {"PROVISIONAL", "CONFIRMED"}:
        status = "ACTIVE"
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {sorted(VALID_STATUSES)}")
    event_type = str(source.get("event_type") or "PURCHASE").upper()
    if event_type not in VALID_EVENT_TYPES:
        raise ValueError(f"event_type must be one of {sorted(VALID_EVENT_TYPES)}")
    reversal_of = str(source.get("reversal_of") or "").strip() or None
    if event_type == "REVERSAL" and not reversal_of:
        raise ValueError("reversal_of is required for REVERSAL events")
    reconciliation_status = str(
        source.get("reconciliation_status") or "UNMATCHED"
    ).upper()
    if reconciliation_status not in VALID_RECONCILIATION_STATUSES:
        raise ValueError(
            f"reconciliation_status must be one of {sorted(VALID_RECONCILIATION_STATUSES)}"
        )
    confidence = _confidence(source.get("confidence"))
    explicit_review = source.get("review_required")
    raw_purchase_type = source.get("purchase_type")
    raw_channel = source.get("channel")
    purchase_type = str(raw_purchase_type or "GENERAL").strip().upper()
    channel = str(raw_channel or "UNKNOWN").strip().upper()
    category_uncertain = raw_purchase_type in (None, "") or purchase_type == "UNKNOWN"
    channel_uncertain = raw_channel in (None, "") or channel == "UNKNOWN"
    review_required = _boolean(
        explicit_review,
        default=confidence < 0.8 or category_uncertain or channel_uncertain,
    )
    tags = source.get("tags") or []
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("tags must be a list of strings")
    normalized = {
        "source_event_id": source_event_id,
        "occurred_at": _iso_datetime(source.get("occurred_at")),
        "card_code": card_code,
        "amount_aed_minor": _amount_minor(_event_amount(source)),
        "currency": str(source.get("currency") or "AED").strip().upper(),
        "purchase_type": purchase_type,
        "channel": channel,
        "merchant": str(source.get("merchant") or "Unknown").strip(),
        "bucket_code": str(source.get("bucket_code") or "").strip().upper() or None,
        "event_type": event_type,
        "source": str(source.get("source") or "email").strip(),
        "status": status,
        "tags_json": json.dumps(sorted(set(tags))),
        "confidence": confidence,
        "review_required": int(review_required),
        "reconciliation_status": reconciliation_status,
        "statement_reference": str(source.get("statement_reference") or "").strip()
        or None,
        "email_reference": str(source.get("email_reference") or "").strip() or None,
        "document_url": str(source.get("document_url") or "").strip() or None,
        "reversal_of": reversal_of,
        "decision_trace_json": _trace_json(
            source.get("decision_trace"), "decision_trace"
        ),
        "ai_trace_json": _trace_json(source.get("ai_trace"), "ai_trace"),
    }
    normalized["identity_key"] = _economic_identity(
        normalized["occurred_at"],
        normalized["card_code"],
        normalized["amount_aed_minor"],
        normalized["currency"],
        normalized["event_type"],
        normalized["merchant"],
    )
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

    def __init__(
        self,
        path: Path,
        *,
        program_config_path: Path | None = None,
    ):
        self.path = path
        self.program_config_path = program_config_path
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
                    CREATE TABLE IF NOT EXISTS statement_receipts (
                        receipt_id TEXT NOT NULL,
                        source_identity TEXT NOT NULL,
                        card_code TEXT NOT NULL,
                        original_received_at TEXT NOT NULL,
                        period_id TEXT,
                        receipt_sha256 TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(card_code, receipt_id),
                        UNIQUE(card_code, source_identity)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS bank_statement_receipts (
                        receipt_id TEXT PRIMARY KEY,
                        receipt_sha256 TEXT NOT NULL UNIQUE,
                        source TEXT NOT NULL,
                        source_message_id TEXT NOT NULL,
                        received_at TEXT NOT NULL,
                        card_code TEXT NOT NULL,
                        statement_reference TEXT,
                        period_start TEXT,
                        period_end TEXT,
                        source_attachment_id TEXT,
                        statement_sha256 TEXT,
                        evidence_reference TEXT,
                        document_url TEXT,
                        subject TEXT,
                        state TEXT NOT NULL DEFAULT 'BANK_CLOSED',
                        processing_state TEXT NOT NULL DEFAULT 'PENDING',
                        reconciliation_state TEXT NOT NULL DEFAULT 'PENDING',
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(source, source_message_id),
                        CHECK (state = 'BANK_CLOSED')
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_bank_receipts_card_received "
                    "ON bank_statement_receipts(card_code, received_at DESC, receipt_id)"
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
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_card_periods_period_id "
                    "ON card_periods(period_id) WHERE period_id IS NOT NULL"
                )
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_statement_receipts_source "
                    "ON statement_receipts(card_code, source_identity)"
                )
                connection.execute(
                    "UPDATE cashback_events SET status='ACTIVE' WHERE status IN ('PROVISIONAL', 'CONFIRMED')"
                )
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_cashback_events_identity ON cashback_events(identity_key) WHERE identity_key IS NOT NULL"
                )

    @staticmethod
    def _migrate_event_columns(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")
        existing = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(cashback_events)"
            ).fetchall()
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
        expected_index = "idx_cashback_events_identity"
        for index_row in connection.execute(
            "PRAGMA index_list(cashback_events)"
        ).fetchall():
            index_name = str(index_row["name"])
            escaped_name = index_name.replace('"', '""')
            index_columns = [
                str(info["name"])
                for info in connection.execute(
                    f'PRAGMA index_info("{escaped_name}")'
                ).fetchall()
            ]
            if index_columns != ["identity_key"]:
                continue
            if int(index_row["unique"]) != 1:
                continue
            if index_name != expected_index:
                raise ValueError(
                    "incompatible unique cashback identity index: " + index_name
                )
        expected = next(
            (
                row
                for row in connection.execute(
                    "PRAGMA index_list(cashback_events)"
                ).fetchall()
                if str(row["name"]) == expected_index
            ),
            None,
        )
        if expected is not None:
            expected_columns = [
                str(info["name"])
                for info in connection.execute(
                    f'PRAGMA index_info("{expected_index}")'
                ).fetchall()
            ]
            if int(expected["unique"]) != 1 or expected_columns != ["identity_key"]:
                raise ValueError("incompatible cashback identity index definition")
            connection.execute(f'DROP INDEX "{expected_index}"')

        rows = connection.execute(
            "SELECT * FROM cashback_events ORDER BY created_at, source_event_id"
        ).fetchall()
        plans: list[tuple[str, str, str]] = []
        base_keys: set[str] = set()
        for row in rows:
            source_event_id = str(row["source_event_id"])
            try:
                occurred_at = _iso_datetime(row["occurred_at"])
            except ValueError as error:
                raise ValueError(
                    f"legacy cashback event {source_event_id} has invalid occurred_at; "
                    "migration aborted"
                ) from error
            identity = _economic_identity(
                occurred_at,
                row["card_code"],
                row["amount_aed_minor"],
                row["currency"],
                row["event_type"],
                row["merchant"],
            )
            plans.append((source_event_id, occurred_at, identity))
            base_keys.add(identity)

        base_owners: set[str] = set()
        target_owners: dict[str, str] = {}
        for source_event_id, occurred_at, identity in plans:
            target = identity
            if identity in base_owners:
                target = _legacy_collision_identity(identity, source_event_id)
                if target in base_keys:
                    raise ValueError(
                        "incompatible cashback identity collision during migration"
                    )
            else:
                base_owners.add(identity)
            if target in target_owners:
                raise ValueError(
                    "incompatible cashback identity collision during migration"
                )
            target_owners[target] = source_event_id
            connection.execute(
                """
                UPDATE cashback_events
                SET occurred_at = ?, identity_key = ?
                WHERE source_event_id = ?
                """,
                (occurred_at, target, source_event_id),
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
            "period_id": "TEXT",
            "closed_by_receipt_id": "TEXT",
            "original_received_at": "TEXT",
        }
        for column, definition in additions.items():
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE card_periods ADD COLUMN {column} {definition}"
                )
        legacy_rows = connection.execute(
            """
            SELECT card_code, period_start, period_end
            FROM card_periods
            """
        ).fetchall()
        for row in legacy_rows:
            bounds = _legacy_date_period_bounds(row["period_start"], row["period_end"])
            if bounds is None:
                continue
            connection.execute(
                """
                UPDATE card_periods
                SET period_start = ?, period_end = ?
                WHERE card_code = ? AND period_start = ? AND period_end = ?
                """,
                (
                    bounds[0],
                    bounds[1],
                    row["card_code"],
                    row["period_start"],
                    row["period_end"],
                ),
            )
        connection.execute(
            """
            UPDATE card_periods
            SET period_id = 'cashback-period:' || card_code || ':' || period_start || ':' || period_end
            WHERE period_id IS NULL
            """
        )

    @staticmethod
    def _migrate_reconciliation_columns(connection: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(reconciliation_runs)"
            ).fetchall()
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

    def _statement_only_cards(self) -> frozenset[str]:
        configuration = load_program_configuration(self.program_config_path)
        return frozenset(
            program.card.upper()
            for program in programs_from_config(configuration)
            if program.tracking_mode == "STATEMENT_ONLY"
        )

    def upsert(self, events: Iterable[dict[str, Any]]) -> dict[str, int]:
        statement_only_cards = self._statement_only_cards()
        normalized = [
            _normalize_event(event, statement_only_cards=statement_only_cards)
            for event in events
        ]
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
                    if (
                        identity_owner
                        and identity_owner["source_event_id"]
                        != event["source_event_id"]
                    ):
                        duplicates += 1
                        continue
                    columns = tuple(event)
                    placeholders = ", ".join("?" for _ in columns)
                    connection.execute(
                        f"""
                        INSERT INTO cashback_events ({", ".join(columns)})
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
        statement_only_cards = self._statement_only_cards()
        normalized = [
            _normalize_event(event, statement_only_cards=statement_only_cards)
            for event in events
        ]
        if not normalized:
            raise ValueError("At least one event is required")
        return normalized

    @staticmethod
    def _ingest_fields(source: dict[str, Any]) -> tuple[str, str, int, int, str]:
        source_name = str(source.get("source") or "mailbox").strip()
        if not source_name:
            raise ValueError("source is required")
        completed_at = _iso_datetime(source.get("completed_at"))
        try:
            scanned_count = int(source.get("scanned_count"))
            accepted_count = int(source.get("accepted_count"))
        except (TypeError, ValueError) as error:
            raise ValueError(
                "scanned_count and accepted_count must be integers"
            ) from error
        if scanned_count < 0 or accepted_count < 0 or accepted_count > scanned_count:
            raise ValueError(
                "ingest counts must be non-negative and accepted cannot exceed scanned"
            )
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
        source_name, completed_at, scanned_count, accepted_count, cursor = (
            self._ingest_fields(source)
        )
        ids = sorted(
            {str(event_id).strip() for event_id in event_ids if str(event_id).strip()}
        )
        digests = sorted(
            {str(digest).strip() for digest in event_digests if str(digest).strip()}
        )
        if len(ids) != len(digests):
            raise ValueError("event ids and event digests must have equal cardinality")
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
        source_name, completed_at, scanned_count, accepted_count, cursor = (
            self._ingest_fields(source)
        )
        service_receipt = source.get("service_receipt")
        if isinstance(service_receipt, dict):
            receipt_id = str(service_receipt.get("receipt_id") or "").strip()
            receipt_sha256 = str(service_receipt.get("receipt_sha256") or "").strip()
        else:
            receipt_id = str(
                source.get("service_receipt_id") or source.get("receipt_id") or ""
            ).strip()
            receipt_sha256 = str(
                source.get("service_receipt_sha256")
                or source.get("receipt_sha256")
                or ""
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
                    raise IngestCursorConflict(
                        "service receipt is unknown or mismatched"
                    )
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
                    raise IngestCursorConflict(
                        "service receipt is not bound to the commit payload"
                    )

                current = connection.execute(
                    "SELECT * FROM ingest_state WHERE source = ?",
                    (source_name,),
                ).fetchone()
                if current is not None:
                    current_receipt_id = str(current["receipt_id"] or "")
                    if (
                        current_receipt_id == receipt_id
                        and str(current["receipt_sha256"] or "") == receipt_sha256
                    ):
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
                        raise IngestCursorConflict(
                            "completed_at is stale or regressive"
                        )
                    current_cursor = str(current["cursor"] or "")
                    if _cursor_order(current_completed, completed_at) == 0:
                        raise IngestCursorConflict(
                            "completed_at is already committed with another receipt"
                        )
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

    def set_alert_acknowledgement(
        self, alert_key: object, acknowledged: object
    ) -> dict[str, Any]:
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

    def _bank_statement_receipt_view(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        result = dict(row)
        if result.get("period_start") and result.get("period_end"):
            finalized = connection.execute(
                """
                SELECT period_start, period_end FROM card_periods
                WHERE card_code = ?
                  AND status = 'FINALIZED'
                """,
                (result["card_code"],),
            ).fetchall()
            expected_start, expected_end = receipt_period_bounds(
                result["period_start"],
                result["period_end"],
            )
            result["settlement_state"] = (
                "FINALIZED"
                if any(
                    _utc_datetime(period["period_start"]) == expected_start
                    and _utc_datetime(period["period_end"]) == expected_end
                    for period in finalized
                )
                else "UNFINALIZED"
            )
        return receipt_view(result)

    @staticmethod
    def _bank_statement_result(
        view: dict[str, Any],
        *,
        idempotent_replay: bool,
    ) -> dict[str, Any]:
        return {
            "statement_receipt": view,
            "receipt": view,
            "receipt_id": view["receipt_id"],
            "state": view["state"],
            "bank_state": view["bank_state"],
            "processing_state": view["processing_state"],
            "reconciliation_state": view["reconciliation_state"],
            "idempotent_replay": idempotent_replay,
        }

    def record_bank_statement_receipt(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist authenticated email arrival before parsing or reconciliation.

        This operation owns only the bank receipt state. It deliberately does
        not update card periods, statement rows, or settlement state.
        """
        normalized = normalize_statement_receipt(payload)
        columns = (
            "receipt_id",
            "receipt_sha256",
            "source",
            "source_message_id",
            "received_at",
            "card_code",
            "statement_reference",
            "period_start",
            "period_end",
            "source_attachment_id",
            "statement_sha256",
            "evidence_reference",
            "document_url",
            "subject",
            "state",
            "processing_state",
            "reconciliation_state",
            "payload_json",
        )
        values = tuple(
            json.dumps(normalized, sort_keys=True, separators=(",", ":"))
            if column == "payload_json"
            else normalized[column]
            for column in columns
        )
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    """
                    SELECT * FROM bank_statement_receipts
                    WHERE receipt_id = ?
                       OR (source = ? AND source_message_id = ?)
                    """,
                    (
                        normalized["receipt_id"],
                        normalized["source"],
                        normalized["source_message_id"],
                    ),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["receipt_id"] != normalized["receipt_id"]
                        or existing["receipt_sha256"] != normalized["receipt_sha256"]
                    ):
                        raise IngestCursorConflict(
                            "statement receipt identity collision"
                        )
                    view = self._bank_statement_receipt_view(connection, existing)
                    connection.commit()
                    return self._bank_statement_result(
                        view,
                        idempotent_replay=True,
                    )
                connection.execute(
                    f"""
                    INSERT INTO bank_statement_receipts ({", ".join(columns)})
                    VALUES ({", ".join("?" for _ in columns)})
                    """,
                    values,
                )
                stored = connection.execute(
                    """
                    SELECT * FROM bank_statement_receipts
                    WHERE receipt_id = ?
                    """,
                    (normalized["receipt_id"],),
                ).fetchone()
                if stored is None:
                    raise IngestCursorConflict(
                        "statement receipt was not readable after insert"
                    )
                view = self._bank_statement_receipt_view(connection, stored)
                connection.commit()
                return self._bank_statement_result(
                    view,
                    idempotent_replay=False,
                )
            except Exception:
                connection.rollback()
                raise

    def statement_receipt(
        self,
        receipt_id: str = "",
        *,
        source: str | None = None,
        source_message_id: str | None = None,
    ) -> dict[str, Any]:
        key = str(receipt_id or "").strip()
        source_name = str(source or "").strip()
        message_id = str(source_message_id or "").strip()
        if not key and not (source_name and message_id):
            raise ValueError("receipt_id or source and source_message_id are required")
        with closing(self._connect()) as connection:
            if key:
                row = connection.execute(
                    """
                    SELECT * FROM bank_statement_receipts
                    WHERE receipt_id = ?
                    """,
                    (key,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT * FROM bank_statement_receipts
                    WHERE source = ? AND source_message_id = ?
                    """,
                    (source_name, message_id),
                ).fetchone()
            if row is None:
                raise IngestCursorConflict("statement receipt is unknown")
            return self._bank_statement_receipt_view(connection, row)

    def statement_receipts(
        self,
        *,
        card_code: str | None = None,
        included_cards: Iterable[str] | None = None,
        excluded_cards: Iterable[str] = (),
        limit: int = 24,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("statement receipt limit must be between 1 and 200")
        included = (
            None
            if included_cards is None
            else sorted(
                {
                    str(card).strip().upper()
                    for card in included_cards
                    if str(card).strip()
                }
            )
        )
        excluded = sorted(
            {str(card).strip().upper() for card in excluded_cards if str(card).strip()}
        )
        clauses: list[str] = []
        parameters: list[object] = []
        if included is not None:
            if not included:
                return []
            placeholders = ", ".join("?" for _ in included)
            clauses.append(f"card_code IN ({placeholders})")
            parameters.extend(included)
        if card_code:
            clauses.append("card_code = ?")
            parameters.append(str(card_code).strip().upper())
        if excluded:
            placeholders = ", ".join("?" for _ in excluded)
            clauses.append(f"card_code NOT IN ({placeholders})")
            parameters.extend(excluded)
        query = "SELECT * FROM bank_statement_receipts"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY received_at DESC, receipt_id DESC LIMIT ?"
        parameters.append(limit)
        with closing(self._connect()) as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
            return [self._bank_statement_receipt_view(connection, row) for row in rows]

    def update_statement_receipt(
        self,
        receipt_id: str,
        *,
        processing_state: str | None = None,
        reconciliation_state: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        statement_reference: str | None = None,
    ) -> dict[str, Any]:
        key = str(receipt_id or "").strip()
        if not key:
            raise ValueError("receipt_id is required")
        requested_processing = (
            None if processing_state is None else str(processing_state).strip().upper()
        )
        requested_reconciliation = (
            None
            if reconciliation_state is None
            else str(reconciliation_state).strip().upper()
        )
        if (
            requested_processing is not None
            and requested_processing not in PROCESSING_STATES
        ):
            raise ValueError(f"Unsupported processing_state: {requested_processing}")
        if (
            requested_reconciliation is not None
            and requested_reconciliation not in RECONCILIATION_STATES
        ):
            raise ValueError(
                f"Unsupported reconciliation_state: {requested_reconciliation}"
            )
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = connection.execute(
                    """
                    SELECT * FROM bank_statement_receipts
                    WHERE receipt_id = ?
                    """,
                    (key,),
                ).fetchone()
                if current is None:
                    raise IngestCursorConflict("statement receipt is unknown")
                current_start = current["period_start"]
                current_end = current["period_end"]
                if (period_start is None) != (period_end is None):
                    raise ValueError(
                        "period_start and period_end must be supplied together"
                    )
                if period_start is not None and period_end is not None:
                    candidate = normalize_statement_receipt(
                        {
                            "source": current["source"],
                            "source_message_id": current["source_message_id"],
                            "received_at": current["received_at"],
                            "card_code": current["card_code"],
                            "period_start": period_start,
                            "period_end": period_end,
                        }
                    )
                    if current_start and (
                        current_start != candidate["period_start"]
                        or current_end != candidate["period_end"]
                    ):
                        raise IngestCursorConflict(
                            "statement period bounds are immutable once known"
                        )
                    period_start = candidate["period_start"]
                    period_end = candidate["period_end"]
                elif current_start or current_end:
                    period_start = current_start
                    period_end = current_end
                assignments: list[str] = []
                values: list[object] = []
                if requested_processing is not None:
                    assignments.append("processing_state = ?")
                    values.append(requested_processing)
                if requested_reconciliation is not None:
                    assignments.append("reconciliation_state = ?")
                    values.append(requested_reconciliation)
                if period_start is not None:
                    assignments.extend(("period_start = ?", "period_end = ?"))
                    values.extend((period_start, period_end))
                if statement_reference is not None:
                    assignments.append("statement_reference = ?")
                    values.append(str(statement_reference).strip() or None)
                if assignments:
                    assignments.append("updated_at = CURRENT_TIMESTAMP")
                    connection.execute(
                        f"""
                        UPDATE bank_statement_receipts
                        SET {", ".join(assignments)}
                        WHERE receipt_id = ?
                        """,
                        (*values, key),
                    )
                stored = connection.execute(
                    """
                    SELECT * FROM bank_statement_receipts
                    WHERE receipt_id = ?
                    """,
                    (key,),
                ).fetchone()
                if stored is None:
                    raise IngestCursorConflict(
                        "statement receipt disappeared during update"
                    )
                view = self._bank_statement_receipt_view(connection, stored)
                connection.commit()
                return view
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _statement_receipt_envelope(
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(payload, dict):
            raise ValueError("statement receipt payload must be an object")
        nested = payload.get("statement_receipt", payload.get("receipt"))
        if nested is not None and not isinstance(nested, dict):
            raise ValueError("statement_receipt must be an object")
        receipt_source = dict(nested or {})
        for field in (
            "receipt_id",
            "source_identity",
            "card_code",
            "original_received_at",
            "period_id",
        ):
            if field not in receipt_source and field in payload:
                receipt_source[field] = payload[field]
        if not receipt_source.get("receipt_id"):
            receipt_source["receipt_id"] = payload.get(
                "statement_reference"
            ) or payload.get("email_reference")
        if not receipt_source.get("source_identity"):
            receipt_source["source_identity"] = (
                payload.get("statement_reference")
                or payload.get("email_reference")
                or receipt_source.get("receipt_id")
            )
        if "original_received_at" not in receipt_source:
            for alias in ("received_at", "received_timestamp", "email_received_at"):
                if payload.get(alias) not in (None, ""):
                    receipt_source["original_received_at"] = payload[alias]
                    break
        if any(
            key in payload or key in receipt_source
            for key in ("transactions", "statement_transactions")
        ):
            raise ValueError("statement receipts cannot contain statement purchases")

        period_source = payload.get("period")
        if period_source is None and isinstance(nested, dict):
            period_source = nested.get("period")
        if period_source is not None and not isinstance(period_source, dict):
            raise ValueError("period must be an object")
        period = dict(period_source or {})
        for field in ("period_id", "card_code", "period_start", "period_end"):
            if field not in period and field in payload:
                period[field] = payload[field]
            if field not in period and field in receipt_source:
                period[field] = receipt_source[field]
        for field in ("period_start", "period_end"):
            receipt_source.pop(field, None)
        receipt_source.pop("period", None)
        if (
            period.get("period_id") not in (None, "")
            and "period_id" not in receipt_source
        ):
            receipt_source["period_id"] = period["period_id"]
        receipt = StatementReceipt(**receipt_source).to_dict()
        if period.get("card_code") in (None, ""):
            period["card_code"] = receipt["card_code"]
        if period.get("period_id") in (None, "") and receipt.get("period_id"):
            period["period_id"] = receipt["period_id"]
        return receipt, period

    @staticmethod
    def _normalized_period(
        period: dict[str, Any], receipt: dict[str, Any]
    ) -> dict[str, str | None]:
        card_code = str(period.get("card_code") or receipt["card_code"]).strip().upper()
        if not card_code:
            raise ValueError("card_code is required")
        if card_code != receipt["card_code"]:
            raise ValueError("period card_code must match statement receipt card_code")
        period_id = str(period.get("period_id") or "").strip() or None
        period_start = (
            _period_iso_datetime(period["period_start"], "period_start")
            if period.get("period_start") not in (None, "")
            else None
        )
        period_end = (
            _period_iso_datetime(period["period_end"], "period_end")
            if period.get("period_end") not in (None, "")
            else None
        )
        if (period_start is None) != (period_end is None):
            raise ValueError("period_start and period_end must be supplied together")
        if period_start is not None and period_end is not None:
            if datetime.fromisoformat(period_end) <= datetime.fromisoformat(
                period_start
            ):
                raise ValueError("period_end must be after period_start")
            if period_id is None:
                period_id = f"cashback-period:{card_code}:{period_start}:{period_end}"
        return {
            "period_id": period_id,
            "card_code": card_code,
            "period_start": period_start,
            "period_end": period_end,
        }

    @staticmethod
    def _statement_receipt_result(
        receipt: dict[str, Any],
        period: sqlite3.Row | None,
        *,
        idempotent_replay: bool,
    ) -> dict[str, Any]:
        period_id = receipt.get("period_id")
        result: dict[str, Any] = {
            "receipt_id": receipt["receipt_id"],
            "source_identity": receipt["source_identity"],
            "card_code": receipt["card_code"],
            "original_received_at": receipt["original_received_at"],
            "period_id": period_id,
            "status": "WAITING_STATEMENT",
            "idempotent_replay": idempotent_replay,
        }
        if period is not None:
            result.update(
                {
                    "period_id": period["period_id"],
                    "period_start": period["period_start"],
                    "period_end": period["period_end"],
                }
            )
            if str(period["closed_by_receipt_id"] or "") == receipt["receipt_id"]:
                result["status"] = "CLOSED"
                result["close_id"] = _close_identifier(
                    str(period["card_code"]),
                    str(period["period_start"]),
                    str(period["period_end"]),
                )
        return result

    def ensure_period(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create one explicit open period without deriving bounds from a clock."""
        period_source = payload.get("period") if isinstance(payload, dict) else None
        period = dict(period_source or payload)
        receipt = {
            "card_code": str(period.get("card_code") or "").strip().upper(),
        }
        normalized = self._normalized_period(period, receipt)
        if normalized["period_start"] is None:
            raise ValueError("period_start and period_end are required")
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                if normalized["period_id"] is not None:
                    existing_by_id = connection.execute(
                        """
                        SELECT * FROM card_periods
                        WHERE period_id = ?
                        """,
                        (normalized["period_id"],),
                    ).fetchone()
                    if existing_by_id is not None:
                        if (
                            str(existing_by_id["card_code"]).upper()
                            != normalized["card_code"]
                            or not _same_instant(
                                existing_by_id["period_start"],
                                normalized["period_start"],
                            )
                            or not _same_instant(
                                existing_by_id["period_end"],
                                normalized["period_end"],
                            )
                        ):
                            raise ValueError(
                                "period_id does not match supplied period bounds"
                            )
                        return dict(existing_by_id)

                existing = connection.execute(
                    """
                    SELECT * FROM card_periods
                    WHERE card_code = ? AND period_start = ? AND period_end = ?
                    """,
                    (
                        normalized["card_code"],
                        normalized["period_start"],
                        normalized["period_end"],
                    ),
                ).fetchone()
                if existing is not None:
                    if (
                        normalized["period_id"]
                        and existing["period_id"] != normalized["period_id"]
                    ):
                        raise ValueError(
                            "period bounds are already used by another period"
                        )
                    return dict(existing)
                overlap = connection.execute(
                    """
                    SELECT 1 FROM card_periods
                    WHERE card_code = ?
                      AND period_start < ?
                      AND period_end > ?
                    LIMIT 1
                    """,
                    (
                        normalized["card_code"],
                        normalized["period_end"],
                        normalized["period_start"],
                    ),
                ).fetchone()
                if overlap is not None:
                    raise ValueError(
                        f"cashback periods overlap for card {normalized['card_code']}"
                    )
                connection.execute(
                    """
                    INSERT INTO card_periods (
                        card_code, period_start, period_end, period_id, status
                    ) VALUES (?, ?, ?, ?, 'OPEN')
                    """,
                    (
                        normalized["card_code"],
                        normalized["period_start"],
                        normalized["period_end"],
                        normalized["period_id"],
                    ),
                )
                row = connection.execute(
                    """
                    SELECT * FROM card_periods
                    WHERE card_code = ? AND period_start = ? AND period_end = ?
                    """,
                    (
                        normalized["card_code"],
                        normalized["period_start"],
                        normalized["period_end"],
                    ),
                ).fetchone()
        return dict(row)

    def record_statement_receipt(
        self,
        payload: dict[str, Any],
        *,
        period: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically persist a statement-email receipt and close one card period.

        The receipt's original source timestamp is the only transition time.
        Processing time, current date, statement rows, and Actual state are not
        consulted.  A source identity is deduplicated within its card only.
        """
        candidate = payload.get(
            "statement_receipt",
            payload.get("receipt", payload),
        )
        if (
            period is None
            and isinstance(candidate, dict)
            and any(
                field in candidate
                for field in (
                    "source",
                    "source_message_id",
                    "received_at",
                    "receivedDateTime",
                )
            )
        ):
            return self.record_bank_statement_receipt(candidate)

        envelope = dict(payload)
        if period is not None:
            envelope["period"] = period
        receipt, period_source = self._statement_receipt_envelope(envelope)
        period_id_supplied = str(period_source.get("period_id") or "").strip() or None
        normalized_period = self._normalized_period(period_source, receipt)
        if normalized_period["period_id"] is not None:
            receipt["period_id"] = normalized_period["period_id"]
        receipt_sha256 = _json_digest({"receipt": receipt, "period": normalized_period})
        card_code = receipt["card_code"]
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """
                    SELECT * FROM statement_receipts
                    WHERE card_code = ? AND source_identity = ?
                    """,
                    (card_code, receipt["source_identity"]),
                ).fetchone()
                if existing is None:
                    existing = connection.execute(
                        """
                        SELECT * FROM statement_receipts
                        WHERE card_code = ? AND receipt_id = ?
                        """,
                        (card_code, receipt["receipt_id"]),
                    ).fetchone()
                if existing is not None:
                    if (
                        str(existing["receipt_id"]) != receipt["receipt_id"]
                        or str(existing["source_identity"])
                        != receipt["source_identity"]
                        or str(existing["card_code"]).upper() != card_code
                        or not _same_instant(
                            existing["original_received_at"],
                            receipt["original_received_at"],
                        )
                    ):
                        raise ValueError(
                            "statement receipt identity was already used for different content"
                        )

                    stored_period_id = str(existing["period_id"] or "").strip() or None
                    period_row = None
                    if stored_period_id is not None:
                        period_row = connection.execute(
                            """
                            SELECT * FROM card_periods
                            WHERE card_code = ? AND period_id = ?
                            """,
                            (card_code, stored_period_id),
                        ).fetchone()
                        if period_row is None:
                            raise ValueError(
                                f"Unknown cashback period: {stored_period_id}"
                            )

                    period_requested = (
                        period_id_supplied is not None
                        or normalized_period["period_start"] is not None
                    )
                    if stored_period_id is not None:
                        if period_requested:
                            candidate = None
                            if period_id_supplied is not None:
                                candidate = connection.execute(
                                    """
                                    SELECT * FROM card_periods
                                    WHERE period_id = ?
                                    """,
                                    (period_id_supplied,),
                                ).fetchone()
                                if candidate is None:
                                    raise ValueError(
                                        f"Unknown cashback period: {period_id_supplied}"
                                    )
                            elif normalized_period["period_start"] is not None:
                                candidate = connection.execute(
                                    """
                                    SELECT * FROM card_periods
                                    WHERE card_code = ? AND period_start = ?
                                      AND period_end = ?
                                    """,
                                    (
                                        card_code,
                                        normalized_period["period_start"],
                                        normalized_period["period_end"],
                                    ),
                                ).fetchone()
                                if candidate is None:
                                    raise ValueError(
                                        "period_id does not match supplied period bounds"
                                    )
                            if (
                                candidate is None
                                or str(candidate["period_id"]) != stored_period_id
                            ):
                                raise ValueError(
                                    "statement receipt period was already bound differently"
                                )
                            if normalized_period["period_start"] is not None and (
                                not _same_instant(
                                    period_row["period_start"],
                                    normalized_period["period_start"],
                                )
                                or not _same_instant(
                                    period_row["period_end"],
                                    normalized_period["period_end"],
                                )
                            ):
                                raise ValueError(
                                    "period_id does not match supplied period bounds"
                                )
                        receipt["period_id"] = stored_period_id
                        return self._statement_receipt_result(
                            receipt,
                            period_row,
                            idempotent_replay=True,
                        )

                    candidate = None
                    if period_requested:
                        if period_id_supplied is not None:
                            candidate = connection.execute(
                                """
                                SELECT * FROM card_periods
                                WHERE period_id = ?
                                """,
                                (period_id_supplied,),
                            ).fetchone()
                            if candidate is None:
                                raise ValueError(
                                    f"Unknown cashback period: {period_id_supplied}"
                                )
                        elif normalized_period["period_start"] is not None:
                            candidate = connection.execute(
                                """
                                SELECT * FROM card_periods
                                WHERE card_code = ? AND period_start = ?
                                  AND period_end = ?
                                """,
                                (
                                    card_code,
                                    normalized_period["period_start"],
                                    normalized_period["period_end"],
                                ),
                            ).fetchone()
                    else:
                        open_periods = connection.execute(
                            """
                            SELECT * FROM card_periods
                            WHERE card_code = ? AND status = 'OPEN'
                            ORDER BY period_start, period_id
                            """,
                            (card_code,),
                        ).fetchall()
                        candidates = [
                            period
                            for period in open_periods
                            if _same_instant(
                                period["period_end"],
                                receipt["original_received_at"],
                            )
                        ]
                        if len(candidates) > 1:
                            raise ValueError(
                                "multiple open cashback periods match statement receipt timestamp"
                            )
                        candidate = candidates[0] if candidates else None
                    if candidate is None:
                        stored_receipt = {
                            "receipt_id": existing["receipt_id"],
                            "source_identity": existing["source_identity"],
                            "card_code": existing["card_code"],
                            "original_received_at": existing["original_received_at"],
                            "period_id": None,
                        }
                        return self._statement_receipt_result(
                            stored_receipt,
                            None,
                            idempotent_replay=True,
                        )
                    if str(candidate["card_code"]).upper() != card_code:
                        raise ValueError(
                            "period_id does not match statement receipt card_code"
                        )
                    if str(candidate["status"]).upper() != "OPEN":
                        raise ValueError(
                            "cashback period is already closed by another receipt"
                        )
                    if normalized_period["period_start"] is not None and (
                        not _same_instant(
                            candidate["period_start"],
                            normalized_period["period_start"],
                        )
                        or not _same_instant(
                            candidate["period_end"],
                            normalized_period["period_end"],
                        )
                    ):
                        raise ValueError(
                            "period_id does not match supplied period bounds"
                        )
                    if not _same_instant(
                        candidate["period_end"], receipt["original_received_at"]
                    ):
                        raise ValueError(
                            "statement receipt timestamp must equal period_end"
                        )
                    stored_period_id = str(candidate["period_id"])
                    bound_receipt = {
                        "receipt_id": receipt["receipt_id"],
                        "source_identity": receipt["source_identity"],
                        "card_code": card_code,
                        "original_received_at": receipt["original_received_at"],
                        "period_id": stored_period_id,
                    }
                    bound_period = {
                        "period_id": stored_period_id,
                        "card_code": card_code,
                        "period_start": candidate["period_start"],
                        "period_end": candidate["period_end"],
                    }
                    connection.execute(
                        """
                        UPDATE statement_receipts
                        SET period_id = ?, receipt_sha256 = ?
                        WHERE card_code = ? AND receipt_id = ? AND period_id IS NULL
                        """,
                        (
                            stored_period_id,
                            _json_digest(
                                {"receipt": bound_receipt, "period": bound_period}
                            ),
                            card_code,
                            receipt["receipt_id"],
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE card_periods
                        SET status = 'CLOSED',
                            closed_by_receipt_id = ?,
                            original_received_at = ?,
                            finalized_at = ?,
                            reconciliation_status = 'RECEIPT_CLOSED',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE card_code = ? AND period_id = ? AND status = 'OPEN'
                        """,
                        (
                            receipt["receipt_id"],
                            receipt["original_received_at"],
                            receipt["original_received_at"],
                            card_code,
                            stored_period_id,
                        ),
                    )
                    period_row = connection.execute(
                        """
                        SELECT * FROM card_periods
                        WHERE card_code = ? AND period_id = ?
                        """,
                        (card_code, stored_period_id),
                    ).fetchone()
                    receipt["period_id"] = stored_period_id
                    return self._statement_receipt_result(
                        receipt,
                        period_row,
                        idempotent_replay=True,
                    )
                period_row = None
                period_id = normalized_period["period_id"]
                if period_id_supplied is not None:
                    period_row = connection.execute(
                        """
                        SELECT * FROM card_periods
                        WHERE period_id = ?
                        """,
                        (period_id_supplied,),
                    ).fetchone()
                    if period_row is None and normalized_period["period_start"] is None:
                        raise ValueError(
                            f"Unknown cashback period: {period_id_supplied}"
                        )
                elif normalized_period["period_start"] is not None:
                    period_row = connection.execute(
                        """
                        SELECT * FROM card_periods
                        WHERE card_code = ? AND period_start = ? AND period_end = ?
                        """,
                        (
                            card_code,
                            normalized_period["period_start"],
                            normalized_period["period_end"],
                        ),
                    ).fetchone()

                if period_row is not None:
                    if str(period_row["card_code"]).upper() != card_code:
                        raise ValueError(
                            "period_id does not match statement receipt card_code"
                        )
                    if normalized_period["period_start"] is not None and (
                        not _same_instant(
                            period_row["period_start"],
                            normalized_period["period_start"],
                        )
                        or not _same_instant(
                            period_row["period_end"],
                            normalized_period["period_end"],
                        )
                    ):
                        raise ValueError(
                            "period_id does not match supplied period bounds"
                        )

                if period_row is None and normalized_period["period_start"] is None:
                    open_periods = connection.execute(
                        """
                        SELECT * FROM card_periods
                        WHERE card_code = ? AND status = 'OPEN'
                        ORDER BY period_start, period_id
                        """,
                        (card_code,),
                    ).fetchall()
                    matching_periods = [
                        candidate
                        for candidate in open_periods
                        if _same_instant(
                            candidate["period_end"],
                            receipt["original_received_at"],
                        )
                    ]
                    if len(matching_periods) > 1:
                        raise ValueError(
                            "multiple open cashback periods match statement receipt timestamp"
                        )
                    period_row = matching_periods[0] if matching_periods else None

                if period_row is None and normalized_period["period_start"] is not None:
                    if not _same_instant(
                        normalized_period["period_end"],
                        receipt["original_received_at"],
                    ):
                        raise ValueError(
                            "statement receipt timestamp must equal period_end"
                        )
                    overlap = connection.execute(
                        """
                        SELECT 1 FROM card_periods
                        WHERE card_code = ?
                          AND period_start < ?
                          AND period_end > ?
                        LIMIT 1
                        """,
                        (
                            card_code,
                            normalized_period["period_end"],
                            normalized_period["period_start"],
                        ),
                    ).fetchone()
                    if overlap is not None:
                        raise ValueError(
                            f"cashback periods overlap for card {card_code}"
                        )
                    connection.execute(
                        """
                        INSERT INTO card_periods (
                            card_code, period_start, period_end, period_id, status
                        ) VALUES (?, ?, ?, ?, 'OPEN')
                        """,
                        (
                            card_code,
                            normalized_period["period_start"],
                            normalized_period["period_end"],
                            period_id,
                        ),
                    )
                    period_row = connection.execute(
                        """
                        SELECT * FROM card_periods
                        WHERE period_id = ?
                        """,
                        (period_id,),
                    ).fetchone()

                if period_row is not None:
                    status = str(period_row["status"]).upper()
                    if status != "OPEN":
                        raise ValueError(
                            "cashback period is already closed by another receipt"
                        )
                    if not _same_instant(
                        period_row["period_end"],
                        receipt["original_received_at"],
                    ):
                        raise ValueError(
                            "statement receipt timestamp must equal period_end"
                        )

                stored_period_id = (
                    str(period_row["period_id"])
                    if period_row is not None
                    else period_id
                )
                connection.execute(
                    """
                    INSERT INTO statement_receipts (
                        receipt_id, source_identity, card_code,
                        original_received_at, period_id, receipt_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt["receipt_id"],
                        receipt["source_identity"],
                        card_code,
                        receipt["original_received_at"],
                        stored_period_id,
                        receipt_sha256,
                    ),
                )
                if (
                    period_row is not None
                    and str(period_row["status"]).upper() == "OPEN"
                ):
                    connection.execute(
                        """
                        UPDATE card_periods
                        SET status = 'CLOSED',
                            closed_by_receipt_id = ?,
                            original_received_at = ?,
                            finalized_at = ?,
                            reconciliation_status = 'RECEIPT_CLOSED',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE card_code = ? AND period_id = ?
                        """,
                        (
                            receipt["receipt_id"],
                            receipt["original_received_at"],
                            receipt["original_received_at"],
                            card_code,
                            stored_period_id,
                        ),
                    )
                    period_row = connection.execute(
                        """
                        SELECT * FROM card_periods
                        WHERE card_code = ? AND period_id = ?
                        """,
                        (card_code, stored_period_id),
                    ).fetchone()
                receipt["period_id"] = stored_period_id
                return self._statement_receipt_result(
                    receipt,
                    period_row,
                    idempotent_replay=False,
                )

    def receipt_rows(self, card_code: str | None = None) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            if card_code is None:
                rows = connection.execute(
                    """
                    SELECT * FROM statement_receipts
                    ORDER BY original_received_at, card_code, receipt_id
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM statement_receipts
                    WHERE card_code = ?
                    ORDER BY original_received_at, receipt_id
                    """,
                    (str(card_code).strip().upper(),),
                ).fetchall()
        return [dict(row) for row in rows]

    def ingest_statement_receipt(
        self,
        payload: dict[str, Any],
        *,
        period: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.record_statement_receipt(payload, period=period)

    def apply_statement_receipt(
        self,
        payload: dict[str, Any],
        *,
        period: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.record_statement_receipt(payload, period=period)

    def close_period(
        self,
        payload: dict[str, Any],
        *,
        period: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.record_statement_receipt(payload, period=period)

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
            validate_period=False,
        )

        statement_content_sha256 = _statement_content_digest(
            statement_events,
            statement_transaction_ids,
            statement_reference=statement_reference,
            card_code=card_code,
            period_start=period_start,
            period_end=period_end,
        )
        legacy_statement_content_sha256 = _legacy_statement_content_digest_from_payload(
            payload,
            statement_events,
            statement_transaction_ids,
            statement_reference=statement_reference,
            card_code=card_code,
            period_start=period_start,
            period_end=period_end,
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

        with closing(self._connect()) as connection:
            with connection:
                # NOTE: Legacy blank digests must be bound exactly once; acquire
                # the write lock before reading so a concurrent recovery cannot
                # overwrite the first committed statement digest.
                connection.execute("BEGIN IMMEDIATE")
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
                        raise ValueError(
                            "statement_reference was already used for a different card or period"
                        )
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
                            raise ValueError(
                                "legacy reconciliation digest recovery proof is invalid"
                            )
                        _statement_period_is_valid(
                            payload,
                            period_start=period_start,
                            period_end=period_end,
                            legacy_local_date=False,
                        )
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
                            (
                                statement_sha256,
                                statement_content_sha256,
                                statement_reference,
                            ),
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
                        raise ValueError(
                            "legacy reconciliation digest columns are incomplete"
                        )
                    if prior_statement_sha256 != statement_sha256:
                        _statement_period_is_valid(
                            payload,
                            period_start=period_start,
                            period_end=period_end,
                            legacy_local_date=False,
                        )
                        raise ValueError(
                            "statement_reference was already used for different statement content or digest"
                        )
                    if prior_content_sha256 == statement_content_sha256:
                        _statement_period_is_valid(
                            payload,
                            period_start=period_start,
                            period_end=period_end,
                            legacy_local_date=False,
                        )
                    elif prior_content_sha256 == legacy_statement_content_sha256:
                        _statement_period_is_valid(
                            payload,
                            period_start=period_start,
                            period_end=period_end,
                            legacy_local_date=True,
                        )
                    else:
                        _statement_period_is_valid(
                            payload,
                            period_start=period_start,
                            period_end=period_end,
                            legacy_local_date=False,
                        )
                        raise ValueError(
                            "statement_reference was already used for different statement content or digest"
                        )
                    if (
                        supplied_content_sha256 is not None
                        and supplied_content_sha256 != prior_content_sha256
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

                _statement_period_is_valid(
                    payload,
                    period_start=period_start,
                    period_end=period_end,
                    legacy_local_date=False,
                )
                if (
                    supplied_content_sha256 is not None
                    and supplied_content_sha256 != statement_content_sha256
                ):
                    raise ValueError(
                        "statement content digest does not match canonical content"
                    )
                candidates = [
                    dict(row)
                    for row in connection.execute(
                        """
                        SELECT * FROM cashback_events
                        WHERE card_code = ? AND status = 'ACTIVE' AND source != 'statement'
                          AND substr(occurred_at, 1, 10) BETWEEN ? AND ?
                        ORDER BY occurred_at, source_event_id
                        """,
                        (card_code, period_start.isoformat(), period_end.isoformat()),
                    ).fetchall()
                ]
                remaining = {row["source_event_id"]: row for row in candidates}
                matched = 0
                statement_only = 0
                for event in statement_events:
                    ranked = _rank_statement_candidates(event, remaining.values())
                    unique_match = bool(len(ranked) == 1 and ranked[0][0] > 0)
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
                            "SELECT * FROM cashback_events WHERE source_event_id = ?",
                            (event["source_event_id"],),
                        ).fetchone()
                        if existing:
                            connection.execute(
                                """
                                UPDATE cashback_events
                                SET status='ACTIVE', reconciliation_status='RECONCILED',
                                    statement_reference=?, updated_at=CURRENT_TIMESTAMP
                                WHERE source_event_id=?
                                """,
                                (statement_reference, event["source_event_id"]),
                            )
                            matched += 1
                            continue
                        existing = connection.execute(
                            "SELECT * FROM cashback_events WHERE identity_key = ?",
                            (event["identity_key"],),
                        ).fetchone()
                        if existing:
                            existing_id = str(existing["source_event_id"])
                            if existing_id == event["source_event_id"]:
                                connection.execute(
                                    """
                                    UPDATE cashback_events
                                    SET status='ACTIVE', reconciliation_status='RECONCILED',
                                        statement_reference=?, updated_at=CURRENT_TIMESTAMP
                                    WHERE source_event_id=?
                                    """,
                                    (statement_reference, existing_id),
                                )
                                matched += 1
                                continue
                            if (
                                existing["source"] != "statement"
                                and not ranked
                                and existing_id in remaining
                            ):
                                connection.execute(
                                    """
                                    UPDATE cashback_events
                                    SET status='ACTIVE', reconciliation_status='RECONCILED',
                                        statement_reference=?, updated_at=CURRENT_TIMESTAMP
                                    WHERE source_event_id=?
                                    """,
                                    (statement_reference, existing_id),
                                )
                                remaining.pop(existing_id)
                                matched += 1
                                continue

                        statement_event = event
                        if existing:
                            collision_identity = _statement_collision_identity(event)
                            collision_number = 0
                            while connection.execute(
                                "SELECT 1 FROM cashback_events WHERE identity_key = ?",
                                (collision_identity,),
                            ).fetchone():
                                collision_number += 1
                                collision_identity = hashlib.sha256(
                                    f"{event['identity_key']}|statement:{event['source_event_id']}|collision:{collision_number}".encode(
                                        "utf-8"
                                    )
                                ).hexdigest()
                            statement_event = {
                                **event,
                                "identity_key": collision_identity,
                            }
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
            raise ValueError(
                "unsupported correction fields: " + ", ".join(sorted(invalid))
            )
        reason = str(payload.get("reason") or "").strip() or None
        correction_source = str(payload.get("source") or "manual").strip() or "manual"
        is_ai = correction_source.casefold().startswith(("ai", "codex"))
        if is_ai:
            protected = set(changes) - AI_CORRECTABLE_EVENT_FIELDS
            if protected:
                raise ValueError(
                    "AI corrections cannot modify protected event fields: "
                    + ", ".join(sorted(protected))
                )
        canonical_changes = json.dumps(changes, sort_keys=True)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                prior = connection.execute(
                    "SELECT * FROM event_corrections WHERE correction_id = ?",
                    (correction_id,),
                ).fetchone()
                if prior:
                    if (
                        prior["source_event_id"] != source_event_id
                        or prior["changes_json"] != canonical_changes
                    ):
                        raise ValueError(
                            "correction_id was already used for different changes"
                        )
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
                if is_ai:
                    manual_locked: set[str] = set()
                    for history in connection.execute(
                        """
                        SELECT changes_json, correction_source
                        FROM event_corrections
                        WHERE source_event_id = ?
                        ORDER BY created_at, correction_id
                        """,
                        (source_event_id,),
                    ):
                        history_source = str(history["correction_source"] or "")
                        if history_source.casefold().startswith(("ai", "codex")):
                            continue
                        history_changes = json.loads(
                            str(history["changes_json"] or "{}")
                        )
                        if isinstance(history_changes, dict):
                            manual_locked.update(history_changes)
                    locked = set(changes) & manual_locked
                    if locked:
                        raise ValueError(
                            "AI corrections cannot modify fields locked by a manual correction: "
                            + ", ".join(sorted(locked))
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
                    "decision_trace": json.loads(
                        str(row["decision_trace_json"] or "[]")
                    ),
                    "ai_trace": json.loads(str(row["ai_trace_json"] or "[]")),
                }
                normalized = _normalize_event({**source, **changes})
                economic_changed = any(
                    (
                        not _same_instant(row[field], normalized[field])
                        if field == "occurred_at"
                        else row[field] != normalized[field]
                    )
                    for field in EVENT_CANONICAL_FIELDS
                )
                if not economic_changed:
                    normalized["identity_key"] = row["identity_key"]
                identity_owner = connection.execute(
                    "SELECT source_event_id FROM cashback_events WHERE identity_key = ?",
                    (normalized["identity_key"],),
                ).fetchone()
                if (
                    identity_owner
                    and identity_owner["source_event_id"] != source_event_id
                ):
                    raise ValueError(
                        "correction would collide with an existing source_event_id"
                    )
                column_by_field = {
                    "amount_aed": "amount_aed_minor",
                    "tags": "tags_json",
                    "ai_trace": "ai_trace_json",
                }
                assignments_by_column = {}
                for field in changes:
                    column = column_by_field.get(field, field)
                    assignments_by_column[column] = normalized[column]
                if economic_changed:
                    assignments_by_column["identity_key"] = normalized["identity_key"]
                assignments = ", ".join(
                    f"{column} = ?" for column in assignments_by_column
                )
                connection.execute(
                    f"UPDATE cashback_events SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE source_event_id = ?",
                    tuple(assignments_by_column.values()) + (source_event_id,),
                )
                connection.execute(
                    """
                    INSERT INTO event_corrections (
                        correction_id, source_event_id, changes_json, reason, correction_source
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        correction_id,
                        source_event_id,
                        canonical_changes,
                        reason,
                        correction_source,
                    ),
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
        if any(
            key in payload
            for key in (
                "statement_receipt",
                "receipt",
                "receipt_id",
                "source_identity",
                "original_received_at",
                "received_at",
                "received_timestamp",
                "email_received_at",
            )
        ):
            return self.record_statement_receipt(payload)
        statement_reference = str(payload.get("statement_reference") or "").strip()
        evidence_reference = str(
            payload.get("statement_evidence_reference") or ""
        ).strip()
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
        actual_import_receipt, actual_import_receipt_sha256 = _trusted_actual_receipt(
            payload
        )
        if "actual_import_verified" in payload and not _boolean(
            payload.get("actual_import_verified")
        ):
            raise ValueError(
                "actual_import_verified cannot replace an Actual import receipt digest"
            )
        acknowledge_variances = _boolean(
            payload.get("acknowledge_variances"), default=False
        )
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                run = connection.execute(
                    "SELECT * FROM reconciliation_runs WHERE statement_reference = ?",
                    (statement_reference,),
                ).fetchone()
                if run is None:
                    raise ValueError(
                        "A successful statement reconciliation is required before finalization"
                    )
                if str(run["statement_sha256"] or "") != statement_sha256:
                    raise ValueError(
                        "statement digest does not match the reconciliation receipt"
                    )
                if str(actual_import_receipt["period_start"]) != str(
                    run["period_start"]
                ) or str(actual_import_receipt["period_end"]) != str(run["period_end"]):
                    raise ValueError(
                        "actual_import_receipt period does not match the reconciliation receipt"
                    )
                receipt_card = (
                    str(actual_import_receipt.get("card_code") or "").strip().upper()
                )
                if not receipt_card:
                    raise ValueError("actual_import_receipt.card_code is required")
                if receipt_card != str(run["card_code"]).strip().upper():
                    raise ValueError(
                        "actual_import_receipt account identity/card does not match the reconciled card"
                    )
                if (
                    supplied_content_sha256 is not None
                    and supplied_content_sha256
                    != str(run["statement_content_sha256"] or "")
                ):
                    raise ValueError(
                        "statement content digest does not match the reconciliation receipt"
                    )
                if run["notification_only_count"] and not acknowledge_variances:
                    raise ValueError(
                        "Unresolved notification variances require explicit acknowledgement before finalization"
                    )
                final_bounds = _legacy_date_period_bounds(
                    run["period_start"], run["period_end"]
                )
                if final_bounds is None:
                    raise ValueError("reconciliation period bounds must be ISO dates")
                final_start, final_end = final_bounds
                existing = connection.execute(
                    """
                    SELECT * FROM card_periods
                    WHERE card_code = ?
                      AND (
                        (period_start = ? AND period_end = ?)
                        OR (period_start = ? AND period_end = ?)
                      )
                    """,
                    (
                        run["card_code"],
                        run["period_start"],
                        run["period_end"],
                        final_start,
                        final_end,
                    ),
                ).fetchone()
                if existing and existing["status"] == "FINALIZED":
                    if (
                        existing["statement_reference"] != statement_reference
                        or str(existing["statement_sha256"] or "") != statement_sha256
                        or str(existing["statement_content_sha256"] or "")
                        != str(run["statement_content_sha256"] or "")
                        or existing["statement_evidence_reference"]
                        != evidence_reference
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
                            str(run["card_code"]),
                            str(run["period_start"]),
                            str(run["period_end"]),
                        ),
                        "card_code": existing["card_code"],
                        "period_start": run["period_start"],
                        "period_end": run["period_end"],
                        "status": existing["status"],
                        "statement_reference": existing["statement_reference"],
                        "statement_sha256": existing["statement_sha256"],
                        "statement_content_sha256": existing[
                            "statement_content_sha256"
                        ],
                        "statement_evidence_reference": existing[
                            "statement_evidence_reference"
                        ],
                        "statement_document_url": existing["statement_document_url"],
                        "actual_import_receipt_sha256": existing[
                            "actual_import_receipt_sha256"
                        ],
                        "actual_verification_sha256": existing[
                            "actual_verification_sha256"
                        ],
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
                        card_code, period_start, period_end, period_id,
                        statement_reference, statement_sha256, statement_content_sha256,
                        statement_evidence_reference, statement_document_url,
                        actual_import_receipt_sha256, actual_verification_sha256,
                        actual_import_verified, reconciliation_status, status, finalized_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'FINALIZED', ?)
                    ON CONFLICT(card_code, period_start, period_end) DO UPDATE SET
                        period_id=COALESCE(card_periods.period_id, excluded.period_id),
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
                        run["card_code"],
                        final_start,
                        final_end,
                        f"cashback-period:{run['card_code']}:{final_start}:{final_end}",
                        statement_reference,
                        statement_sha256,
                        run["statement_content_sha256"],
                        evidence_reference,
                        document_url,
                        actual_import_receipt_sha256,
                        actual_import_receipt_sha256,
                        reconciliation_status,
                        now,
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
                    raise ValueError(
                        f"No active cashback programme for {run['card_code']} on {next_start}"
                    )
                calculated_start, next_end = statement_period(
                    next_start,
                    next_program.statement_close_day,
                )
                if calculated_start != next_start:
                    raise ValueError(
                        "Configured statement cycle does not continue from the reconciled period"
                    )
                next_start_bound = _period_iso_datetime(
                    datetime.combine(next_start, datetime.min.time(), tzinfo=UTC),
                    "period_start",
                )
                next_end_bound = _period_iso_datetime(
                    datetime.combine(
                        next_end + timedelta(days=1), datetime.min.time(), tzinfo=UTC
                    ),
                    "period_end",
                )
                next_period_id = f"cashback-period:{run['card_code']}:{next_start_bound}:{next_end_bound}"
                connection.execute(
                    """
                    INSERT INTO card_periods (
                        card_code, period_start, period_end, period_id, status
                    ) VALUES (?, ?, ?, ?, 'OPEN')
                    ON CONFLICT(card_code, period_start, period_end) DO UPDATE SET
                        period_id=COALESCE(card_periods.period_id, excluded.period_id)
                    """,
                    (
                        run["card_code"],
                        next_start_bound,
                        next_end_bound,
                        next_period_id,
                    ),
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

    def rows_for_period(
        self,
        period_id: str,
        *,
        card_code: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return active events in the half-open period interval ``[start, end)``."""
        requested_period_id = str(period_id or "").strip()
        if not requested_period_id:
            raise ValueError("period_id is required")
        with closing(self._connect()) as connection:
            period = connection.execute(
                """
                SELECT * FROM card_periods
                WHERE period_id = ?
                """,
                (requested_period_id,),
            ).fetchone()
            if period is None:
                raise ValueError(f"Unknown cashback period: {requested_period_id}")
            selected_card = str(card_code or period["card_code"]).strip().upper()
            if selected_card != str(period["card_code"]).upper():
                return []
            records = connection.execute(
                """
                SELECT * FROM cashback_events
                WHERE card_code = ? AND status = 'ACTIVE'
                ORDER BY occurred_at, source_event_id
                """,
                (selected_card,),
            ).fetchall()

        start = _utc_datetime(period["period_start"])
        end = _utc_datetime(period["period_end"])
        return [
            dict(row)
            for row in records
            if start <= _utc_datetime(row["occurred_at"]) < end
        ]

    def events_for_period(
        self,
        period_id: str,
        *,
        card_code: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.rows_for_period(period_id, card_code=card_code)

    def fx_replay_for_source_event_ids(
        self,
        source_event_ids: Iterable[str],
    ) -> dict[str, list[dict[str, Any]]]:
        """Read persisted FX conversion traces for source replay only."""
        ids = tuple(
            sorted(
                {
                    str(source_event_id).strip()
                    for source_event_id in source_event_ids
                    if str(source_event_id).strip()
                }
            )
        )
        if not ids:
            return {}
        placeholders = ", ".join("?" for _ in ids)
        with closing(self._connect()) as connection:
            records = connection.execute(
                f"""
                SELECT source_event_id, decision_trace_json
                FROM cashback_events
                WHERE source_event_id IN ({placeholders})
                """,
                ids,
            ).fetchall()
        replay: dict[str, list[dict[str, Any]]] = {}
        for row in records:
            try:
                traces = json.loads(str(row["decision_trace_json"] or "[]"))
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"FX replay trace is invalid for {row['source_event_id']}"
                ) from error
            if not isinstance(traces, list) or any(
                not isinstance(trace, dict) for trace in traces
            ):
                raise ValueError(
                    f"FX replay trace must be a list for {row['source_event_id']}"
                )
            conversions = [
                trace for trace in traces if trace.get("trace_type") == "FX_CONVERSION"
            ]
            if conversions:
                replay[str(row["source_event_id"])] = conversions
        return replay

    def rows(self, start: date, end: date) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            records = connection.execute(
                """
                SELECT * FROM cashback_events
                WHERE substr(occurred_at, 1, 10) BETWEEN ? AND ?
                  AND status = 'ACTIVE'
                ORDER BY occurred_at, source_event_id
                """,
                (start.isoformat(), end.isoformat()),
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

    def stats(
        self,
        *,
        included_cards: Iterable[str] | None = None,
        ingest_source: str | None = None,
    ) -> dict[str, Any]:
        eligible = (
            None
            if included_cards is None
            else {
                str(card).strip().upper()
                for card in included_cards
                if str(card).strip()
            }
        )
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS event_count, MAX(occurred_at) AS last_event_at,
                       SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END) AS live_event_count,
                       SUM(CASE WHEN reconciliation_status = 'VARIANCE' THEN 1 ELSE 0 END) AS variance_count
                FROM cashback_events
                """
            ).fetchone()
            ingest_query = """
                SELECT source, last_success_at, scanned_count, accepted_count, cursor
                FROM ingest_state
            """
            ingest_parameters: tuple[object, ...] = ()
            if ingest_source:
                ingest_query += " WHERE source = ?"
                ingest_parameters = (ingest_source,)
            ingest_query += " ORDER BY last_success_at DESC LIMIT 1"
            ingest = connection.execute(ingest_query, ingest_parameters).fetchone()
            correction_count = connection.execute(
                "SELECT COUNT(*) AS count FROM event_corrections"
            ).fetchone()["count"]
        result = dict(row)
        result["live_event_count"] = result["live_event_count"] or 0
        result["variance_count"] = result["variance_count"] or 0
        result["last_successful_ingest_at"] = (
            ingest["last_success_at"] if ingest else None
        )
        result["last_ingest_source"] = ingest["source"] if ingest else None
        result["last_scan_count"] = ingest["scanned_count"] if ingest else 0
        result["last_accepted_count"] = ingest["accepted_count"] if ingest else 0
        result["last_ingest_cursor"] = ingest["cursor"] if ingest else None
        result["acknowledged_alerts"] = self.alert_acknowledgements()
        result["correction_count"] = correction_count
        period_rows = self.period_rows()
        if eligible is not None:
            period_rows = [
                row
                for row in period_rows
                if str(row.get("card_code") or "").strip().upper() in eligible
            ]
        result["card_periods"] = period_rows
        result["statement_receipts"] = self.statement_receipts(
            included_cards=eligible,
            limit=200,
        )
        result["statement_receipt_count"] = len(result["statement_receipts"])
        result["bank_closed_count"] = sum(
            receipt["state"] == BANK_CLOSED for receipt in result["statement_receipts"]
        )
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
                    or configured_reward_bucket(
                        programs, card, purchase_type, channel, currency
                    )
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
                    "decision_trace": json.loads(
                        str(row["decision_trace_json"] or "[]")
                    ),
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
    memberships: Iterable[object] | dict[str, object] | None = None,
    ingest_source: str | None = None,
    check_schedule_config_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    configuration = load_program_configuration(program_config_path, as_of=as_of)
    configured_programs = programs_from_config(configuration, as_of, as_of=as_of)
    eligible_cards = eligible_card_codes(configured_programs, memberships)
    programs = tuple(
        program
        for program in configured_programs
        if program.card.upper() in eligible_cards
    )
    configured_periods = {
        program.card: statement_period(as_of, program.statement_close_day)
        for program in programs
    }
    explicit_periods: dict[str, list[dict[str, Any]]] = {}
    for period in store.period_rows():
        explicit_periods.setdefault(str(period["card_code"]).upper(), []).append(period)

    periods: dict[str, tuple[date, date]] = {}
    event_rows: list[dict[str, Any]] = []
    as_of_start = datetime.combine(as_of, datetime.min.time(), tzinfo=UTC)
    as_of_end = as_of_start + timedelta(days=1)
    for program in programs:
        card = program.card
        candidates = explicit_periods.get(card, [])
        if not candidates:
            periods[card] = configured_periods[card]
            period_start, period_end = periods[card]
            event_rows.extend(
                row
                for row in store.rows(period_start, min(as_of, period_end))
                if row["card_code"] == card
            )
            continue

        current = next(
            (
                period
                for period in sorted(
                    candidates,
                    key=lambda item: _utc_datetime(item["period_start"]),
                )
                if str(period["status"]).upper() == "OPEN"
                and _utc_datetime(period["period_start"]) < as_of_end
                and _utc_datetime(period["period_end"]) > as_of_start
            ),
            None,
        )
        if current is None:
            continue
        current_start = _utc_datetime(current["period_start"])
        current_end = _utc_datetime(current["period_end"])
        current_end_date = current_end.date()
        if current_end.time() == datetime.min.time():
            current_end_date -= timedelta(days=1)
        periods[card] = (current_start.date(), current_end_date)
        event_rows.extend(
            row
            for row in store.rows_for_period(current["period_id"], card_code=card)
            if _utc_datetime(row["occurred_at"]) < as_of_end
        )
    transactions = events_to_transactions(event_rows, programs)
    result = cashback_dashboard(
        programs,
        transactions,
        as_of,
        payment_intents_from_config(configuration),
        memberships=memberships,
        periods_by_card=periods,
        routing_profiles=configuration.get("routing_profiles") or (),
        route_policies=configuration.get("route_policies") or None,
        base_currency=str(configuration.get("currency") or "AED"),
    )
    result["profile"] = configuration.get("profile") or {}
    stats = store.stats(
        included_cards=eligible_cards,
        ingest_source=ingest_source,
    )
    checked_at = now or datetime.now(UTC)
    health = scheduled_sync_health(
        stats.get("last_successful_ingest_at"),
        now=checked_at,
        grace_minutes=stale_after_minutes,
        ingest_source=ingest_source,
        config_path=check_schedule_config_path,
    )
    result["data_status"] = {
        "mode": "LIVE_TRANSACTION_EVENTS",
        "generated_at": checked_at.isoformat(),
        "stale_after_minutes": stale_after_minutes,
        **stats,
        **health,
    }
    return result


def write_dashboard(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)
