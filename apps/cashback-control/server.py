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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
from finance_tracker.cashback import load_program_configuration, programs_from_config, statement_period
from finance_tracker.cashback_events import prepare_statement_reconciliation
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
from finance_tracker.mail_ingestion import OutlookScanPlan, build_outlook_envelope
from finance_tracker.notifications import (
    DEFAULT_NOTIFICATION_ADAPTERS,
    parse_outlook_notifications,
)
from finance_tracker.web_push import WebPushDispatcher, WebPushStore

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
STORE = CashbackEventStore(DATABASE_PATH)
PUSH_STORE = WebPushStore(DATABASE_PATH)
WRITE_LOCK = threading.Lock()
PUSH_LOCK = threading.Lock()
STALE_AFTER_MINUTES = int(os.environ.get("CASHBACK_STALE_AFTER_MINUTES", "90"))
REFRESH_SECONDS = max(0, int(os.environ.get("CASHBACK_REFRESH_SECONDS", "60")))
INGEST_SOURCE = os.environ.get("CASHBACK_INGEST_SOURCE", "outlook:rakbank").strip()
if not INGEST_SOURCE:
    raise ValueError("CASHBACK_INGEST_SOURCE must not be empty")
try:
    OPERATIONAL_TIMEZONE = ZoneInfo(os.environ.get("CASHBACK_TIMEZONE", "Asia/Dubai"))
except ZoneInfoNotFoundError as error:
    raise ValueError("CASHBACK_TIMEZONE must be a valid IANA timezone") from error
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
PUSH_DISPATCHER = WebPushDispatcher(
    PUSH_STORE,
    public_key=os.environ.get("CASHBACK_VAPID_PUBLIC_KEY", ""),
    private_key=os.environ.get("CASHBACK_VAPID_PRIVATE_KEY", ""),
    subject=os.environ.get("CASHBACK_VAPID_SUBJECT", ""),
    public_url=os.environ.get("CASHBACK_PUBLIC_URL", ""),
)


def _strict_timestamp(value: object, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.isoformat()


def _strict_count(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _validate_outlook_envelope(
    source: dict[str, object],
    *,
    source_name: str,
    completed_at: str,
    cursor: str,
    messages: list[dict[str, object]],
) -> None:
    schema_version = source.get("schema_version")
    if schema_version is not None and (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != 1
    ):
        raise ValueError("Unsupported Outlook envelope schema version")
    if datetime.fromisoformat(_strict_timestamp(cursor, "cursor")) != datetime.fromisoformat(completed_at):
        raise ValueError("cursor must equal completed_at for a frozen Outlook envelope")
    if "scanned_count" in source and _strict_count(source["scanned_count"], "scanned_count") != len(messages):
        raise ValueError("scanned_count must match the number of Outlook messages")
    if "matched_count" in source:
        matched_count = _strict_count(source["matched_count"], "matched_count")
        if matched_count < 0 or matched_count > len(messages):
            raise ValueError("matched_count must be within the scanned message count")
    window_start = source.get("window_start")
    if window_start not in (None, ""):
        start = _strict_timestamp(window_start, "window_start")
        if datetime.fromisoformat(start) > datetime.fromisoformat(completed_at):
            raise ValueError("window_start cannot be after completed_at")
        plan = OutlookScanPlan(
            source=source_name,
            window_start=start,
            window_end=completed_at,
            cursor_before=None,
            overlap_hours=0,
            initial_scan=False,
        )
        build_outlook_envelope(plan, messages)
        return
    # Older local submitters omitted window_start. Keep that compatibility and
    # enforce message bounds when the frozen window is supplied above.
    seen: set[str] = set()
    for message in messages:
        message_id = str(message.get("id") or "").strip()
        if not message_id:
            raise ValueError("Every Outlook message requires its exact id")
        if message_id in seen:
            raise ValueError(f"Duplicate Outlook message id in scan batch: {message_id}")
        seen.add(message_id)
        _strict_timestamp(message.get("receivedDateTime"), f"message {message_id} receivedDateTime")


def parse_outlook_batch(source: dict[str, object]) -> dict[str, object]:
    source_name = str(source.get("source") or "outlook").strip()
    if not source_name:
        raise ValueError("source is required")
    completed_at = _strict_timestamp(source.get("completed_at"), "completed_at")
    cursor = str(source.get("cursor") or "").strip()
    if not cursor:
        raise ValueError("cursor is required")
    messages = source.get("messages")
    if not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages):
        raise ValueError("messages must be a list of Outlook message objects")
    _validate_outlook_envelope(
        source,
        source_name=source_name,
        completed_at=completed_at,
        cursor=cursor,
        messages=messages,
    )

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
    enabled_adapter_codes = {
        source.adapter for source in notification_sources if source.active and source.adapter
    }
    enabled_adapters = tuple(
        adapter for adapter in DEFAULT_NOTIFICATION_ADAPTERS if adapter.code in enabled_adapter_codes
    )
    batch = parse_outlook_notifications(
        messages,
        card_by_last4,
        rules,
        adapters=enabled_adapters,
        cashback_config=cashback_config,
    )
    persistence = (
        STORE.upsert(list(batch.events))
        if batch.events
        else {"inserted": 0, "updated": 0, "unchanged": 0, "duplicates": 0}
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
    with WRITE_LOCK:
        selected_as_of = datetime.now(UTC).astimezone(OPERATIONAL_TIMEZONE).date()
        payload = build_live_dashboard(
            STORE,
            selected_as_of,
            stale_after_minutes=STALE_AFTER_MINUTES,
            program_config_path=PROGRAM_CONFIG_PATH,
            ingest_source=INGEST_SOURCE,
            excluded_cards={"EI_AMAZON"},
        )
        payload["selected_as_of"] = selected_as_of.isoformat()
        payload["is_historical"] = False
        write_dashboard(DASHBOARD_PATH, payload)
    with PUSH_LOCK:
        PUSH_DISPATCHER.evaluate(payload)
    return payload


def refresh_dashboard_periodically(stop_event: threading.Event) -> None:
    while not stop_event.wait(REFRESH_SECONDS):
        try:
            rebuild_dashboard()
        except Exception as error:  # service boundary; the next interval retries
            print(json.dumps({
                "timestamp": datetime_now(),
                "level": "error",
                "event": "dashboard_refresh_failed",
                "error": f"{type(error).__name__}: {error}"[:500],
            }), flush=True)


def _available_as_of_dates() -> list[str]:
    """Return dates for which a historical projection has usable evidence."""
    dates = {
        str(row["period_end"])
        for row in STORE.period_rows()
        if row["status"] == "FINALIZED"
        and str(row["card_code"]).upper() != "EI_AMAZON"
    }
    for receipt in STORE.statement_receipts(
        excluded_cards={"EI_AMAZON"},
        limit=200,
    ):
        if receipt["period_end"]:
            dates.add(str(receipt["period_end"]))
    today = datetime.now(UTC).astimezone(OPERATIONAL_TIMEZONE).date()
    configuration = load_program_configuration(PROGRAM_CONFIG_PATH)
    programs_by_card = {
        program.card.upper(): program
        for program in programs_from_config(configuration, period_date=today)
        if program.card.upper() != "EI_AMAZON"
    }
    for row in STORE.rows(date(1970, 1, 1), today):
        card = programs_by_card.get(str(row["card_code"]).upper())
        if card is None:
            continue
        try:
            occurred = date.fromisoformat(str(row["occurred_at"])[:10])
        except ValueError:
            continue
        _, period_end = statement_period(occurred, card.statement_close_day)
        if period_end <= today:
            dates.add(period_end.isoformat())
    return sorted(
        (value for value in dates if date.fromisoformat(value) <= today),
        reverse=True,
    )


def _historical_dashboard(
    as_of: date,
    *,
    periods_by_card: dict[str, tuple[date, date]] | None = None,
) -> dict[str, object]:
    """Build a read-only projection without touching the live snapshot."""
    payload = build_live_dashboard(
        STORE,
        as_of,
        stale_after_minutes=STALE_AFTER_MINUTES,
        program_config_path=PROGRAM_CONFIG_PATH,
        ingest_source=INGEST_SOURCE,
        excluded_cards={"EI_AMAZON"},
        periods_by_card=periods_by_card,
    )
    payload["selected_as_of"] = as_of.isoformat()
    payload["is_historical"] = True
    cycles = [
        receipt
        for receipt in STORE.statement_receipts(
            excluded_cards={"EI_AMAZON"},
            limit=200,
        )
        if receipt["period_end"] == as_of.isoformat()
    ]
    finalized = any(
        row["status"] == "FINALIZED"
        and str(row["period_end"]) == as_of.isoformat()
        and str(row["card_code"]).upper() != "EI_AMAZON"
        for row in STORE.period_rows()
    )
    payload["statement_cycles"] = cycles
    payload["settlement_state"] = (
        "UNFINALIZED"
        if any(cycle["settlement_state"] != "FINALIZED" for cycle in cycles) or not finalized
        else "FINALIZED"
    )
    return payload


def historical_periods(limit: int = 24) -> list[dict[str, object]]:
    """Return finalized and evidence-backed unfinalized cycles for browsing."""
    if limit < 1 or limit > 200:
        raise ValueError("period limit must be between 1 and 200")
    finalized = {
        (
            str(row["card_code"]),
            str(row["period_start"]),
            str(row["period_end"]),
        ): row
        for row in STORE.period_rows()
        if row["status"] == "FINALIZED"
        and str(row["card_code"]).upper() != "EI_AMAZON"
    }
    receipts = STORE.statement_receipts(
        excluded_cards={"EI_AMAZON"},
        limit=200,
    )
    candidates: dict[tuple[str, str, str], dict[str, object]] = {}
    for receipt in receipts:
        if not receipt["period_start"] or not receipt["period_end"]:
            continue
        key = (
            str(receipt["card_code"]),
            str(receipt["period_start"]),
            str(receipt["period_end"]),
        )
        candidates.setdefault(key, receipt)

    result: list[dict[str, object]] = []
    for key, period in finalized.items():
        receipt = next(
            (
                item
                for item in receipts
                if (
                    str(item["card_code"]),
                    str(item["period_start"]),
                    str(item["period_end"]),
                )
                == key
            ),
            None,
        )
        period_end = date.fromisoformat(key[2])
        snapshot = _historical_dashboard(period_end)
        card = next(
            (item for item in snapshot["cards"] if item["card"] == key[0]),
            None,
        )
        if card is None:
            continue
        item: dict[str, object] = {
            "card": key[0],
            "period_start": key[1],
            "period_end": key[2],
            "status": period["status"],
            "reconciliation_status": period["reconciliation_status"],
            "statement_reference": period["statement_reference"],
            "finalized_at": period["finalized_at"],
            "settlement_state": "FINALIZED",
            "summary": card,
        }
        if receipt is not None:
            item["statement_receipt"] = receipt
            item["bank_state"] = receipt["bank_state"]
            item["processing_state"] = receipt["processing_state"]
            item["reconciliation_state"] = receipt["reconciliation_state"]
        result.append(item)

    for key, receipt in candidates.items():
        if key in finalized:
            continue
        period_end = date.fromisoformat(key[2])
        snapshot = _historical_dashboard(period_end)
        card = next(
            (item for item in snapshot["cards"] if item["card"] == key[0]),
            None,
        )
        if card is None:
            continue
        result.append({
            "card": key[0],
            "period_start": key[1],
            "period_end": key[2],
            "status": "BANK_CLOSED",
            "bank_state": receipt["bank_state"],
            "processing_state": receipt["processing_state"],
            "reconciliation_state": receipt["reconciliation_state"],
            "bounds_state": receipt["bounds_state"],
            "reconciliation_status": receipt["reconciliation_state"],
            "statement_reference": receipt["statement_reference"],
            "finalized_at": None,
            "settlement_state": "UNFINALIZED",
            "statement_receipt": receipt,
            "summary": card,
        })
    result.sort(
        key=lambda item: (
            str(item["period_end"]),
            str(item["card"]),
        ),
        reverse=True,
    )
    return result[:limit]


def _previous_statement_cycles(as_of: date | None = None) -> list[dict[str, object]]:
    """Return each active card's prior cycle using actual bounds when available."""
    selected_as_of = as_of or datetime.now(UTC).astimezone(OPERATIONAL_TIMEZONE).date()
    configuration = load_program_configuration(PROGRAM_CONFIG_PATH)
    programs = programs_from_config(configuration, period_date=selected_as_of)
    result: list[dict[str, object]] = []

    for program in programs:
        if program.card.upper() == "EI_AMAZON":
            continue
        current_start, _ = statement_period(selected_as_of, program.statement_close_day)
        expected_start, expected_end = statement_period(
            current_start - timedelta(days=1),
            program.statement_close_day,
        )
        receipts = STORE.statement_receipts(
            card_code=program.card,
            excluded_cards={"EI_AMAZON"},
            limit=200,
        )
        dated_receipts = []
        for candidate in receipts:
            if not candidate["period_start"] or not candidate["period_end"]:
                continue
            candidate_start = date.fromisoformat(str(candidate["period_start"]))
            candidate_end = date.fromisoformat(str(candidate["period_end"]))
            if candidate_start < current_start and candidate_end <= selected_as_of:
                dated_receipts.append((candidate_start, candidate_end, candidate))
        receipt = (
            max(
                dated_receipts,
                key=lambda item: (
                    item[1],
                    item[0],
                    str(item[2].get("received_at") or ""),
                ),
            )[2]
            if dated_receipts
            else next(
                (
                    candidate
                    for candidate in receipts
                    if not candidate["period_start"] and not candidate["period_end"]
                ),
                None,
            )
        )

        known_bounds = bool(receipt and receipt["period_start"] and receipt["period_end"])
        calculation_start = (
            date.fromisoformat(str(receipt["period_start"]))
            if known_bounds
            else expected_start
        )
        calculation_end = (
            date.fromisoformat(str(receipt["period_end"]))
            if known_bounds
            else expected_end
        )
        snapshot = _historical_dashboard(
            calculation_end,
            periods_by_card=(
                {program.card: (calculation_start, calculation_end)}
                if known_bounds
                else None
            ),
        )
        candidate = next(
            (card for card in snapshot["cards"] if card["card"] == program.card),
            None,
        )
        finalized = any(
            row["status"] == "FINALIZED"
            and str(row["card_code"]).upper() == program.card.upper()
            and str(row["period_start"]) == calculation_start.isoformat()
            and str(row["period_end"]) == calculation_end.isoformat()
            for row in STORE.period_rows()
        )
        has_snapshot_evidence = (
            finalized
            or (candidate is not None and int(candidate.get("transaction_count") or 0) > 0)
            or bool(receipt and receipt["processing_state"] == "PARSED")
        )
        result.append({
            "card": program.card,
            "period_start": receipt["period_start"] if known_bounds else None,
            "period_end": receipt["period_end"] if known_bounds else None,
            "expected_period_start": expected_start.isoformat(),
            "expected_period_end": expected_end.isoformat(),
            "status": receipt["status"] if receipt else "NO_RECEIPT",
            "bank_state": receipt["bank_state"] if receipt else "NOT_RECEIVED",
            "processing_state": receipt["processing_state"] if receipt else None,
            "reconciliation_state": receipt["reconciliation_state"] if receipt else None,
            "reconciliation_status": receipt["reconciliation_state"] if receipt else None,
            "bounds_state": receipt["bounds_state"] if receipt else "UNKNOWN",
            "statement_reference": receipt["statement_reference"] if receipt else None,
            "finalized_at": None,
            "settlement_state": receipt["settlement_state"] if receipt else None,
            "statement_receipt": receipt,
            "snapshot_as_of": calculation_end.isoformat() if has_snapshot_evidence else None,
            "summary": candidate if has_snapshot_evidence else None,
        })
    return result

class CashbackHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def end_headers(self) -> None:
        if not urlsplit(self.path).path.startswith("/api/"):
            self.send_header("Content-Security-Policy", CASHBACK_CONTENT_SECURITY_POLICY)
            self.send_header("X-Content-Type-Options", "nosniff")
            # The dashboard is a small operational UI. Revalidate its static
            # shell on every visit so a container rollout cannot leave a
            # device running stale routing code from its browser cache.
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if path in {
            "/api/dashboard",
            "/api/periods",
            "/api/periods/previous",
            "/api/health",
            "/api/push/config",
            "/api/statement-receipts",
        } and not self._authorize_operational_read(allow_ingest_token=path == "/api/health"):
            self._json(HTTPStatus.FORBIDDEN, {"error": "Operational read authorization required"})
            return
        if path == "/api/health":
            self._json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "dashboard_available": DASHBOARD_PATH.is_file(),
                    "event_store": STORE.stats(INGEST_SOURCE, excluded_cards={"EI_AMAZON"}),
                },
            )
            return

        if path == "/api/statement-receipts":
            receipts = STORE.statement_receipts(
                excluded_cards={"EI_AMAZON"},
                limit=200,
            )
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
            receipts = STORE.statement_receipts(
                excluded_cards={"EI_AMAZON"},
                limit=200,
            )
            self._json(
                HTTPStatus.OK,
                {
                    "periods": periods,
                    "period_count": len(periods),
                    "available_as_of_dates": _available_as_of_dates(),
                    "statement_receipts": receipts,
                    "statement_receipt_count": len(receipts),
                    "previous_statement_cycles": _previous_statement_cycles(),
                },
            )
            return

        if path == "/api/push/config":
            self._json(HTTPStatus.OK, PUSH_DISPATCHER.config())
            return

        if path == "/api/dashboard":
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            raw_as_of = query.get("as_of", [None])
            if len(raw_as_of) != 1:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "as_of must be a single ISO date"})
                return
            if raw_as_of[0] is not None:
                raw_date = str(raw_as_of[0])
                try:
                    as_of = date.fromisoformat(raw_date)
                except ValueError:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "as_of must be a valid ISO date"})
                    return
                if as_of.isoformat() != raw_date:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "as_of must be a valid ISO date"})
                    return
                today = datetime.now(UTC).astimezone(OPERATIONAL_TIMEZONE).date()
                if as_of > today:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "as_of cannot be in the future"})
                    return
                try:
                    payload = _historical_dashboard(as_of)
                except ValueError as error:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                    return
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
            payload["selected_as_of"] = payload.get(
                "selected_as_of",
                datetime.now(UTC).astimezone(OPERATIONAL_TIMEZONE).date().isoformat(),
            )
            payload["is_historical"] = False
            self._json(HTTPStatus.OK, payload)
            return

        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        handlers = {
            "/api/events": self._post_events,
            "/api/events/validate": self._post_events_validate,
            "/api/ingest-runs": self._post_ingest_runs,
            "/api/ingest/transaction": self._post_ingest_transaction,
            "/api/ingest/receipt": self._post_ingest_receipt,
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
                self._json(HTTPStatus.FORBIDDEN, {"error": "Browser mutation authorization required"})
                return
        elif not INGEST_TOKEN:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Cashback ingest token is not configured"})
            return
        elif not self._authorize_ingest():
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "Invalid ingest token"})
            return
        try:
            result = handlers[path](self._read_json_body())
        except (ValueError, json.JSONDecodeError) as error:
            status = HTTPStatus.CONFLICT if isinstance(error, IngestCursorConflict) else HTTPStatus.BAD_REQUEST
            self._json(status, {"error": str(error)})
            return
        except Exception as error:  # service boundary: keep operational details out of responses
            self._log_post_failure(path, error)
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal server error"})
            return
        self._json(HTTPStatus.OK, result)

    def _post_statement_receipt(self, source: object) -> dict[str, object]:
        """Accept the n8n arrival receipt before decrypt, parse, or reconcile."""
        if not isinstance(source, dict):
            raise ValueError("Payload must be a statement receipt object")
        receipt = source.get("receipt", source)
        if not isinstance(receipt, dict):
            raise ValueError("receipt must be a statement receipt object")
        with WRITE_LOCK:
            result = STORE.record_statement_receipt(receipt)
        stored = result["statement_receipt"]
        return {
            "statement_receipt": stored,
            "receipt": stored,
            "idempotent_replay": result["idempotent_replay"],
            "bank_state": stored["bank_state"],
            "processing_state": stored["processing_state"],
            "reconciliation_state": stored["reconciliation_state"],
            "producer_contract": STATEMENT_RECEIPT_CONTRACT,
        }

    def _post_statement_receipt_state(self, source: object) -> dict[str, object]:
        if not isinstance(source, dict):
            raise ValueError("Payload must be a statement receipt state object")
        receipt_id = str(
            source.get("receipt_id")
            or source.get("statement_receipt_id")
            or ""
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
            "bank_state": stored["bank_state"],
            "processing_state": stored["processing_state"],
            "reconciliation_state": stored["reconciliation_state"],
        }


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

    def _post_ingest_transaction(self, source: object) -> dict[str, object]:
        if not isinstance(source, dict):
            raise ValueError("Payload must be a transaction object")
        with WRITE_LOCK:
            result = STORE.ingest_transaction(source)
        dashboard = rebuild_dashboard()
        return {**result, "event_store": dashboard["data_status"]}

    def _post_ingest_receipt(self, source: object) -> dict[str, object]:
        if not isinstance(source, dict):
            raise ValueError("Payload must be a scan receipt object")
        with WRITE_LOCK:
            return STORE.combine_transaction_receipts(source)

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
        raw_receipts = source.get("statement_receipts")
        if raw_receipts is None and source.get("statement_receipt") is not None:
            raw_receipts = [source["statement_receipt"]]
        if raw_receipts is None:
            receipt_results: list[dict[str, object]] = []
        else:
            if isinstance(raw_receipts, dict):
                raw_receipts = [raw_receipts]
            if (
                not isinstance(raw_receipts, list)
                or any(not isinstance(item, dict) for item in raw_receipts)
            ):
                raise ValueError("statement_receipts must be a list of receipt objects")
            with WRITE_LOCK:
                receipt_results = [
                    STORE.record_statement_receipt(item)["statement_receipt"]
                    for item in raw_receipts
                ]
        result = parse_outlook_batch(source)
        dashboard = rebuild_dashboard()
        response = {**result, "event_store": dashboard["data_status"]}
        if receipt_results:
            response["statement_receipts"] = receipt_results
        return response

    def _post_reconcile(self, source: object) -> dict[str, object]:
        if not isinstance(source, dict):
            raise ValueError("Payload must be a statement reconciliation object")
        result = STORE.reconcile_statement(prepare_statement_reconciliation(
            source, load_program_configuration(PROGRAM_CONFIG_PATH, as_of=date.fromisoformat(str(source.get("period_end"))))
        ))
        receipt_id = str(
            source.get("statement_receipt_id")
            or source.get("receipt_id")
            or ""
        ).strip()
        if receipt_id:
            reconciliation_state = (
                "RECONCILED" if result["notification_only"] == 0 else "VARIANCE"
            )
            result["statement_receipt"] = STORE.update_statement_receipt(
                receipt_id,
                processing_state="PARSED",
                reconciliation_state=reconciliation_state,
                period_start=str(source.get("period_start") or ""),
                period_end=str(source.get("period_end") or ""),
                statement_reference=str(source.get("statement_reference") or ""),
            )
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

    def _post_events_payload(self, source: object) -> list[dict[str, object]]:
        events = source if isinstance(source, list) else [source]
        if any(not isinstance(event, dict) for event in events):
            raise ValueError("Payload must be an event object or a list of event objects")
        event_objects = [event for event in events if isinstance(event, dict)]
        profile_currency = str(
            load_program_configuration(PROGRAM_CONFIG_PATH).get("currency") or ""
        ).strip().upper()
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
        print(json.dumps({
            "timestamp": datetime_now(),
            "level": "error",
            "event": "http_request_failed",
            "path": path,
            "exception_type": type(error).__name__,
        }), flush=True)

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

    def _authorize_browser_mutation(self) -> bool:
        """Enforce the public-origin CSRF check and Access session boundary."""
        origin = self.headers.get("Origin") or ""
        if (
            self.headers.get_content_type() != "application/json"
            or origin != PUBLIC_URL
            or (self.headers.get("Host") or "").casefold() != PUBLIC_ORIGIN.netloc.casefold()
            or self.headers.get("Authorization")
        ):
            return False
        return self._verify_access_session()

    def _authorize_operational_read(self, *, allow_ingest_token: bool = False) -> bool:
        """Require the Access session for dashboard and operational metadata reads."""
        if (
            allow_ingest_token
            and INGEST_TOKEN
            and self._authorize_ingest()
        ):
            return True
        if (self.headers.get("Host") or "").casefold() != PUBLIC_ORIGIN.netloc.casefold():
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
        print(json.dumps({
            "timestamp": datetime_now(),
            "level": "info",
            "event": "http_request",
            "client": self.client_address[0],
            "request": self.requestline,
            "message": format % args,
        }), flush=True)


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
    print(json.dumps({
        "timestamp": datetime_now(),
        "level": "info",
        "event": "service_started",
        "host": host,
        "port": port,
    }), flush=True)
    try:
        server.serve_forever()
    finally:
        stop_event.set()
        if refresh_thread is not None:
            refresh_thread.join(timeout=min(REFRESH_SECONDS + 1, 5))
        server.server_close()


if __name__ == "__main__":
    main()
