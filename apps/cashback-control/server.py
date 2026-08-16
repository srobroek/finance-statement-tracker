"""Serve and update the live provisional cashback routing dashboard."""

from __future__ import annotations

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

APP_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = APP_ROOT.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from finance_tracker.actual_pipeline import account_maps, load_actual_config, load_compiled_rules
from finance_tracker.cashback import load_program_configuration
from finance_tracker.cashback_events import CashbackEventStore, build_live_dashboard, write_dashboard
from finance_tracker.notifications import parse_outlook_notifications


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
STORE = CashbackEventStore(DATABASE_PATH)
WRITE_LOCK = threading.Lock()
STALE_AFTER_MINUTES = int(os.environ.get("CASHBACK_STALE_AFTER_MINUTES", "90"))
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
def parse_outlook_batch(source: dict[str, object]) -> dict[str, object]:
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
    batch = parse_outlook_notifications(
        messages,
        card_by_last4,
        rules,
    )
    persistence = (
        STORE.upsert(list(batch.events))
        if batch.events
        else {"inserted": 0, "updated": 0, "event_count": 0}
    )
    return {
        "parse": batch.to_dict(),
        "persistence": persistence,
        "cursor_candidate": source.get("cursor"),
        "cursor_committed": False,
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
        return payload


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
            "/api/review-queue",
        }:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        if path == "/api/alerts/ack":
            origin = urlsplit(self.headers.get("Origin") or "")
            if (
                self.headers.get_content_type() != "application/json"
                or not origin.netloc
                or origin.netloc.casefold() != str(self.headers.get("Host") or "").casefold()
            ):
                self._json(HTTPStatus.FORBIDDEN, {"error": "Same-origin JSON request required"})
                return
        elif INGEST_TOKEN and self.headers.get("Authorization") != f"Bearer {INGEST_TOKEN}":
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "Invalid ingest token"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0 or length > 1_000_000:
                raise ValueError("Request body must be between 1 byte and 1 MB")
            source = json.loads(self.rfile.read(length))
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
            if path == "/api/review-queue":
                if not isinstance(source, dict):
                    raise ValueError("Payload must be a review queue request object")
                try:
                    limit = int(source.get("limit") or 50)
                except (TypeError, ValueError) as error:
                    raise ValueError("review queue limit must be an integer") from error
                queue = STORE.review_queue(limit)
                self._json(HTTPStatus.OK, {"events": queue, "event_count": len(queue)})
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
            if path == "/api/events/validate":
                STORE.validate(events)
                self._json(HTTPStatus.OK, {"valid": True, "event_count": len(events)})
                return
            result = STORE.upsert(events)
            dashboard = rebuild_dashboard()
        except (ValueError, json.JSONDecodeError) as error:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
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
    host = os.environ.get("CASHBACK_HOST", "127.0.0.1")
    port = int(os.environ.get("CASHBACK_PORT", "5010"))
    rebuild_dashboard()
    server = ThreadingHTTPServer((host, port), CashbackHandler)
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(
            signal_name,
            lambda *_: threading.Thread(target=server.shutdown, daemon=True).start(),
        )
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
        server.server_close()


if __name__ == "__main__":
    main()
