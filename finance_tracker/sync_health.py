"""Derive live feed health from configured scheduled check deadlines."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_INGESTION_CONFIG = (
    Path(__file__).resolve().parent.parent / "config" / "ingestion.json"
)
_DAILY_CADENCE = "DAILY_MORNING_PER_ACTIVE_BANK"
_TIME_PATTERN = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


def _health(
    *,
    status: str,
    last_successful_check_at: object,
    grace_minutes: int,
    due_at: datetime | None = None,
    next_check_at: datetime | None = None,
    timezone_name: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    due = due_at.isoformat() if due_at is not None else None
    next_check = next_check_at.isoformat() if next_check_at is not None else None
    result: dict[str, Any] = {
        "is_stale": status != "CURRENT",
        "check_status": status,
        "status": status,
        "last_successful_check_at": last_successful_check_at,
        "expected_due_at": due,
        "next_scheduled_check_at": next_check,
        # Short aliases make the schedule contract convenient for API clients
        # while the expected_* names remain the persisted dashboard contract.
        "due_at": due,
        "next_check_at": next_check,
        "freshness_basis": "SCHEDULE",
        "check_grace_minutes": grace_minutes,
        "check_timezone": timezone_name,
    }
    if error:
        result["schedule_error"] = error
    return result


def _load_schedule(config_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read ingestion schedule: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("ingestion config must be an object")
    outlook = payload.get("outlook")
    if outlook is None:
        raise LookupError("ingestion config requires outlook")
    if not isinstance(outlook, dict):
        raise ValueError("ingestion config outlook must be an object")
    schedule = outlook.get("schedule")
    if not isinstance(schedule, dict):
        raise LookupError("live acquisition schedule is not configured")
    return outlook, schedule


def _parse_clock_times(slot: dict[str, Any]) -> list[time]:
    values = slot.get("times")
    if not isinstance(values, list) or not values:
        raise ValueError("live acquisition schedule requires at least one time")
    clocks: list[time] = []
    for value in values:
        if not isinstance(value, str) or _TIME_PATTERN.fullmatch(value) is None:
            raise ValueError("live acquisition times must use HH:MM")
        clocks.append(time.fromisoformat(value))
    return sorted(set(clocks))


def _parse_last_check(value: object) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("last successful check must include a timezone")
    return parsed.astimezone(UTC)


def scheduled_sync_health(
    last_successful_check_at: str | None,
    *,
    now: datetime,
    grace_minutes: int,
    ingest_source: str | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Evaluate a successful receipt against the active daily schedule.

    ``grace_minutes`` is measured after the scheduled deadline; it is not a
    rolling age limit. A successful zero-event receipt is therefore as healthy
    as any other successful receipt. Schedule/configuration errors fail closed
    with a stable status rather than claiming a healthy feed.
    """
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if isinstance(grace_minutes, bool) or grace_minutes <= 0:
        raise ValueError("grace_minutes must be positive")

    raw_last = last_successful_check_at
    try:
        outlook, schedule = _load_schedule(config_path or DEFAULT_INGESTION_CONFIG)
    except LookupError as exc:
        return _health(
            status="SCHEDULE_UNCONFIGURED",
            last_successful_check_at=raw_last,
            grace_minutes=grace_minutes,
            error=str(exc),
        )
    except ValueError as exc:
        return _health(
            status="SCHEDULE_ERROR",
            last_successful_check_at=raw_last,
            grace_minutes=grace_minutes,
            error=str(exc),
        )

    cadence = schedule.get("cadence")
    if cadence is None or not str(cadence).strip():
        return _health(
            status="SCHEDULE_UNCONFIGURED",
            last_successful_check_at=raw_last,
            grace_minutes=grace_minutes,
            error="live acquisition schedule cadence is not configured",
        )
    if cadence != _DAILY_CADENCE:
        return _health(
            status="SCHEDULE_ERROR",
            last_successful_check_at=raw_last,
            grace_minutes=grace_minutes,
            error="unsupported live acquisition schedule cadence",
        )
    timezone_name = str(schedule.get("timezone") or "").strip()
    if not timezone_name:
        return _health(
            status="SCHEDULE_UNCONFIGURED",
            last_successful_check_at=raw_last,
            grace_minutes=grace_minutes,
            error="live acquisition schedule timezone is not configured",
        )
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return _health(
            status="SCHEDULE_ERROR",
            last_successful_check_at=raw_last,
            grace_minutes=grace_minutes,
            timezone_name=timezone_name,
            error="live acquisition schedule timezone is invalid",
        )

    slots = schedule.get("active_bank_slots")
    if slots is None:
        slots = []
    if not isinstance(slots, list) or any(not isinstance(slot, dict) for slot in slots):
        return _health(
            status="SCHEDULE_ERROR",
            last_successful_check_at=raw_last,
            grace_minutes=grace_minutes,
            timezone_name=timezone_name,
            error="active bank slots must be objects",
        )
    source = str(ingest_source or "").strip()
    selected = [
        slot for slot in slots if str(slot.get("source") or "").strip() == source
    ]
    # A source-less or legacy ``outlook`` request may use the only active bank.
    cursor_source = str(outlook.get("cursor_source") or "").strip()
    if (
        not selected
        and (ingest_source is None or source == cursor_source)
        and len(slots) == 1
    ):
        selected = slots
    if not selected or len(selected) != 1:
        return _health(
            status="SCHEDULE_UNCONFIGURED",
            last_successful_check_at=raw_last,
            grace_minutes=grace_minutes,
            timezone_name=timezone_name,
            error="no unambiguous active bank schedule is configured for this source",
        )
    try:
        clocks = _parse_clock_times(selected[0])
    except ValueError as exc:
        return _health(
            status="SCHEDULE_ERROR",
            last_successful_check_at=raw_last,
            grace_minutes=grace_minutes,
            timezone_name=timezone_name,
            error=str(exc),
        )

    now_utc = now.astimezone(UTC)
    cutoff = now_utc - timedelta(minutes=grace_minutes)
    cutoff_day = cutoff.astimezone(zone).date()
    due_candidates = [
        datetime.combine(cutoff_day - timedelta(days=offset), clock, zone).astimezone(
            UTC
        )
        for offset in (0, 1)
        for clock in clocks
    ]
    due_at = max(candidate for candidate in due_candidates if candidate <= cutoff)
    local_day = now_utc.astimezone(zone).date()
    next_candidates = [
        datetime.combine(local_day + timedelta(days=offset), clock, zone).astimezone(
            UTC
        )
        for offset in (0, 1)
        for clock in clocks
    ]
    next_check_at = min(
        candidate for candidate in next_candidates if candidate > now_utc
    )

    try:
        last_check = _parse_last_check(raw_last)
    except (TypeError, ValueError):
        return _health(
            status="INVALID_CHECK_TIMESTAMP",
            last_successful_check_at=raw_last,
            grace_minutes=grace_minutes,
            due_at=due_at,
            next_check_at=next_check_at,
            timezone_name=timezone_name,
            error="last successful check timestamp is invalid",
        )
    if last_check is None:
        status = "NEVER_CHECKED"
    elif last_check > now_utc:
        status = "INVALID_CHECK_TIMESTAMP"
    elif due_at <= last_check:
        status = "CURRENT"
    else:
        status = "OVERDUE"
    return _health(
        status=status,
        last_successful_check_at=raw_last,
        grace_minutes=grace_minutes,
        due_at=due_at,
        next_check_at=next_check_at,
        timezone_name=timezone_name,
    )


__all__ = ["scheduled_sync_health"]
