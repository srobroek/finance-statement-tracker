"""Executable browser checks for untrusted Cashback dashboard values."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from functools import partial
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "apps" / "cashback-control" / "web"
FIXTURE = ROOT / "tests" / "fixtures" / "cashback-web-malicious.json"
EXPECTED_CSP = (
    "default-src 'self'; base-uri 'none'; connect-src 'self'; form-action 'self'; "
    "frame-ancestors 'none'; img-src 'self'; object-src 'none'; script-src 'self'; "
    "style-src 'self' 'unsafe-inline'"
)


class _RenderedDom(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, list[tuple[str, str | None]]]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append((tag, attrs))

    def handle_data(self, data: str) -> None:
        self.text.append(data)


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


def _meta_attributes(shell: str) -> list[dict[str, str]]:
    parser = _RenderedDom()
    parser.feed(shell)
    return [
        {name: value or "" for name, value in attrs}
        for tag, attrs in parser.elements
        if tag.casefold() == "meta"
    ]


def _server_policy(source: str) -> str:
    tree = ast.parse(source)
    assignment = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "CASHBACK_CONTENT_SECURITY_POLICY" for target in node.targets)
    )
    return ast.literal_eval(assignment.value)


def _header_calls(source: str) -> set[tuple[str, str]]:
    tree = ast.parse(source)
    return {
        (call.args[0].value, call.args[1].id if isinstance(call.args[1], ast.Name) else call.args[1].value)
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "send_header"
        and len(call.args) == 2
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[1], (ast.Name, ast.Constant))
    }


def _fixture_sentinels() -> set[str]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    encoded = json.dumps(payload)
    return set(re.findall(r"dataset\.xss='([^']+)'", encoded))


def _load_cashback_server():
    app_root = str(ROOT / "apps" / "cashback-control")
    sys.path.insert(0, app_root)
    try:
        import importlib

        os.environ.setdefault("CASHBACK_PUBLIC_URL", "http://127.0.0.1:5010")
        return importlib.import_module("server")
    finally:
        sys.path.remove(app_root)


class CashbackWebSecurityTests(unittest.TestCase):
    def test_dashboard_has_no_html_string_sinks(self) -> None:
        app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
        self.assertNotRegex(app, r"\b(?:innerHTML|outerHTML|insertAdjacentHTML)\b")
        shell = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        shell_policy = next(
            attrs.get("content")
            for attrs in _meta_attributes(shell)
            if attrs.get("http-equiv", "").casefold() == "content-security-policy"
        )
        self.assertEqual(shell_policy, EXPECTED_CSP)
        server_source = (ROOT / "apps" / "cashback-control" / "server.py").read_text(encoding="utf-8")
        self.assertEqual(_server_policy(server_source), EXPECTED_CSP)
        self.assertIn(
            ('Content-Security-Policy', 'CASHBACK_CONTENT_SECURITY_POLICY'),
            _header_calls(server_source),
        )
        self.assertIn(("X-Content-Type-Options", "nosniff"), _header_calls(server_source))

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
        self._assert_safe_render(dom, _fixture_sentinels())

    @unittest.skipUnless(_browser_command(), "Chrome/Chromium is required for executable browser fixtures")
    def test_malicious_api_error_renders_as_text(self) -> None:
        dom = self._dump_dom("error")
        self.assertIn('class="error"', dom)
        self.assertIn("&lt;img", dom)
        self.assertNotIn('data-xss="', dom)
        self._assert_safe_render(dom, {"api-error"})

    @unittest.skipUnless(_browser_command(), "Chrome/Chromium is required for executable browser fixtures")
    def test_cashback_handler_sends_exact_browser_security_headers(self) -> None:
        try:
            server_module = _load_cashback_server()
        except ModuleNotFoundError as error:
            self.skipTest(f"Cashback server dependencies are unavailable: {error}")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_module.CashbackHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{httpd.server_port}/index.html", timeout=5) as response:
                self.assertEqual(response.headers["Content-Security-Policy"], EXPECTED_CSP)
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def _assert_safe_render(self, dom: str, sentinels: set[str]) -> None:
        rendered = _RenderedDom()
        rendered.feed(dom)
        self.assertFalse(
            [tag for tag, _ in rendered.elements if tag.casefold() in {"img", "svg"}],
            "hostile image/vector nodes were created",
        )
        self.assertFalse(
            [
                (tag, name)
                for tag, attrs in rendered.elements
                for name, _ in attrs
                if name.casefold().startswith("on")
            ],
            "event-handler attributes were created",
        )
        script_attrs = [attrs for tag, attrs in rendered.elements if tag.casefold() == "script"]
        self.assertEqual(len(script_attrs), 1)
        self.assertEqual(
            dict(script_attrs[0]),
            {"src": "/app.js?v=20260817-7", "defer": ""},
        )
        rendered_text = "".join(rendered.text)
        safe_attributes = " ".join(value or "" for _, attrs in rendered.elements for _, value in attrs)
        for sentinel in sentinels:
            self.assertTrue(
                sentinel in rendered_text or sentinel in safe_attributes,
                f"sentinel {sentinel!r} was not rendered through a safe DOM path",
            )

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
