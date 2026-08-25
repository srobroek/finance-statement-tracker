from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


def _datetime(value: object, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class OutlookScanPlan:
    source: str
    window_start: str
    window_end: str
    cursor_before: str | None
    overlap_hours: int
    initial_scan: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_outlook_scan(
    ingestion_config: dict[str, Any],
    ingest_state: dict[str, Any],
    run_upper_bound: datetime,
) -> OutlookScanPlan:
    if run_upper_bound.tzinfo is None:
        raise ValueError("run_upper_bound must include a timezone")
    upper = run_upper_bound.astimezone(timezone.utc)
    outlook = ingestion_config.get("outlook")
    if not isinstance(outlook, dict):
        raise ValueError("ingestion config requires outlook")
    state = ingest_state.get("ingest_state") if isinstance(ingest_state.get("ingest_state"), dict) else ingest_state
    source = str(state.get("source") or outlook.get("cursor_source") or "outlook").strip()
    overlap_hours = int(outlook.get("scan_overlap_hours") or 0)
    initial_hours = int(outlook.get("initial_lookback_hours") or 0)
    if overlap_hours < 0 or initial_hours <= 0:
        raise ValueError("Outlook overlap must be non-negative and initial lookback must be positive")

    cursor_text = str(state.get("cursor") or "").strip() or None
    if cursor_text:
        cursor = _datetime(cursor_text, "ingest cursor")
        if cursor > upper:
            raise ValueError("ingest cursor cannot be after run_upper_bound")
        start = cursor - timedelta(hours=overlap_hours)
        initial = False
    else:
        start = upper - timedelta(hours=initial_hours)
        initial = True
    return OutlookScanPlan(
        source=source,
        window_start=start.isoformat(),
        window_end=upper.isoformat(),
        cursor_before=cursor_text,
        overlap_hours=overlap_hours,
        initial_scan=initial,
    )


def build_outlook_envelope(
    plan: OutlookScanPlan, messages: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    rows = list(messages)
    if any(not isinstance(message, dict) for message in rows):
        raise ValueError("Outlook messages must be objects")
    seen: set[str] = set()
    for message in rows:
        message_id = str(message.get("id") or "").strip()
        if not message_id:
            raise ValueError("Every Outlook message requires its exact id")
        if message_id in seen:
            raise ValueError(f"Duplicate Outlook message id in scan batch: {message_id}")
        seen.add(message_id)
        received = _datetime(message.get("receivedDateTime"), f"message {message_id} receivedDateTime")
        start = _datetime(plan.window_start, "window_start")
        end = _datetime(plan.window_end, "window_end")
        if received < start or received > end:
            raise ValueError(f"Outlook message {message_id} is outside the frozen scan window")
    return {
        "schema_version": 1,
        "source": plan.source,
        "window_start": plan.window_start,
        "completed_at": plan.window_end,
        "cursor": plan.window_end,
        "scanned_count": len(rows),
        "messages": rows,
    }


def build_ingest_commit_payload(
    envelope: dict[str, Any], service_response: dict[str, Any]
) -> dict[str, Any]:
    messages = envelope.get("messages")
    parsed = service_response.get("parse")
    if not isinstance(messages, list) or not isinstance(parsed, dict):
        raise ValueError("Envelope messages and service parse result are required")
    scanned = int(parsed.get("scanned_count", -1))
    accepted = int(parsed.get("accepted_count", -1))
    if scanned != len(messages):
        raise ValueError(
            f"Service scanned_count {scanned} does not match envelope count {len(messages)}"
        )
    if accepted < 0 or accepted > scanned:
        raise ValueError("Service accepted_count is outside the scanned range")
    cursor = str(envelope.get("cursor") or "").strip()
    if str(service_response.get("cursor_candidate") or "").strip() != cursor:
        raise ValueError("Service cursor candidate does not match the frozen envelope cursor")
    if service_response.get("cursor_committed") is not False:
        raise ValueError("Outlook message submission must not commit the cursor")
    receipt = service_response.get("service_receipt")
    if not isinstance(receipt, dict):
        raise ValueError("Service response service_receipt is required")
    receipt_id = str(receipt.get("receipt_id") or "").strip()
    receipt_sha256 = str(receipt.get("receipt_sha256") or "").strip()
    if not receipt_id or not receipt_sha256:
        raise ValueError(
            "Service response service_receipt requires receipt_id and receipt_sha256"
        )
    return {
        "source": str(envelope.get("source") or "outlook"),
        "completed_at": str(envelope.get("completed_at") or cursor),
        "scanned_count": scanned,
        "accepted_count": accepted,
        "cursor": cursor,
        "service_receipt": {
            "receipt_id": receipt_id,
            "receipt_sha256": receipt_sha256,
        },
    }
