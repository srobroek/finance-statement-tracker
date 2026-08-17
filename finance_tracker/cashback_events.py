from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
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


ACTIVE_STATUSES = frozenset({"PROVISIONAL", "CONFIRMED"})
VALID_STATUSES = ACTIVE_STATUSES | {"IGNORED", "REVERSED"}
VALID_EVENT_TYPES = frozenset({"PURCHASE", "REFUND", "REVERSAL"})
VALID_RECONCILIATION_STATUSES = frozenset({"UNMATCHED", "MATCHED", "VARIANCE", "RECONCILED"})
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


def default_payment_intents() -> tuple[PaymentIntent, ...]:
    return payment_intents_from_config(load_program_configuration())


def _iso_datetime(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("occurred_at is required")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _amount_minor(value: object) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("amount must be numeric") from error
    if amount <= 0:
        raise ValueError("amount must be greater than zero; use event_type REFUND for credits")
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


def _normalize_event(source: dict[str, Any]) -> dict[str, Any]:
    source_event_id = str(source.get("source_event_id") or "").strip()
    card_code = str(source.get("card_code") or "").strip().upper()
    if not source_event_id:
        raise ValueError("source_event_id is required")
    if not card_code:
        raise ValueError("card_code is required")
    status = str(source.get("status") or "PROVISIONAL").upper()
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {sorted(VALID_STATUSES)}")
    event_type = str(source.get("event_type") or "PURCHASE").upper()
    if event_type not in VALID_EVENT_TYPES:
        raise ValueError(f"event_type must be one of {sorted(VALID_EVENT_TYPES)}")
    reversal_of = str(source.get("reversal_of") or "").strip() or None
    if event_type == "REVERSAL" and not reversal_of:
        raise ValueError("reversal_of is required for REVERSAL events")
    reconciliation_status = str(source.get("reconciliation_status") or "UNMATCHED").upper()
    if reconciliation_status not in VALID_RECONCILIATION_STATUSES:
        raise ValueError(
            f"reconciliation_status must be one of {sorted(VALID_RECONCILIATION_STATUSES)}"
        )
    confidence = _confidence(source.get("confidence"))
    review_required = _boolean(source.get("review_required"), default=confidence < 0.8)
    tags = source.get("tags") or []
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("tags must be a list of strings")
    normalized = {
        "source_event_id": source_event_id,
        "occurred_at": _iso_datetime(source.get("occurred_at")),
        "card_code": card_code,
        "amount_aed_minor": _amount_minor(_event_amount(source)),
        "currency": str(source.get("currency") or "AED").strip().upper(),
        "purchase_type": str(source.get("purchase_type") or "GENERAL").strip().upper(),
        "channel": str(source.get("channel") or "UNKNOWN").strip().upper(),
        "merchant": str(source.get("merchant") or "Unknown").strip(),
        "bucket_code": str(source.get("bucket_code") or "").strip().upper() or None,
        "event_type": event_type,
        "source": str(source.get("source") or "email").strip(),
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
                        statement_evidence_reference TEXT,
                        statement_document_url TEXT,
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
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_cashback_events_identity ON cashback_events(identity_key) WHERE identity_key IS NOT NULL"
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
        duplicates = 0
        with closing(self._connect()) as connection:
            with connection:
                for event in normalized:
                    identity_owner = connection.execute(
                        "SELECT source_event_id FROM cashback_events WHERE identity_key = ?",
                        (event["identity_key"],),
                    ).fetchone()
                    if identity_owner and identity_owner["source_event_id"] != event["source_event_id"]:
                        duplicates += 1
                        continue
                    exists = connection.execute(
                        "SELECT 1 FROM cashback_events WHERE source_event_id = ?",
                        (event["source_event_id"],),
                    ).fetchone()
                    columns = tuple(event)
                    placeholders = ", ".join("?" for _ in columns)
                    assignments = ", ".join(
                        f"{column}=excluded.{column}" for column in columns if column != "source_event_id"
                    )
                    connection.execute(
                        f"""
                        INSERT INTO cashback_events ({', '.join(columns)})
                        VALUES ({placeholders})
                        ON CONFLICT(source_event_id) DO UPDATE SET
                            {assignments}, updated_at=CURRENT_TIMESTAMP
                        """,
                        tuple(event[column] for column in columns),
                    )
                    if exists:
                        updated += 1
                    else:
                        inserted += 1
        return {"inserted": inserted, "updated": updated, "duplicates": duplicates}

    def validate(self, events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """Validate events without persisting them or exposing normalized payloads."""
        normalized = [_normalize_event(event) for event in events]
        if not normalized:
            raise ValueError("At least one event is required")
        return normalized

    def record_ingest_success(self, source: dict[str, Any]) -> dict[str, Any]:
        source_name = str(source.get("source") or "mailbox").strip()
        if not source_name:
            raise ValueError("source is required")
        completed_at = _iso_datetime(source.get("completed_at") or datetime.now(timezone.utc).isoformat())
        try:
            scanned_count = int(source.get("scanned_count") or 0)
            accepted_count = int(source.get("accepted_count") or 0)
        except (TypeError, ValueError) as error:
            raise ValueError("scanned_count and accepted_count must be integers") from error
        if scanned_count < 0 or accepted_count < 0:
            raise ValueError("ingest counts must be non-negative")
        cursor = str(source.get("cursor") or "").strip() or None
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO ingest_state (
                        source, last_success_at, scanned_count, accepted_count, cursor
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(source) DO UPDATE SET
                        last_success_at=excluded.last_success_at,
                        scanned_count=excluded.scanned_count,
                        accepted_count=excluded.accepted_count,
                        cursor=excluded.cursor,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (source_name, completed_at, scanned_count, accepted_count, cursor),
                )
        return {
            "source": source_name,
            "last_success_at": completed_at,
            "scanned_count": scanned_count,
            "accepted_count": accepted_count,
            "cursor": cursor,
        }

    def ingest_state(self, source: str = "outlook") -> dict[str, Any]:
        source_name = str(source or "outlook").strip()
        if not source_name:
            raise ValueError("source is required")
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT source, last_success_at, scanned_count, accepted_count, cursor
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
        try:
            period_start = date.fromisoformat(str(payload.get("period_start")))
            period_end = date.fromisoformat(str(payload.get("period_end")))
        except ValueError as error:
            raise ValueError("period_start and period_end must be ISO dates") from error
        if period_end < period_start:
            raise ValueError("period_end cannot be before period_start")
        rows = payload.get("transactions")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("transactions must be a list of statement transaction objects")

        statement_events = []
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
                "status": "CONFIRMED",
                "confidence": 1,
                "reconciliation_status": "RECONCILED",
                "statement_reference": statement_reference,
            })
            occurred = date.fromisoformat(event["occurred_at"][:10])
            if occurred < period_start or occurred > period_end:
                raise ValueError(f"statement transaction {transaction_id} falls outside the statement period")
            statement_events.append(event)

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
                    return {
                        "statement_reference": statement_reference,
                        "card_code": prior["card_code"],
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
                        WHERE card_code = ? AND status = 'PROVISIONAL'
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
                    event_date = date.fromisoformat(event["occurred_at"][:10])
                    event_merchant = _merchant_key(event["merchant"])
                    ranked = []
                    for candidate in remaining.values():
                        if candidate["amount_aed_minor"] != event["amount_aed_minor"]:
                            continue
                        if _event_polarity(candidate["event_type"]) != _event_polarity(event["event_type"]):
                            continue
                        candidate_date = date.fromisoformat(str(candidate["occurred_at"])[:10])
                        day_gap = abs((event_date - candidate_date).days)
                        if day_gap > 3:
                            continue
                        candidate_merchant = _merchant_key(candidate["merchant"])
                        merchant_score = 2 if candidate_merchant == event_merchant else 0
                        if (
                            merchant_score == 0
                            and candidate_merchant
                            and event_merchant
                            and (candidate_merchant in event_merchant or event_merchant in candidate_merchant)
                        ):
                            merchant_score = 1
                        ranked.append((merchant_score, -day_gap, str(candidate["source_event_id"])))
                    ranked.sort(reverse=True)
                    unique_match = bool(
                        ranked
                        and (len(ranked) == 1 or ranked[0][:2] != ranked[1][:2])
                        and (ranked[0][0] > 0 or (len(ranked) == 1 and ranked[0][1] == 0))
                    )
                    if unique_match:
                        source_event_id = ranked[0][2]
                        connection.execute(
                            """
                            UPDATE cashback_events
                            SET status='CONFIRMED', reconciliation_status='RECONCILED',
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
                        if existing:
                            connection.execute(
                                "UPDATE cashback_events SET status='CONFIRMED', reconciliation_status='RECONCILED', updated_at=CURRENT_TIMESTAMP WHERE source_event_id=?",
                                (existing["source_event_id"],),
                            )
                            matched += 1
                            continue
                        columns = tuple(event)
                        connection.execute(
                            f"INSERT INTO cashback_events ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                            tuple(event[column] for column in columns),
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
                        matched_count, statement_only_count, notification_only_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        statement_reference,
                        card_code,
                        period_start.isoformat(),
                        period_end.isoformat(),
                        matched,
                        statement_only,
                        notification_only,
                    ),
                )
        return {
            "statement_reference": statement_reference,
            "card_code": card_code,
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
        if not _boolean(payload.get("actual_import_verified")):
            raise ValueError("actual_import_verified must be true")
        acknowledge_variances = _boolean(payload.get("acknowledge_variances"), default=False)
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                run = connection.execute(
                    "SELECT * FROM reconciliation_runs WHERE statement_reference = ?",
                    (statement_reference,),
                ).fetchone()
                if run is None:
                    raise ValueError("A successful statement reconciliation is required before finalization")
                if run["notification_only_count"] and not acknowledge_variances:
                    raise ValueError(
                        "Unresolved notification variances require explicit acknowledgement before finalization"
                    )
                existing = connection.execute(
                    "SELECT * FROM card_periods WHERE card_code=? AND period_start=? AND period_end=?",
                    (run["card_code"], run["period_start"], run["period_end"]),
                ).fetchone()
                if existing and existing["status"] == "FINALIZED":
                    return {
                        "card_code": existing["card_code"],
                        "period_start": existing["period_start"],
                        "period_end": existing["period_end"],
                        "status": existing["status"],
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
                        statement_evidence_reference, statement_document_url,
                        actual_import_verified, reconciliation_status, status, finalized_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, 'FINALIZED', ?)
                    ON CONFLICT(card_code, period_start, period_end) DO UPDATE SET
                        statement_reference=excluded.statement_reference,
                        statement_evidence_reference=excluded.statement_evidence_reference,
                        statement_document_url=excluded.statement_document_url,
                        actual_import_verified=1,
                        reconciliation_status=excluded.reconciliation_status,
                        status='FINALIZED', finalized_at=excluded.finalized_at,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        run["card_code"], run["period_start"], run["period_end"],
                        statement_reference, evidence_reference, document_url,
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
            "card_code": run["card_code"],
            "period_start": run["period_start"],
            "period_end": run["period_end"],
            "status": "FINALIZED",
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
        with closing(self._connect()) as connection:
            records = connection.execute(
                """
                SELECT * FROM cashback_events
                WHERE substr(occurred_at, 1, 10) BETWEEN ? AND ?
                  AND status IN ('PROVISIONAL', 'CONFIRMED')
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
                WHERE status IN ('PROVISIONAL', 'CONFIRMED')
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

    def stats(self) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS event_count, MAX(occurred_at) AS last_event_at,
                       SUM(CASE WHEN status IN ('PROVISIONAL', 'CONFIRMED') THEN 1 ELSE 0 END) AS live_event_count,
                       SUM(CASE WHEN reconciliation_status = 'VARIANCE' THEN 1 ELSE 0 END) AS variance_count
                FROM cashback_events
                """
            ).fetchone()
            ingest = connection.execute(
                """
                SELECT source, last_success_at, scanned_count, accepted_count, cursor
                FROM ingest_state
                ORDER BY last_success_at DESC
                LIMIT 1
                """
            ).fetchone()
            correction_count = connection.execute(
                "SELECT COUNT(*) AS count FROM event_corrections"
            ).fetchone()["count"]
        result = dict(row)
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
) -> dict[str, Any]:
    configuration = load_program_configuration(program_config_path)
    programs = programs_from_config(configuration, as_of)
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
    stats = store.stats()
    last_ingest = stats.get("last_successful_ingest_at")
    stale = True
    if last_ingest:
        parsed = datetime.fromisoformat(str(last_ingest).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
        stale = age_seconds > stale_after_minutes * 60
    result["data_status"] = {
        "mode": "LIVE_TRANSACTION_EVENTS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
