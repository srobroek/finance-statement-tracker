"""Evaluate successful scan receipts against the configured acquisition schedule."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_INGESTION_CONFIG = Path(__file__).resolve().parent.parent / "config" / "ingestion.json"


def scheduled_sync_health(
    last_successful_check_at: str | None,
    *,
    now: datetime,
    grace_minutes: int,
    ingest_source: str | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """An empty successful scan satisfies a due check just like a nonempty one.

    The latest run whose scheduled instant is at or before ``now - grace``
    must have a successful receipt. Time since the last transaction is irrelevant.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if grace_minutes <= 0:
        raise ValueError("grace_minutes must be positive")
    config = json.loads((config_path or DEFAULT_INGESTION_CONFIG).read_text(encoding="utf-8"))
    outlook = config.get("outlook") or {}
    schedule = outlook.get("schedule") or {}
    if schedule.get("cadence") != "DAILY_MORNING_PER_ACTIVE_BANK":
        raise ValueError("Unsupported live acquisition schedule cadence")
    zone = ZoneInfo(str(schedule.get("timezone") or ""))
    slots = schedule.get("active_bank_slots") or []
    selected = [slot for slot in slots if slot.get("source") == ingest_source]
    # The aggregate/unspecified source is unambiguous only with one active bank.
    if not selected and ingest_source in (None, outlook.get("cursor_source")) and len(slots) == 1:
        selected = slots
    if len(selected) != 1:
        return {
            "is_stale": True, "check_status": "SCHEDULE_UNCONFIGURED",
            "last_successful_check_at": last_successful_check_at,
            "expected_due_at": None, "next_scheduled_check_at": None,
            "freshness_basis": "SCHEDULE", "check_grace_minutes": grace_minutes,
        }
    clock_times = []
    for value in selected[0].get("times") or []:
        if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("Live acquisition times must use HH:MM")
        clock_times.append(time.fromisoformat(value))
    if not clock_times:
        raise ValueError("Live acquisition schedule requires a time")
    now = now.astimezone(UTC)
    cutoff = now - timedelta(minutes=grace_minutes)
    cutoff_day = cutoff.astimezone(zone).date()
    due_candidates = [
        datetime.combine(cutoff_day - timedelta(days=offset), clock, zone).astimezone(UTC)
        for offset in (0, 1) for clock in clock_times
    ]
    due = max(value for value in due_candidates if value <= cutoff)
    local_day = now.astimezone(zone).date()
    upcoming = [
        datetime.combine(local_day + timedelta(days=offset), clock, zone).astimezone(UTC)
        for offset in (0, 1) for clock in clock_times
    ]
    next_check = min(value for value in upcoming if value > now)
    status = "NEVER_CHECKED"
    if last_successful_check_at:
        try:
            last_check = datetime.fromisoformat(last_successful_check_at.replace("Z", "+00:00"))
            if last_check.tzinfo is None:
                last_check = last_check.replace(tzinfo=UTC)
            last_check = last_check.astimezone(UTC)
            status = "CURRENT" if due <= last_check <= now else "OVERDUE"
            if last_check > now:
                status = "INVALID_CHECK_TIMESTAMP"
        except ValueError:
            status = "INVALID_CHECK_TIMESTAMP"
    return {
        "is_stale": status != "CURRENT",
        "check_status": status,
        "last_successful_check_at": last_successful_check_at,
        "expected_due_at": due.isoformat(),
        "next_scheduled_check_at": next_check.isoformat(),
        "freshness_basis": "SCHEDULE",
        "check_grace_minutes": grace_minutes,
        "check_timezone": str(zone),
    }
