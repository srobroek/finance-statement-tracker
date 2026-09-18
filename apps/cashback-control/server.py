"""Serve and update the live cashback routing dashboard."""

from __future__ import annotations

import hmac
import json
import os
import signal
import sys
import threading
import time
from datetime import UTC, date, datetime, timedelta
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from typing import cast

from access_auth import (
    ACCESS_ASSERTION_HEADER,
    AccessVerificationError,
    build_access_verifier,
    local_access_exemption,
)

APP_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = APP_ROOT.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from finance_tracker.actual_pipeline import (
    account_maps,
    load_actual_config,
    load_compiled_rules,
)
from finance_tracker.actual_snapshot import eligible_card_codes
from finance_tracker.models import CardMembership
from finance_tracker.cashback import (
    load_program_configuration,
    programs_from_config,
    statement_period,
)
from finance_tracker.cashback_events import (
    CashbackEventStore,
    IngestCursorConflict,
    _iso_datetime,
    _json_digest,
    build_live_dashboard,
    write_dashboard,
)
from finance_tracker.statement_cycles import STATEMENT_RECEIPT_CONTRACT
from finance_tracker.notification_sources import (
    load_notification_sources,
    validate_notification_adapter_coverage,
)
from finance_tracker.notifications import (
    DEFAULT_NOTIFICATION_ADAPTERS,
    parse_outlook_notifications,
)
from finance_tracker.web_push import WebPushDispatcher, WebPushStore
from finance_tracker.sync_health import scheduled_sync_health

WEB_ROOT = APP_ROOT / "web"
CASHBACK_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; base-uri 'none'; connect-src 'self'; form-action 'self'; "
    "frame-ancestors 'none'; img-src 'self'; object-src 'none'; script-src 'self'; "
    "style-src 'self' 'unsafe-inline'"
)
DASHBOARD_PATH = Path(
    os.environ.get(
        "CASHBACK_DASHBOARD_PATH",
        str(REPOSITORY_ROOT / "runtime" / "cashback-dashboard.json"),
    )
).resolve()
DATABASE_PATH = Path(
    os.environ.get(
        "CASHBACK_DB_PATH",
        str(REPOSITORY_ROOT / "runtime" / "cashback-events.sqlite3"),
    )
).resolve()
INGEST_TOKEN = os.environ.get("CASHBACK_INGEST_TOKEN", "")
PUBLIC_URL = os.environ.get("CASHBACK_PUBLIC_URL", "").strip()
PUBLIC_ORIGIN = urlsplit(PUBLIC_URL)
BIND_HOST = os.environ.get("CASHBACK_HOST", "127.0.0.1").strip()
ACCESS_VERIFIER = build_access_verifier(bind_host=BIND_HOST, public_url=PUBLIC_URL)
WRITE_LOCK = threading.Lock()
PUSH_LOCK = threading.Lock()
STALE_AFTER_MINUTES = int(os.environ.get("CASHBACK_STALE_AFTER_MINUTES", "90"))
REFRESH_SECONDS = max(0, int(os.environ.get("CASHBACK_REFRESH_SECONDS", "60")))
INGEST_SOURCE = os.environ.get("CASHBACK_INGEST_SOURCE", "outlook").strip()
if not INGEST_SOURCE:
    raise ValueError("CASHBACK_INGEST_SOURCE must not be empty")
CHECK_SCHEDULE_CONFIG_PATH = Path(
    os.environ.get(
        "CASHBACK_INGESTION_CONFIG_PATH",
        str(REPOSITORY_ROOT / "config" / "ingestion.json"),
    )
).resolve()
PROGRAM_CONFIG_PATH = Path(
    os.environ.get(
        "CASHBACK_PROGRAM_CONFIG_PATH",
        str(REPOSITORY_ROOT / "config" / "cashback-programs.json"),
    )
).resolve()
ACTUAL_CONFIG_PATH = Path(
    os.environ.get(
        "ACTUAL_BOOTSTRAP_CONFIG_PATH",
        str(REPOSITORY_ROOT / "config" / "actual-bootstrap.json"),
    )
).resolve()
STATIC_RULES_PATH = Path(
    os.environ.get(
        "STATIC_RULES_CONFIG_PATH",
        str(REPOSITORY_ROOT / "config" / "static-rules.seed.json"),
    )
).resolve()
NOTIFICATION_SOURCES_PATH = Path(
    os.environ.get(
        "TRANSACTION_EMAIL_SOURCES_PATH",
        str(REPOSITORY_ROOT / "config" / "transaction-email-sources.json"),
    )
).resolve()
MEMBERSHIP_STATE_PATH = Path(
    os.environ.get(
        "CASHBACK_MEMBERSHIP_STATE_PATH",
        str(REPOSITORY_ROOT / "runtime" / "cashback-state.json"),
    )
).resolve()
STORE = CashbackEventStore(
    DATABASE_PATH,
    program_config_path=PROGRAM_CONFIG_PATH,
)
PUSH_STORE = WebPushStore(DATABASE_PATH)
PUSH_DISPATCHER = WebPushDispatcher(
    PUSH_STORE,
    public_key=os.environ.get("CASHBACK_VAPID_PUBLIC_KEY", ""),
    private_key=os.environ.get("CASHBACK_VAPID_PRIVATE_KEY", ""),
    subject=os.environ.get("CASHBACK_VAPID_SUBJECT", ""),
    public_url=os.environ.get("CASHBACK_PUBLIC_URL", ""),
)


def _load_memberships(path: Path | None = None) -> tuple[dict[str, object], ...]:
    state_path = MEMBERSHIP_STATE_PATH if path is None else path
    if not state_path.exists():
        return ()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Cashback membership state is unreadable") from error
    if not isinstance(state, dict) or not isinstance(state.get("memberships"), list):
        raise ValueError("Cashback membership state must contain a memberships list")
    memberships: list[dict[str, object]] = []
    for row in state["memberships"]:
        if not isinstance(row, dict):
            raise ValueError("Cashback membership entries must be objects")
        memberships.append(CardMembership(**row).to_dict())
    return tuple(memberships)


def _fx_replay_for_messages(
    messages: list[dict[str, object]],
) -> dict[str, list[dict[str, object]]]:
    """Load persisted FX traces before any provider can be consulted."""
    message_ids = {
        str(message.get("id") or "").strip()
        for message in messages
        if str(message.get("id") or "").strip()
    }
    persisted = STORE.fx_replay_for_source_event_ids(
        f"{message_id}:0" for message_id in message_ids
    )
    return {
        source_event_id.removesuffix(":0"): traces
        for source_event_id, traces in persisted.items()
    }


def parse_outlook_batch(source: dict[str, object]) -> dict[str, object]:
    source_name = str(source.get("source") or "outlook").strip()
    completed_at = _iso_datetime(source.get("completed_at"))
    cursor = str(source.get("cursor") or "").strip()
    if not cursor:
        raise ValueError("cursor is required")
    messages = source.get("messages")
    if not isinstance(messages, list) or any(
        not isinstance(message, dict) for message in messages
    ):
        raise ValueError("messages must be a list of Outlook message objects")

    actual_config = load_actual_config(ACTUAL_CONFIG_PATH)
    card_by_last4, _ = account_maps(actual_config)
    all_rules = load_compiled_rules(STATIC_RULES_PATH)
    cashback_config = load_program_configuration(PROGRAM_CONFIG_PATH)
    live_config = cashback_config.get("live_ingestion") or {}
    live_rule_set = str(live_config.get("rule_set") or "").strip().upper()
    if not live_rule_set:
        raise ValueError("Cashback configuration requires live_ingestion.rule_set")
    rules = [rule for rule in all_rules if live_rule_set in rule.rule_sets]
    if not rules:
        raise ValueError(f"No canonical rules belong to rule_set {live_rule_set}")
    notification_sources = load_notification_sources(NOTIFICATION_SOURCES_PATH)
    validate_notification_adapter_coverage(
        notification_sources,
        (adapter.code for adapter in DEFAULT_NOTIFICATION_ADAPTERS),
    )
    fx_replay = _fx_replay_for_messages(messages)
    enabled_adapter_codes = {
        source.adapter
        for source in notification_sources
        if source.active and source.adapter
    }
    enabled_adapters = tuple(
        adapter
        for adapter in DEFAULT_NOTIFICATION_ADAPTERS
        if adapter.code in enabled_adapter_codes
    )
    batch = parse_outlook_notifications(
        messages,
        card_by_last4,
        rules,
        adapters=enabled_adapters,
        cashback_config=cashback_config,
        fx_replay=fx_replay,
    )
    persistence = (
        STORE.upsert(list(batch.events))
        if batch.events
        else {"inserted": 0, "updated": 0, "event_count": 0}
    )
    service_receipt = STORE.create_ingest_receipt(
        {
            "source": source_name,
            "completed_at": completed_at,
            "scanned_count": batch.scanned_count,
            "accepted_count": batch.accepted_count,
            "cursor": cursor,
        },
        event_ids=(event["source_event_id"] for event in batch.events),
        event_digests=(_json_digest(event) for event in batch.events),
    )
    return {
        "parse": batch.to_dict(),
        "persistence": persistence,
        "cursor_candidate": cursor,
        "cursor_committed": False,
        "service_receipt": service_receipt,
    }


def rebuild_dashboard() -> dict[str, object]:
    memberships = _load_memberships()
    with WRITE_LOCK:
        payload = build_live_dashboard(
            STORE,
            date.today(),
            stale_after_minutes=STALE_AFTER_MINUTES,
            program_config_path=PROGRAM_CONFIG_PATH,
            memberships=memberships,
            ingest_source=INGEST_SOURCE,
            check_schedule_config_path=CHECK_SCHEDULE_CONFIG_PATH,
        )
        write_dashboard(DASHBOARD_PATH, payload)
    with PUSH_LOCK:
        PUSH_DISPATCHER.evaluate(payload)
    return payload


def refresh_dashboard_periodically(stop_event: threading.Event) -> None:
    while not stop_event.wait(REFRESH_SECONDS):
        try:
            rebuild_dashboard()
        except Exception as error:  # service boundary; the next interval retries
            print(
                json.dumps(
                    {
                        "timestamp": datetime_now(),
                        "level": "error",
                        "event": "dashboard_refresh_failed",
                        "error": f"{type(error).__name__}: {error}"[:500],
                    }
                ),
                flush=True,
            )


def _reporting_date(
    value: object,
    *,
    period_end: bool = False,
    field_name: str = "date",
) -> date:
    """Parse a date or aware UTC period boundary into its reporting date."""
    if isinstance(value, datetime):
        parsed_datetime = value
    elif isinstance(value, date):
        return value
    else:
        raw = str(value) if value is not None else ""
        is_date_only = (
            len(raw) == 10
            and raw[4] == "-"
            and raw[7] == "-"
            and raw[:4].isdigit()
            and raw[5:7].isdigit()
            and raw[8:].isdigit()
        )
        if is_date_only:
            try:
                return date.fromisoformat(raw)
            except ValueError as error:
                raise ValueError(
                    f"{field_name} must be YYYY-MM-DD or an aware UTC ISO datetime"
                ) from error
        try:
            parsed_datetime = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"{field_name} must be YYYY-MM-DD or an aware UTC ISO datetime"
            ) from error

    if parsed_datetime.tzinfo is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    if parsed_datetime.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must use a UTC offset")
    parsed = parsed_datetime.date()
    if not period_end or parsed_datetime.time() != datetime.min.time():
        return parsed
    return parsed - timedelta(days=1)


def _period_datetime(
    value: object,
    *,
    period_end: bool = False,
    field_name: str = "date",
) -> datetime:
    """Parse one UTC bound while retaining its exact half-open instant."""
    if isinstance(value, datetime):
        parsed_datetime = value
        is_date_only = False
    elif isinstance(value, date):
        parsed_datetime = datetime.combine(value, datetime.min.time(), tzinfo=UTC)
        is_date_only = True
    else:
        raw = str(value) if value is not None else ""
        is_date_only = (
            len(raw) == 10
            and raw[4] == "-"
            and raw[7] == "-"
            and raw[:4].isdigit()
            and raw[5:7].isdigit()
            and raw[8:].isdigit()
        )
        if is_date_only:
            try:
                parsed_datetime = datetime.combine(
                    date.fromisoformat(raw), datetime.min.time(), tzinfo=UTC
                )
            except ValueError as error:
                raise ValueError(
                    f"{field_name} must be YYYY-MM-DD or an aware UTC ISO datetime"
                ) from error
        else:
            try:
                parsed_datetime = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"{field_name} must be YYYY-MM-DD or an aware UTC ISO datetime"
                ) from error
    if parsed_datetime.tzinfo is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    if parsed_datetime.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must use a UTC offset")
    parsed_datetime = parsed_datetime.astimezone(UTC)
    if period_end and is_date_only:
        parsed_datetime += timedelta(days=1)
    return parsed_datetime


def _stored_period_bounds(
    row: dict[str, object],
) -> tuple[datetime, datetime, date, date]:
    period_start = _period_datetime(row["period_start"], field_name="period_start")
    period_end = _period_datetime(
        row["period_end"], period_end=True, field_name="period_end"
    )
    if period_end <= period_start:
        raise ValueError("period_end must be after period_start")
    return (
        period_start,
        period_end,
        period_start.date(),
        (period_end - timedelta(microseconds=1)).date(),
    )


class _HistoricalPeriodStore:
    def __init__(
        self,
        source: CashbackEventStore,
        selected_period_ids: set[str],
        derived_periods: tuple[dict[str, object], ...] = (),
    ) -> None:
        self.source = source
        self.selected_period_ids = selected_period_ids
        self.derived_periods = {
            str(row.get("period_id") or ""): dict(row)
            for row in derived_periods
            if str(row.get("period_id") or "")
        }

    def period_rows(self) -> list[dict[str, object]]:
        source_rows = [dict(candidate) for candidate in self.source.period_rows()]
        existing_ids = {str(row.get("period_id") or "") for row in source_rows}
        rows = source_rows + [
            dict(row)
            for period_id, row in self.derived_periods.items()
            if period_id not in existing_ids
        ]
        selected_cards = {
            str(row.get("card_code") or "").strip().upper()
            for row in rows
            if str(row.get("period_id") or "") in self.selected_period_ids
        }
        for row in rows:
            period_id = str(row.get("period_id") or "")
            card_code = str(row.get("card_code") or "").strip().upper()
            if period_id not in self.selected_period_ids:
                if (
                    card_code in selected_cards
                    and str(row.get("status") or "").strip().upper() == "OPEN"
                ):
                    row["status"] = "HISTORICAL"
                continue
            row["status"] = "OPEN"
            raw_end = str(row.get("period_end") or "")
            if len(raw_end) == 10:
                end = _reporting_date(raw_end) + timedelta(days=1)
                row["period_end"] = (
                    datetime.combine(end, datetime.min.time(), tzinfo=UTC)
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z")
                )
        return rows

    def rows_for_period(
        self,
        period_id: str,
        *,
        card_code: str | None = None,
    ) -> list[dict[str, object]]:
        requested_period_id = str(period_id or "").strip()
        derived = self.derived_periods.get(requested_period_id)
        if derived is None:
            return self.source.rows_for_period(
                requested_period_id,
                card_code=card_code,
            )
        selected_card = str(card_code or derived.get("card_code") or "").strip().upper()
        if selected_card != str(derived.get("card_code") or "").strip().upper():
            return []
        try:
            period_start = derived.get("_period_start_exact")
            if not isinstance(period_start, datetime):
                period_start = _period_datetime(derived["period_start"])
            period_end = derived.get("_period_end_exact")
            if not isinstance(period_end, datetime):
                period_end = _period_datetime(derived["period_end"], period_end=True)
            if period_end <= period_start:
                return []
            records = self.source.rows(period_start.date(), period_end.date())
        except (KeyError, TypeError, ValueError):
            return []
        rows: list[dict[str, object]] = []
        for row in records:
            if str(row.get("card_code") or "").strip().upper() != selected_card:
                continue
            try:
                occurred_at = _period_datetime(
                    row["occurred_at"], field_name="occurred_at"
                )
            except (KeyError, TypeError, ValueError):
                continue
            if period_start <= occurred_at < period_end:
                rows.append(row)
        return rows

    def __getattr__(self, name: str) -> object:
        return getattr(self.source, name)


def _historical_period_ids(
    as_of: date,
    *,
    store: CashbackEventStore | None = None,
) -> set[str]:
    source = STORE if store is None else store
    selected: set[str] = set()
    for row in source.period_rows():
        if str(row.get("status") or "").upper() not in {
            "OPEN",
            "FINALIZED",
            "CLOSED",
        }:
            continue
        try:
            period_start = _reporting_date(row["period_start"])
            period_end = _reporting_date(row["period_end"], period_end=True)
        except (KeyError, ValueError):
            continue
        period_id = str(row.get("period_id") or "")
        if period_id and period_start <= as_of <= period_end:
            selected.add(period_id)
    return selected


def _available_as_of_dates(
    *,
    store: CashbackEventStore | None = None,
    config_path: Path | None = None,
    as_of: date | None = None,
    memberships: tuple[dict[str, object], ...] | None = None,
) -> list[str]:
    """Return historical dates backed by stored cashback evidence."""
    evidence_store = STORE if store is None else store
    configuration_path = PROGRAM_CONFIG_PATH if config_path is None else config_path
    today = (
        date.today() if as_of is None else _reporting_date(as_of, field_name="as_of")
    )
    effective_memberships = _load_memberships() if memberships is None else memberships
    configuration = load_program_configuration(configuration_path)
    configured_programs = programs_from_config(configuration)
    eligible_cards = eligible_card_codes(configured_programs, effective_memberships)
    dates: set[str] = set()

    def add_date(value: object, *, period_end: bool = False) -> None:
        try:
            parsed = _reporting_date(
                value,
                period_end=period_end,
                field_name="period_end" if period_end else "occurred_at",
            )
        except ValueError:
            return
        if parsed <= today:
            dates.add(parsed.isoformat())

    periods = evidence_store.period_rows()
    periods_by_id = {
        str(row.get("period_id") or ""): row
        for row in periods
        if str(row.get("period_id") or "")
    }
    for period in periods:
        if (
            str(period.get("status") or "").upper() == "FINALIZED"
            and str(period.get("card_code") or "").strip().upper() in eligible_cards
        ):
            add_date(period.get("period_end"), period_end=True)

    receipts = evidence_store.receipt_rows()
    for receipt in receipts:
        if str(receipt.get("card_code") or "").strip().upper() not in eligible_cards:
            continue
        period_end = receipt.get("period_end")
        if not period_end:
            period = periods_by_id.get(str(receipt.get("period_id") or ""))
            period_end = None if period is None else period.get("period_end")
        if period_end:
            add_date(period_end, period_end=True)

    programs_by_card = {
        program.card.upper(): program
        for program in configured_programs
        if program.card.upper() in eligible_cards
    }
    for row in evidence_store.rows(date(1970, 1, 1), today):
        card = programs_by_card.get(str(row.get("card_code") or "").upper())
        if card is None:
            continue
        try:
            occurred = date.fromisoformat(str(row.get("occurred_at") or "")[:10])
        except ValueError:
            continue
        _, period_end = statement_period(occurred, card.statement_close_day)
        add_date(period_end)
    return sorted(dates, reverse=True)


def historical_periods(
    limit: int = 24,
    memberships: tuple[dict[str, object], ...] | None = None,
    as_of: date | None = None,
) -> list[dict[str, object]]:
    effective_memberships = _load_memberships() if memberships is None else memberships
    today = (
        date.today() if as_of is None else _reporting_date(as_of, field_name="as_of")
    )
    if limit <= 0:
        return []
    configuration = load_program_configuration(PROGRAM_CONFIG_PATH)
    configured_programs = programs_from_config(configuration)
    eligible_cards = eligible_card_codes(configured_programs, effective_memberships)

    receipt_reader = getattr(STORE, "receipt_rows", None)
    receipt_rows = (
        cast(list[dict[str, object]], receipt_reader())
        if callable(receipt_reader)
        else []
    )
    receipt_period_ids = {
        str(row.get("period_id") or "")
        for row in receipt_rows
        if str(row.get("period_id") or "")
    }

    stored_by_key: dict[tuple[str, datetime, datetime], dict[str, object]] = {}
    stored_intervals_by_card: dict[
        str, list[tuple[datetime, datetime, tuple[str, datetime, datetime]]]
    ] = {}
    for candidate in STORE.period_rows():
        row = dict(candidate)
        card_code = str(row.get("card_code") or "").strip().upper()
        status = str(row.get("status") or "").strip().upper()
        period_id = str(row.get("period_id") or "").strip()
        if (
            not period_id
            or card_code not in eligible_cards
            or status not in {"OPEN", "FINALIZED", "CLOSED"}
        ):
            continue
        try:
            period_start_exact, period_end_exact, _, _ = _stored_period_bounds(row)
        except (KeyError, ValueError):
            continue
        key = (period_id, period_start_exact, period_end_exact)
        stored_by_key[key] = row
        stored_intervals_by_card.setdefault(card_code, []).append(
            (period_start_exact, period_end_exact, key)
        )

    stored_keys_with_events: set[tuple[str, datetime, datetime]] = set()
    event_periods: dict[tuple[str, date, date], list[datetime]] = {}
    event_programs: dict[tuple[str, date, date], object] = {}
    events_reader = getattr(STORE, "rows", None)
    event_rows = (
        cast(list[dict[str, object]], events_reader(date(1970, 1, 1), today))
        if callable(events_reader)
        else []
    )
    for event in event_rows:
        card_code = str(event.get("card_code") or "").strip().upper()
        try:
            occurred_at = _period_datetime(
                event["occurred_at"], field_name="occurred_at"
            )
        except (KeyError, TypeError, ValueError):
            continue
        occurred = occurred_at.date()
        try:
            historical_programs = programs_from_config(
                configuration, occurred, as_of=occurred
            )
        except ValueError:
            continue
        program = next(
            (
                candidate
                for candidate in historical_programs
                if candidate.card.upper() == card_code
            ),
            None,
        )
        if program is None or card_code not in eligible_cards:
            continue
        if occurred > today:
            continue

        matching_stored = [
            item
            for item in stored_intervals_by_card.get(card_code, [])
            if item[0] <= occurred_at < item[1]
        ]
        if matching_stored:
            _, _, matching_key = min(
                matching_stored,
                key=lambda item: (
                    (item[1] - item[0]).total_seconds(),
                    item[0],
                    item[1],
                    str(item[2][0]),
                ),
            )
            stored_keys_with_events.add(matching_key)
            continue
        effective_start = getattr(program, "effective_start", None)
        if effective_start is not None and occurred < effective_start:
            continue

        try:
            period_start, period_end = statement_period(
                occurred, program.statement_close_day
            )
        except (TypeError, ValueError):
            continue
        if period_end > today:
            continue
        event_periods.setdefault((card_code, period_start, period_end), []).append(
            occurred_at
        )
        event_programs[(card_code, period_start, period_end)] = program

    derived_periods: list[dict[str, object]] = []
    for (card_code, cycle_start, cycle_end), event_times in event_periods.items():
        cycle_start_exact = datetime.combine(
            cycle_start, datetime.min.time(), tzinfo=UTC
        )
        effective_start = getattr(
            event_programs[(card_code, cycle_start, cycle_end)],
            "effective_start",
            None,
        )
        if isinstance(effective_start, datetime):
            effective_start = effective_start.date()
        if isinstance(effective_start, date):
            cycle_start_exact = max(
                cycle_start_exact,
                datetime.combine(effective_start, datetime.min.time(), tzinfo=UTC),
            )
        cycle_end_exact = datetime.combine(
            cycle_end + timedelta(days=1), datetime.min.time(), tzinfo=UTC
        )
        uncovered = [(cycle_start_exact, cycle_end_exact)]
        for stored_start, stored_end, _ in stored_intervals_by_card.get(card_code, []):
            remaining: list[tuple[datetime, datetime]] = []
            for segment_start, segment_end in uncovered:
                if stored_end <= segment_start or stored_start >= segment_end:
                    remaining.append((segment_start, segment_end))
                    continue
                if segment_start < stored_start:
                    remaining.append((segment_start, min(stored_start, segment_end)))
                if stored_end < segment_end:
                    remaining.append((max(stored_end, segment_start), segment_end))
            uncovered = remaining
        for period_start_exact, period_end_exact in uncovered:
            if not any(
                period_start_exact <= occurred_at < period_end_exact
                for occurred_at in event_times
            ):
                continue
            period_start = period_start_exact.date()
            period_end = (period_end_exact - timedelta(microseconds=1)).date()
            derived_periods.append(
                {
                    "period_id": (
                        f"cashback-derived:{card_code}:"
                        f"{period_start.isoformat()}:{period_end.isoformat()}"
                    ),
                    "card_code": card_code,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "_period_start_exact": period_start_exact,
                    "_period_end_exact": period_end_exact,
                    "status": "OPEN",
                    "reconciliation_status": "UNMATCHED",
                    "statement_reference": None,
                    "finalized_at": None,
                }
            )

    derived_period_ids = {str(row["period_id"]) for row in derived_periods}
    candidates: list[tuple[dict[str, object], date, date]] = []
    for key, row in stored_by_key.items():
        status = str(row.get("status") or "").strip().upper()
        period_start = key[1].date()
        period_end = (key[2] - timedelta(microseconds=1)).date()
        if period_end <= today and (
            status == "FINALIZED"
            or key in stored_keys_with_events
            or str(row.get("period_id") or "") in receipt_period_ids
        ):
            candidates.append((row, period_start, period_end))
    candidates.extend(
        (
            row,
            _reporting_date(row["period_start"]),
            _reporting_date(row["period_end"]),
        )
        for row in derived_periods
    )
    candidates.sort(
        key=lambda item: (
            item[2],
            str(item[0].get("card_code") or ""),
            item[1],
            str(item[0].get("period_id") or ""),
        ),
        reverse=True,
    )

    result = []
    for period, period_start, period_end in candidates[:limit]:
        period_id = str(period.get("period_id") or "")
        selected_derived = (period,) if period_id in derived_period_ids else ()
        snapshot = build_live_dashboard(
            cast(
                CashbackEventStore,
                _HistoricalPeriodStore(
                    STORE,
                    {period_id},
                    derived_periods=selected_derived,
                ),
            ),
            period_end,
            stale_after_minutes=STALE_AFTER_MINUTES,
            program_config_path=PROGRAM_CONFIG_PATH,
            memberships=effective_memberships,
        )
        card = next(
            (
                item
                for item in snapshot["cards"]
                if item["card"] == period.get("card_code")
            ),
            None,
        )
        if card is None:
            continue
        result.append(
            {
                "card": period.get("card_code"),
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "status": str(period.get("status") or "OPEN").upper(),
                "reconciliation_status": str(
                    period.get("reconciliation_status") or "UNMATCHED"
                ).upper(),
                "statement_reference": period.get("statement_reference"),
                "finalized_at": period.get("finalized_at"),
                "summary": card,
            }
        )
    return result


def _eligible_statement_receipts() -> list[dict[str, object]]:
    configuration = load_program_configuration(PROGRAM_CONFIG_PATH)
    programs = programs_from_config(configuration)
    eligible_cards = eligible_card_codes(programs, _load_memberships())
    return cast(
        list[dict[str, object]],
        STORE.statement_receipts(limit=200, included_cards=eligible_cards),
    )


def _bank_receipt_historical_store(
    receipt: dict[str, object],
    *,
    card_code: str,
) -> _HistoricalPeriodStore:
    """Select an exact stored period or a bounded read-only receipt interval."""
    period_start = _period_datetime(
        receipt["period_start"],
        field_name="period_start",
    )
    period_end = _period_datetime(
        receipt["period_end"],
        period_end=True,
        field_name="period_end",
    )
    exact_period_ids: set[str] = set()
    for candidate in STORE.period_rows():
        row = dict(candidate)
        if str(row.get("card_code") or "").strip().upper() != card_code.upper():
            continue
        period_id = str(row.get("period_id") or "").strip()
        if not period_id:
            continue
        try:
            stored_start, stored_end, _, _ = _stored_period_bounds(row)
        except (KeyError, TypeError, ValueError):
            continue
        if stored_start == period_start and stored_end == period_end:
            exact_period_ids.add(period_id)
    if exact_period_ids:
        return _HistoricalPeriodStore(STORE, exact_period_ids)

    virtual_id = (
        f"cashback-bank-receipt:{card_code.upper()}:"
        f"{period_start.date().isoformat()}:"
        f"{(period_end - timedelta(days=1)).date().isoformat()}"
    )
    virtual_period = {
        "period_id": virtual_id,
        "card_code": card_code.upper(),
        "period_start": period_start.isoformat().replace("+00:00", "Z"),
        "period_end": period_end.isoformat().replace("+00:00", "Z"),
        "_period_start_exact": period_start,
        "_period_end_exact": period_end,
        "status": "OPEN",
        "reconciliation_status": "UNMATCHED",
        "statement_reference": receipt.get("statement_reference"),
        "finalized_at": None,
    }
    return _HistoricalPeriodStore(
        STORE,
        {virtual_id},
        derived_periods=(virtual_period,),
    )


def _previous_statement_cycles(
    as_of: date | None = None,
) -> list[dict[str, object]]:
    """Return each eligible card's previous cycle from stored receipt evidence."""
    selected_as_of = (
        date.today() if as_of is None else _reporting_date(as_of, field_name="as_of")
    )
    configuration = load_program_configuration(
        PROGRAM_CONFIG_PATH, as_of=selected_as_of
    )
    programs = programs_from_config(
        configuration,
        selected_as_of,
        as_of=selected_as_of,
    )
    memberships = _load_memberships()
    eligible_cards = eligible_card_codes(programs, memberships)
    result: list[dict[str, object]] = []
    for program in programs:
        if program.card.upper() not in eligible_cards:
            continue
        current_start, _ = statement_period(selected_as_of, program.statement_close_day)
        expected_start, expected_end = statement_period(
            current_start - timedelta(days=1),
            program.statement_close_day,
        )
        receipts = STORE.statement_receipts(
            card_code=program.card,
            included_cards=eligible_cards,
            limit=200,
        )
        dated_receipts = [
            candidate
            for candidate in receipts
            if candidate.get("period_start")
            and candidate.get("period_end")
            and str(candidate["period_start"]) < current_start.isoformat()
            and str(candidate["period_end"]) <= selected_as_of.isoformat()
        ]
        receipt = (
            max(
                dated_receipts,
                key=lambda candidate: (
                    str(candidate["period_end"]),
                    str(candidate["period_start"]),
                    str(candidate.get("received_at") or ""),
                ),
            )
            if dated_receipts
            else next(
                (
                    candidate
                    for candidate in receipts
                    if not candidate.get("period_start")
                    and not candidate.get("period_end")
                ),
                None,
            )
        )
        known_bounds = bool(
            receipt and receipt.get("period_start") and receipt.get("period_end")
        )
        if known_bounds:
            assert receipt is not None
            calculation_end = date.fromisoformat(str(receipt["period_end"]))
        else:
            calculation_end = expected_end
        summary: dict[str, object] | None = None
        if receipt is not None and known_bounds:
            try:
                assert receipt is not None
                historical_store = _bank_receipt_historical_store(
                    receipt,
                    card_code=program.card,
                )
                snapshot = build_live_dashboard(
                    cast(CashbackEventStore, historical_store),
                    calculation_end,
                    stale_after_minutes=STALE_AFTER_MINUTES,
                    program_config_path=PROGRAM_CONFIG_PATH,
                    memberships=memberships,
                )
                summary = next(
                    (
                        card
                        for card in snapshot["cards"]
                        if card["card"] == program.card
                    ),
                    None,
                )
            except (KeyError, ValueError):
                summary = None
        result.append(
            {
                "card": program.card,
                "period_start": (receipt.get("period_start") if known_bounds else None)
                if receipt
                else None,
                "period_end": (receipt.get("period_end") if known_bounds else None)
                if receipt
                else None,
                "expected_period_start": expected_start.isoformat(),
                "expected_period_end": expected_end.isoformat(),
                "status": receipt["status"] if receipt else "NO_RECEIPT",
                "bank_state": receipt["bank_state"] if receipt else "NOT_RECEIVED",
                "processing_state": (receipt["processing_state"] if receipt else None),
                "reconciliation_state": (
                    receipt["reconciliation_state"] if receipt else None
                ),
                "reconciliation_status": (
                    receipt["reconciliation_state"] if receipt else None
                ),
                "bounds_state": (receipt["bounds_state"] if receipt else "UNKNOWN"),
                "statement_reference": (
                    receipt["statement_reference"] if receipt else None
                ),
                "finalized_at": None,
                "settlement_state": (receipt["settlement_state"] if receipt else None),
                "statement_receipt": receipt,
                "snapshot_as_of": (
                    calculation_end.isoformat() if summary is not None else None
                ),
                "summary": summary,
            }
        )
    return result


class CashbackHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def end_headers(self) -> None:
        if not urlsplit(self.path).path.startswith("/api/"):
            self.send_header(
                "Content-Security-Policy", CASHBACK_CONTENT_SECURITY_POLICY
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            # The dashboard is a small operational UI. Revalidate its static
            # shell on every visit so a container rollout cannot leave a
            # device running stale routing code from its browser cache.
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        request = urlsplit(self.path)
        path = request.path
        if path in {
            "/api/dashboard",
            "/api/periods",
            "/api/periods/previous",
            "/api/statement-receipts",
            "/api/health",
            "/api/push/config",
        } and not self._authorize_operational_read(
            allow_ingest_token=path == "/api/health"
        ):
            self._json(
                HTTPStatus.FORBIDDEN,
                {"error": "Operational read authorization required"},
            )
            return

        if path == "/api/health":
            configuration = load_program_configuration(PROGRAM_CONFIG_PATH)
            eligible_cards = eligible_card_codes(
                programs_from_config(configuration),
                _load_memberships(),
            )
            event_store = STORE.stats(
                included_cards=eligible_cards,
                ingest_source=INGEST_SOURCE,
            )
            event_store.update(
                scheduled_sync_health(
                    event_store.get("last_successful_ingest_at"),
                    now=datetime.now(UTC),
                    grace_minutes=STALE_AFTER_MINUTES,
                    ingest_source=INGEST_SOURCE,
                    config_path=CHECK_SCHEDULE_CONFIG_PATH,
                )
            )
            self._json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "dashboard_available": DASHBOARD_PATH.is_file(),
                    "event_store": event_store,
                },
            )
            return

        if path == "/api/statement-receipts":
            receipts = _eligible_statement_receipts()
            self._json(
                HTTPStatus.OK,
                {
                    "statement_receipts": receipts,
                    "receipt_count": len(receipts),
                    "previous_statement_cycles": _previous_statement_cycles(),
                    "producer_contract": STATEMENT_RECEIPT_CONTRACT,
                },
            )
            return

        if path == "/api/periods/previous":
            self._json(
                HTTPStatus.OK,
                {"previous_statement_cycles": _previous_statement_cycles()},
            )
            return

        if path == "/api/periods":
            periods = historical_periods()
            receipts = _eligible_statement_receipts()
            previous = _previous_statement_cycles()
            self._json(
                HTTPStatus.OK,
                {
                    "periods": periods,
                    "period_count": len(periods),
                    "available_as_of_dates": _available_as_of_dates(),
                    "statement_receipts": receipts,
                    "statement_receipt_count": len(receipts),
                    "previous_statement_cycles": previous,
                },
            )
            return

        if path == "/api/push/config":
            self._json(HTTPStatus.OK, PUSH_DISPATCHER.config())
            return

        if path == "/api/dashboard":
            as_of_values = parse_qs(request.query, keep_blank_values=True).get(
                "as_of", []
            )
            if len(as_of_values) > 1:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "as_of must be supplied at most once"},
                )
                return
            if as_of_values:
                try:
                    selected_as_of = _reporting_date(
                        as_of_values[0], field_name="as_of"
                    )
                except ValueError as error:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                    return
                if selected_as_of > date.today():
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "as_of cannot be in the future"},
                    )
                    return
                payload = build_live_dashboard(
                    cast(
                        CashbackEventStore,
                        _HistoricalPeriodStore(
                            STORE,
                            _historical_period_ids(selected_as_of),
                        ),
                    ),
                    selected_as_of,
                    stale_after_minutes=STALE_AFTER_MINUTES,
                    program_config_path=PROGRAM_CONFIG_PATH,
                    memberships=_load_memberships(),
                    ingest_source=INGEST_SOURCE,
                    check_schedule_config_path=CHECK_SCHEDULE_CONFIG_PATH,
                )
                self._json(HTTPStatus.OK, payload)
                return
            if not DASHBOARD_PATH.is_file():
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "Dashboard snapshot has not been generated yet."},
                )
                return
            try:
                payload = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": f"Dashboard snapshot is unreadable: {error}"},
                )
                return
            self._json(HTTPStatus.OK, payload)
            return

        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        handlers = {
            "/api/events": self._post_events,
            "/api/events/validate": self._post_events_validate,
            "/api/ingest/transaction": self._post_ingest_transaction,
            "/api/ingest/receipt": self._post_ingest_receipt,
            "/api/ingest-runs": self._post_ingest_runs,
            "/api/ingest-state": self._post_ingest_state,
            "/api/reconcile": self._post_reconcile,
            "/api/corrections": self._post_corrections,
            "/api/periods/finalize": self._post_period_finalize,
            "/api/statement-receipts": self._post_statement_receipt,
            "/api/statement-receipts/state": self._post_statement_receipt_state,
            "/api/alerts/ack": self._post_alert_ack,
            "/api/outlook/messages": self._post_outlook_messages,
            "/api/push/subscriptions": self._post_push_subscription,
        }
        if path not in handlers:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        same_origin_paths = {
            "/api/alerts/ack",
            "/api/push/subscriptions",
        }
        if path in same_origin_paths:
            if not self._authorize_browser_mutation():
                self._json(
                    HTTPStatus.FORBIDDEN,
                    {"error": "Browser mutation authorization required"},
                )
                return
        elif not INGEST_TOKEN:
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "Cashback ingest token is not configured"},
            )
            return
        elif not self._authorize_ingest():
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "Invalid ingest token"})
            return
        try:
            result = handlers[path](self._read_json_body())
        except (ValueError, json.JSONDecodeError) as error:
            status = (
                HTTPStatus.CONFLICT
                if isinstance(error, IngestCursorConflict)
                else HTTPStatus.BAD_REQUEST
            )
            self._json(status, {"error": str(error)})
            return
        except (
            Exception
        ) as error:  # service boundary: keep operational details out of responses
            self._log_post_failure(path, error)
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal server error"}
            )
            return
        self._json(HTTPStatus.OK, result)

    def _read_json_body(self) -> object:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0 or length > 1_000_000:
            raise ValueError("Request body must be between 1 byte and 1 MB")
        return json.loads(self.rfile.read(length))

    def _post_push_subscription(self, source: object) -> dict[str, object]:
        if not isinstance(source, dict):
            raise ValueError("Payload must be a push subscription request")
        action = str(source.get("action") or "subscribe").strip().casefold()
        subscription = source.get("subscription")
        if not isinstance(subscription, dict):
            raise ValueError("subscription must be an object")
        if action == "unsubscribe":
            result = PUSH_STORE.remove_subscription(subscription.get("endpoint"))
            return {
                "subscription": {"removed": bool(result["removed"])},
                "push": PUSH_DISPATCHER.config(),
            }
        if action != "subscribe":
            raise ValueError("action must be subscribe or unsubscribe")
        result = PUSH_STORE.upsert_subscription(
            subscription,
            str(self.headers.get("User-Agent") or "")[:500],
        )
        delivery = PUSH_DISPATCHER.send_test(str(result["endpoint"]))
        return {
            "subscription": {"enabled": bool(result["enabled"])},
            "test_delivery": delivery,
            "push": PUSH_DISPATCHER.config(),
        }

    def _post_alert_ack(self, source: object) -> dict[str, object]:
        if not isinstance(source, dict):
            raise ValueError("Payload must be an alert acknowledgement object")
        result = STORE.set_alert_acknowledgement(
            source.get("alert_key"),
            source.get("acknowledged"),
        )
        dashboard = rebuild_dashboard()
        return {"alert": result, "event_store": dashboard["data_status"]}

    def _post_ingest_runs(self, source: object) -> dict[str, object]:
        if not isinstance(source, dict):
            raise ValueError("Payload must be an ingest run object")
        result = STORE.record_ingest_success(source)
        dashboard = rebuild_dashboard()
        return {"ingest": result, "event_store": dashboard["data_status"]}

    def _post_ingest_state(self, source: object) -> dict[str, object]:
        if not isinstance(source, dict):
            raise ValueError("Payload must be an ingest state request object")
        state = STORE.ingest_state(str(source.get("source") or "outlook"))
        return {"ingest_state": state}

    def _post_outlook_messages(self, source: object) -> dict[str, object]:
        if not isinstance(source, dict):
            raise ValueError("Payload must be an Outlook message batch object")
        result = parse_outlook_batch(source)
        dashboard = rebuild_dashboard()
        return {**result, "event_store": dashboard["data_status"]}

    def _post_reconcile(self, source: object) -> dict[str, object]:
        if not isinstance(source, dict):
            raise ValueError("Payload must be a statement reconciliation object")
        result = STORE.reconcile_statement(source)
        dashboard = rebuild_dashboard()
        return {"reconciliation": result, "event_store": dashboard["data_status"]}

    def _post_corrections(self, source: object) -> dict[str, object]:
        if not isinstance(source, dict):
            raise ValueError("Payload must be an event correction object")
        result = STORE.correct_event(source)
        dashboard = rebuild_dashboard()
        return {"correction": result, "event_store": dashboard["data_status"]}

    def _post_period_finalize(self, source: object) -> dict[str, object]:
        if not isinstance(source, dict):
            raise ValueError("Payload must be a card-period finalization object")
        result = STORE.finalize_period(source, program_config_path=PROGRAM_CONFIG_PATH)
        dashboard = rebuild_dashboard()
        return {"period": result, "event_store": dashboard["data_status"]}

    def _post_statement_receipt(self, source: object) -> dict[str, object]:
        """Record an authenticated statement email before document processing."""
        if not isinstance(source, dict):
            raise ValueError("Payload must be a statement receipt object")
        receipt = source.get("statement_receipt", source.get("receipt", source))
        if not isinstance(receipt, dict):
            raise ValueError("statement_receipt must be an object")
        with WRITE_LOCK:
            result = STORE.record_bank_statement_receipt(receipt)
        stored = result["statement_receipt"]
        return {
            **result,
            "producer_contract": STATEMENT_RECEIPT_CONTRACT,
            "statement_receipt": stored,
            "receipt": stored,
        }

    def _post_statement_receipt_state(self, source: object) -> dict[str, object]:
        if not isinstance(source, dict):
            raise ValueError("Payload must be a statement receipt state object")
        receipt_id = str(
            source.get("receipt_id") or source.get("statement_receipt_id") or ""
        ).strip()
        if not receipt_id:
            raise ValueError("receipt_id is required")
        with WRITE_LOCK:
            stored = STORE.update_statement_receipt(
                receipt_id,
                processing_state=source.get("processing_state"),
                reconciliation_state=source.get("reconciliation_state"),
                period_start=source.get("period_start"),
                period_end=source.get("period_end"),
                statement_reference=source.get("statement_reference"),
            )
        return {
            "statement_receipt": stored,
            "receipt": stored,
            "state": stored["state"],
            "bank_state": stored["bank_state"],
            "processing_state": stored["processing_state"],
            "reconciliation_state": stored["reconciliation_state"],
        }

    def _post_ingest_transaction(self, source: object) -> dict[str, object]:
        if not isinstance(source, dict):
            raise ValueError("Payload must be a transaction object")
        with WRITE_LOCK:
            result = STORE.ingest_transaction(source)
        return {
            **result,
            "event_store": rebuild_dashboard()["data_status"],
        }

    def _post_ingest_receipt(self, source: object) -> dict[str, object]:
        if not isinstance(source, dict):
            raise ValueError("Payload must be a scan receipt object")
        with WRITE_LOCK:
            return STORE.combine_transaction_receipts(source)

    def _post_events_payload(self, source: object) -> list[dict[str, object]]:
        events = source if isinstance(source, list) else [source]
        if any(not isinstance(event, dict) for event in events):
            raise ValueError(
                "Payload must be an event object or a list of event objects"
            )
        event_objects = [event for event in events if isinstance(event, dict)]
        profile_currency = (
            str(load_program_configuration(PROGRAM_CONFIG_PATH).get("currency") or "")
            .strip()
            .upper()
        )
        if not profile_currency:
            raise ValueError("Cashback profile currency is required")
        return [
            {**event, "currency": event.get("currency") or profile_currency}
            for event in event_objects
        ]

    def _post_events_validate(self, source: object) -> dict[str, object]:
        events = self._post_events_payload(source)
        STORE.validate(events)
        return {"valid": True, "event_count": len(events)}

    def _post_events(self, source: object) -> dict[str, object]:
        events = self._post_events_payload(source)
        result = STORE.upsert(events)
        dashboard = rebuild_dashboard()
        return {**result, "event_store": dashboard["data_status"]}

    def _log_post_failure(self, path: str, error: Exception) -> None:
        print(
            json.dumps(
                {
                    "timestamp": datetime_now(),
                    "level": "error",
                    "event": "http_request_failed",
                    "path": path,
                    "exception_type": type(error).__name__,
                }
            ),
            flush=True,
        )

    def _authorize_ingest(self) -> bool:
        authorization = self.headers.get("Authorization")
        if not isinstance(authorization, str):
            return False
        try:
            return hmac.compare_digest(
                authorization.encode("ascii"),
                f"Bearer {INGEST_TOKEN}".encode("ascii"),
            )
        except UnicodeEncodeError:
            return False

    def _json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _header_matches(observed: object, expected: str) -> bool:
        try:
            return hmac.compare_digest(
                str(observed or "").encode("ascii"),
                expected.encode("ascii"),
            )
        except UnicodeEncodeError:
            return False

    def _authorize_browser_mutation(self) -> bool:
        """Enforce the public-origin CSRF check and Access session boundary."""
        origin = self.headers.get("Origin") or ""
        if (
            self.headers.get_content_type() != "application/json"
            or not self._header_matches(origin, PUBLIC_URL)
            or not self._header_matches(
                (self.headers.get("Host") or "").casefold(),
                PUBLIC_ORIGIN.netloc.casefold(),
            )
            or self.headers.get("Authorization")
        ):
            return False
        return self._verify_access_session()

    def _authorize_operational_read(self, *, allow_ingest_token: bool = False) -> bool:
        """Require the Access session for dashboard and operational metadata reads."""
        if (
            allow_ingest_token
            and INGEST_TOKEN
            and self._header_matches(
                self.headers.get("Authorization") or "",
                f"Bearer {INGEST_TOKEN}",
            )
        ):
            return True
        if not self._header_matches(
            (self.headers.get("Host") or "").casefold(),
            PUBLIC_ORIGIN.netloc.casefold(),
        ):
            return False
        return self._verify_access_session()

    def _verify_access_session(self) -> bool:
        if local_access_exemption(BIND_HOST, self.client_address[0], PUBLIC_URL):
            return True
        if ACCESS_VERIFIER is None:
            return False
        try:
            ACCESS_VERIFIER.verify(self.headers.get(ACCESS_ASSERTION_HEADER))
        except AccessVerificationError:
            return False
        return True

    def log_message(self, format: str, *args: object) -> None:
        print(
            json.dumps(
                {
                    "timestamp": datetime_now(),
                    "level": "info",
                    "event": "http_request",
                    "client": self.client_address[0],
                    "request": self.requestline,
                    "message": format % args,
                }
            ),
            flush=True,
        )


def datetime_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main() -> None:
    host = BIND_HOST
    port = int(os.environ.get("CASHBACK_PORT", "5010"))
    rebuild_dashboard()
    server = ThreadingHTTPServer((host, port), CashbackHandler)
    stop_event = threading.Event()
    refresh_thread = None
    if REFRESH_SECONDS:
        refresh_thread = threading.Thread(
            target=refresh_dashboard_periodically,
            args=(stop_event,),
            name="cashback-dashboard-refresh",
            daemon=True,
        )
        refresh_thread.start()

    def stop_server(*_: object) -> None:
        stop_event.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    for signal_name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_name, stop_server)
    print(
        json.dumps(
            {
                "timestamp": datetime_now(),
                "level": "info",
                "event": "service_started",
                "host": host,
                "port": port,
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        stop_event.set()
        if refresh_thread is not None:
            refresh_thread.join(timeout=min(REFRESH_SECONDS + 1, 5))
        server.server_close()


if __name__ == "__main__":
    main()
