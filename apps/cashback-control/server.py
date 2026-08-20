"""Serve and update the live cashback routing dashboard."""

from __future__ import annotations

import hmac
import json
import os
import signal
import sys
import threading
import time
from datetime import date
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

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
from finance_tracker.cashback import load_program_configuration
from finance_tracker.cashback_events import (
    CashbackEventStore,
    IngestCursorConflict,
    _iso_datetime,
    _json_digest,
    build_live_dashboard,
    write_dashboard,
)
from finance_tracker.notification_sources import (
    load_notification_sources,
    validate_notification_adapter_coverage,
)
from finance_tracker.notifications import (
    DEFAULT_NOTIFICATION_ADAPTERS,
    parse_outlook_notifications,
)
from finance_tracker.web_push import WebPushDispatcher, WebPushStore

WEB_ROOT = APP_ROOT / "web"
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


def parse_outlook_batch(source: dict[str, object]) -> dict[str, object]:
    source_name = str(source.get("source") or "outlook").strip()
    completed_at = _iso_datetime(source.get("completed_at"))
    cursor = str(source.get("cursor") or "").strip()
    if not cursor:
        raise ValueError("cursor is required")
    messages = source.get("messages")
    if not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages):
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
    with WRITE_LOCK:
        payload = build_live_dashboard(
            STORE,
            date.today(),
            stale_after_minutes=STALE_AFTER_MINUTES,
            program_config_path=PROGRAM_CONFIG_PATH,
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
            print(json.dumps({
                "timestamp": datetime_now(),
                "level": "error",
                "event": "dashboard_refresh_failed",
                "error": f"{type(error).__name__}: {error}"[:500],
            }), flush=True)


def historical_periods(limit: int = 24) -> list[dict[str, object]]:
    periods = [row for row in STORE.period_rows() if row["status"] == "FINALIZED"][:limit]
    result = []
    for period in periods:
        period_end = date.fromisoformat(str(period["period_end"]))
        snapshot = build_live_dashboard(
            STORE,
            period_end,
            stale_after_minutes=STALE_AFTER_MINUTES,
            program_config_path=PROGRAM_CONFIG_PATH,
        )
        card = next(
            (item for item in snapshot["cards"] if item["card"] == period["card_code"]),
            None,
        )
        if card is None:
            continue
        result.append({
            "card": period["card_code"],
            "period_start": period["period_start"],
            "period_end": period["period_end"],
            "status": period["status"],
            "reconciliation_status": period["reconciliation_status"],
            "statement_reference": period["statement_reference"],
            "finalized_at": period["finalized_at"],
            "summary": card,
        })
    return result


class CashbackHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def end_headers(self) -> None:
        if not urlsplit(self.path).path.startswith("/api/"):
            # The dashboard is a small operational UI. Revalidate its static
            # shell on every visit so a container rollout cannot leave a
            # device running stale routing code from its browser cache.
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "dashboard_available": DASHBOARD_PATH.is_file(),
                    "event_store": STORE.stats(),
                },
            )
            return

        if path == "/api/periods":
            periods = historical_periods()
            self._json(HTTPStatus.OK, {"periods": periods, "period_count": len(periods)})
            return

        if path == "/api/push/config":
            self._json(HTTPStatus.OK, PUSH_DISPATCHER.config())
            return

        if path == "/api/dashboard":
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
        if path not in {
            "/api/events",
            "/api/events/validate",
            "/api/ingest-runs",
            "/api/ingest-state",
            "/api/reconcile",
            "/api/corrections",
            "/api/periods/finalize",
            "/api/alerts/ack",
            "/api/outlook/messages",
            "/api/push/subscriptions",
        }:
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
        elif self.headers.get("Authorization") != f"Bearer {INGEST_TOKEN}":
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "Invalid ingest token"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0 or length > 1_000_000:
                raise ValueError("Request body must be between 1 byte and 1 MB")
            source = json.loads(self.rfile.read(length))
            if path == "/api/push/subscriptions":
                if not isinstance(source, dict):
                    raise ValueError("Payload must be a push subscription request")
                action = str(source.get("action") or "subscribe").strip().casefold()
                subscription = source.get("subscription")
                if not isinstance(subscription, dict):
                    raise ValueError("subscription must be an object")
                if action == "unsubscribe":
                    result = PUSH_STORE.remove_subscription(subscription.get("endpoint"))
                    self._json(HTTPStatus.OK, {"subscription": result, "push": PUSH_DISPATCHER.config()})
                    return
                if action != "subscribe":
                    raise ValueError("action must be subscribe or unsubscribe")
                result = PUSH_STORE.upsert_subscription(
                    subscription,
                    str(self.headers.get("User-Agent") or "")[:500],
                )
                delivery = PUSH_DISPATCHER.send_test(str(result["endpoint"]))
                self._json(
                    HTTPStatus.OK,
                    {"subscription": result, "test_delivery": delivery, "push": PUSH_DISPATCHER.config()},
                )
                return
            if path == "/api/alerts/ack":
                if not isinstance(source, dict):
                    raise ValueError("Payload must be an alert acknowledgement object")
                result = STORE.set_alert_acknowledgement(
                    source.get("alert_key"),
                    source.get("acknowledged"),
                )
                dashboard = rebuild_dashboard()
                self._json(HTTPStatus.OK, {"alert": result, "event_store": dashboard["data_status"]})
                return
            if path == "/api/ingest-runs":
                if not isinstance(source, dict):
                    raise ValueError("Payload must be an ingest run object")
                result = STORE.record_ingest_success(source)
                dashboard = rebuild_dashboard()
                self._json(HTTPStatus.OK, {"ingest": result, "event_store": dashboard["data_status"]})
                return
            if path == "/api/ingest-state":
                if not isinstance(source, dict):
                    raise ValueError("Payload must be an ingest state request object")
                state = STORE.ingest_state(str(source.get("source") or "outlook"))
                self._json(HTTPStatus.OK, {"ingest_state": state})
                return
            if path == "/api/outlook/messages":
                if not isinstance(source, dict):
                    raise ValueError("Payload must be an Outlook message batch object")
                result = parse_outlook_batch(source)
                dashboard = rebuild_dashboard()
                self._json(HTTPStatus.OK, {**result, "event_store": dashboard["data_status"]})
                return
            if path == "/api/reconcile":
                if not isinstance(source, dict):
                    raise ValueError("Payload must be a statement reconciliation object")
                result = STORE.reconcile_statement(source)
                dashboard = rebuild_dashboard()
                self._json(HTTPStatus.OK, {"reconciliation": result, "event_store": dashboard["data_status"]})
                return
            if path == "/api/corrections":
                if not isinstance(source, dict):
                    raise ValueError("Payload must be an event correction object")
                result = STORE.correct_event(source)
                dashboard = rebuild_dashboard()
                self._json(HTTPStatus.OK, {"correction": result, "event_store": dashboard["data_status"]})
                return
            if path == "/api/periods/finalize":
                if not isinstance(source, dict):
                    raise ValueError("Payload must be a card-period finalization object")
                result = STORE.finalize_period(source, program_config_path=PROGRAM_CONFIG_PATH)
                dashboard = rebuild_dashboard()
                self._json(HTTPStatus.OK, {"period": result, "event_store": dashboard["data_status"]})
                return
            events = source if isinstance(source, list) else [source]
            if any(not isinstance(event, dict) for event in events):
                raise ValueError("Payload must be an event object or a list of event objects")
            profile_currency = str(
                load_program_configuration(PROGRAM_CONFIG_PATH).get("currency") or ""
            ).strip().upper()
            if not profile_currency:
                raise ValueError("Cashback profile currency is required")
            events = [
                {**event, "currency": event.get("currency") or profile_currency}
                for event in events
            ]
            if path == "/api/events/validate":
                STORE.validate(events)
                self._json(HTTPStatus.OK, {"valid": True, "event_count": len(events)})
                return
            result = STORE.upsert(events)
            dashboard = rebuild_dashboard()
        except (ValueError, json.JSONDecodeError) as error:
            status = HTTPStatus.CONFLICT if isinstance(error, IngestCursorConflict) else HTTPStatus.BAD_REQUEST
            self._json(status, {"error": str(error)})
            return
        self._json(HTTPStatus.OK, {**result, "event_store": dashboard["data_status"]})

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
            or not hmac.compare_digest(origin, PUBLIC_URL)
            or not hmac.compare_digest(
                (self.headers.get("Host") or "").casefold(),
                PUBLIC_ORIGIN.netloc.casefold(),
            )
            or self.headers.get("Authorization")
        ):
            return False
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
