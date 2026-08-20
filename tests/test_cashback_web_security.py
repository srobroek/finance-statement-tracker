"""Executable browser checks for untrusted Cashback dashboard values."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "apps" / "cashback-control" / "web"
FIXTURE = ROOT / "tests" / "fixtures" / "cashback-web-malicious.json"


class _FixtureHandler(SimpleHTTPRequestHandler):
    fixture_payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def __init__(self, *args, directory: str, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def _json(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        request = urlsplit(self.path)
        mode = parse_qs(request.query).get("fixture", ["success"])[0]
        if request.path == "/index.html":
            self.server.fixture_mode = mode
        else:
            mode = getattr(self.server, "fixture_mode", "success")
        if request.path == "/api/dashboard":
            if mode == "error":
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "<img src=x onerror=\"document.body.dataset.xss='api-error'\">"},
                )
            else:
                self._json(HTTPStatus.OK, self.fixture_payload)
            return
        if request.path == "/api/periods":
            self._json(HTTPStatus.OK, {"periods": []})
            return
        if request.path == "/api/push/config":
            self._json(HTTPStatus.OK, {"enabled": False})
            return
        super().do_GET()

    def log_message(self, format: str, *args: object) -> None:
        return


def _browser_command() -> str | None:
    for candidate in ("google-chrome", "chromium", "chromium-browser"):
        command = shutil.which(candidate)
        if command:
            return command
    return None


class CashbackWebSecurityTests(unittest.TestCase):
    def test_dashboard_has_no_html_string_sinks(self) -> None:
        app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
        self.assertNotRegex(app, r"\b(?:innerHTML|outerHTML|insertAdjacentHTML)\b")
        shell = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("Content-Security-Policy", shell)
        self.assertIn("script-src 'self'", shell)
        server = (ROOT / "apps" / "cashback-control" / "server.py").read_text(encoding="utf-8")
        self.assertIn("CASHBACK_CONTENT_SECURITY_POLICY", server)
        self.assertIn("frame-ancestors 'none'", server)

    @unittest.skipUnless(_browser_command(), "Chrome/Chromium is required for executable browser fixtures")
    def test_malicious_card_rule_and_alert_values_render_as_text(self) -> None:
        dom = self._dump_dom("success")
        self.assertIn("route-row", dom)
        self.assertIn("position-card", dom)
        self.assertIn("tier-ladder", dom)
        self.assertIn("bucket-row", dom)
        self.assertIn("alert-card", dom)
        self.assertIn("&lt;img", dom)
        self.assertIn("&lt;svg", dom)
        self.assertNotIn('data-xss="', dom)

    @unittest.skipUnless(_browser_command(), "Chrome/Chromium is required for executable browser fixtures")
    def test_malicious_api_error_renders_as_text(self) -> None:
        dom = self._dump_dom("error")
        self.assertIn('class="error"', dom)
        self.assertIn("&lt;img", dom)
        self.assertNotIn('data-xss="', dom)

    def _dump_dom(self, mode: str) -> str:
        handler = partial(_FixtureHandler, directory=str(WEB_ROOT))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        browser = _browser_command()
        self.assertIsNotNone(browser)
        try:
            with tempfile.TemporaryDirectory(prefix="cashback-web-chrome-") as profile:
                result = subprocess.run(
                    [
                        browser,
                        "--headless=new",
                        "--no-sandbox",
                        "--disable-gpu",
                        "--disable-dev-shm-usage",
                        f"--user-data-dir={profile}",
                        "--dump-dom",
                        "--virtual-time-budget=2500",
                        f"http://127.0.0.1:{server.server_port}/index.html?fixture={mode}",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
