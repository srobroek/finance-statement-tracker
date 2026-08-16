"""Serve deterministic statement and browser ingestion jobs for Actual Budget."""

from __future__ import annotations

import hmac
import json
import os
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


APP_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = APP_ROOT.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from finance_tracker.ingestion_jobs import IngestionJobRunner
from finance_tracker.statement_sources import load_statement_sources
from finance_tracker.statements import DEFAULT_STATEMENT_ADAPTERS


DATA_ROOT = Path(os.environ.get("FINANCE_INGEST_DATA", "/var/lib/finance-ingestion")).resolve()
TOKEN = os.environ.get("FINANCE_INGEST_TOKEN", "")
HOST = os.environ.get("FINANCE_INGEST_HOST", "0.0.0.0")
PORT = int(os.environ.get("FINANCE_INGEST_PORT", "5020"))
RUNNER = IngestionJobRunner(DATA_ROOT, REPOSITORY_ROOT)
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {TOKEN}"
        return bool(TOKEN) and hmac.compare_digest(supplied, expected)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/health":
            sources = load_statement_sources(REPOSITORY_ROOT / "config" / "statement-sources.json")
            self._json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "actual_writes_enabled": os.environ.get("ALLOW_ACTUAL_WRITES", "").casefold() == "true",
                    "statement_adapters": list(DEFAULT_STATEMENT_ADAPTERS.codes),
                    "statement_placeholders": [
                        source.card_code for source in sources if not source.adapter_active
                    ],
                },
            )
            return
        if path.startswith("/api/jobs/"):
            if not self._authorized():
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "Invalid ingestion token"})
                return
            try:
                self._json(HTTPStatus.OK, RUNNER.result(path.rsplit("/", 1)[-1]))
            except ValueError as error:
                self._json(HTTPStatus.NOT_FOUND, {"error": str(error)})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        if urlsplit(self.path).path != "/api/jobs":
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        if not self._authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "Invalid ingestion token"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0 or length > 1_000_000:
                raise ValueError("Request body must be between 1 byte and 1 MB")
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict):
                raise ValueError("Ingestion request must be an object")
            with LOCK:
                result = RUNNER.submit(request)
            self._json(HTTPStatus.OK, result)
        except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as error:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})


def main() -> None:
    if not TOKEN:
        raise RuntimeError("FINANCE_INGEST_TOKEN must be configured")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
