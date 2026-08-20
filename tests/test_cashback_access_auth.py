from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "cashback-control"))

from access_auth import (  # noqa: E402
    AccessConfigurationError,
    AccessSettings,
    AccessVerificationError,
    CloudflareAccessVerifier,
    build_access_verifier,
    local_access_exemption,
)


ROOT = Path(__file__).resolve().parents[1]


def _public_jwk(private_key: Any, kid: str) -> dict[str, Any]:
    key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    key.update({"kid": kid, "alg": "RS256", "use": "sig", "key_ops": ["verify"]})
    return key


class _JWKSHandler(BaseHTTPRequestHandler):
    payload: dict[str, Any] = {"keys": []}

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class CloudflareAccessVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.issuer = "https://finance.cloudflareaccess.com"
        self.audience = "cashback-audience"
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.jwk = _public_jwk(self.private_key, "current")
        self.settings = AccessSettings(self.issuer, self.audience, "https://example.test/certs")

    def _token(self, **claims: Any) -> str:
        payload = {
            "iss": self.issuer,
            "aud": self.audience,
            "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()),
            "sub": "user-1",
        }
        payload.update(claims)
        return jwt.encode(
            payload,
            self.private_key,
            algorithm="RS256",
            headers={"kid": "current", "typ": "JWT"},
        )

    def _verifier(self, payload: dict[str, Any] | None = None) -> CloudflareAccessVerifier:
        return CloudflareAccessVerifier(
            self.settings,
            fetch_jwks=lambda *_: payload or {"keys": [self.jwk]},
        )

    def test_valid_assertion_requires_exact_claims_and_signature(self) -> None:
        verifier = self._verifier()
        self.assertEqual(verifier.verify(self._token())["sub"], "user-1")
        cases = (
            ("missing", None),
            ("forged", "not-a-jwt"),
            ("issuer", self._token(iss="https://other.cloudflareaccess.com")),
            ("audience", self._token(aud="other-audience")),
            ("expired", self._token(exp=int(time.time()) - 1)),
            (
                "wrong-key",
                jwt.encode(
                    {
                        "iss": self.issuer,
                        "aud": self.audience,
                        "exp": int(time.time()) + 300,
                    },
                    self.other_key,
                    algorithm="RS256",
                    headers={"kid": "current"},
                ),
            ),
        )
        for name, assertion in cases:
            with self.subTest(name=name), self.assertRaises(AccessVerificationError):
                verifier.verify(assertion)

    def test_only_asymmetric_algorithms_are_accepted(self) -> None:
        verifier = self._verifier()
        forged = jwt.encode(
            {
                "iss": self.issuer,
                "aud": self.audience,
                "exp": int(time.time()) + 300,
            },
            "shared-secret-value-with-at-least-32-bytes",
            algorithm="HS256",
            headers={"kid": "current"},
        )
        with self.assertRaises(AccessVerificationError):
            verifier.verify(forged)

    def test_unknown_key_refreshes_once_for_rotation(self) -> None:
        rotated_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        rotated_jwk = _public_jwk(rotated_key, "rotated")
        responses = [{"keys": [self.jwk]}, {"keys": [rotated_jwk]}]
        calls: list[int] = []

        def fetch(*_: Any) -> dict[str, Any]:
            calls.append(1)
            return responses[min(len(calls) - 1, 1)]

        verifier = CloudflareAccessVerifier(self.settings, fetch_jwks=fetch)
        self.assertEqual(verifier.verify(self._token())["sub"], "user-1")
        rotated = jwt.encode(
            {
                "iss": self.issuer,
                "aud": self.audience,
                "exp": int(time.time()) + 300,
                "sub": "rotated-user",
            },
            rotated_key,
            algorithm="RS256",
            headers={"kid": "rotated"},
        )
        self.assertEqual(verifier.verify(rotated)["sub"], "rotated-user")
        self.assertEqual(len(calls), 2)

    def test_expired_cache_does_not_use_stale_key_after_refresh_failure(self) -> None:
        now = [100.0]

        def fetch(*_: Any) -> dict[str, Any]:
            if now[0] > 100:
                raise AccessVerificationError("network failure")
            return {"keys": [self.jwk]}

        verifier = CloudflareAccessVerifier(
            AccessSettings(
                self.issuer,
                self.audience,
                self.settings.jwks_url,
                cache_ttl_seconds=10,
            ),
            fetch_jwks=fetch,
            clock=lambda: now[0],
        )
        self.assertEqual(verifier.verify(self._token())["sub"], "user-1")
        now[0] = 111
        with self.assertRaises(AccessVerificationError):
            verifier.verify(self._token())

    def test_public_mode_requires_all_access_settings(self) -> None:
        with self.assertRaises(AccessConfigurationError):
            build_access_verifier(
                bind_host="0.0.0.0",
                public_url="https://cashback.example",
                environ={"CASHBACK_ACCESS_ISSUER": "https://access.example"},
            )
        self.assertIsNone(
            build_access_verifier(
                bind_host="127.0.0.1",
                public_url="http://127.0.0.1:5010",
                environ={},
            )
        )

    def test_local_exemption_requires_loopback_bind_client_and_public_url(self) -> None:
        self.assertTrue(local_access_exemption("127.0.0.1", "127.0.0.1", "http://127.0.0.1:5010"))
        self.assertFalse(local_access_exemption("0.0.0.0", "127.0.0.1", "http://127.0.0.1:5010"))
        self.assertFalse(local_access_exemption("127.0.0.1", "10.0.0.8", "http://127.0.0.1:5010"))
        self.assertFalse(local_access_exemption("127.0.0.1", "127.0.0.1", "https://cashback.example"))


class BrowserMutationServerTests(unittest.TestCase):
    issuer = "https://finance.cloudflareaccess.com"
    audience = "cashback-audience"

    def setUp(self) -> None:
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        _JWKSHandler.payload = {"keys": [_public_jwk(self.private_key, "current")]}
        self.jwks_server = ThreadingHTTPServer(("127.0.0.1", 0), _JWKSHandler)
        self.jwks_thread = threading.Thread(target=self.jwks_server.serve_forever, daemon=True)
        self.jwks_thread.start()
        self.jwks_url = f"http://127.0.0.1:{self.jwks_server.server_port}/certs"

    def tearDown(self) -> None:
        self.jwks_server.shutdown()
        self.jwks_server.server_close()
        self.jwks_thread.join(timeout=5)

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return listener.getsockname()[1]

    def _token(self, key: Any | None = None, kid: str = "current", **claims: Any) -> str:
        payload = {
            "iss": self.issuer,
            "aud": self.audience,
            "exp": int(time.time()) + 300,
            "sub": "user-1",
        }
        payload.update(claims)
        return jwt.encode(payload, key or self.private_key, algorithm="RS256", headers={"kid": kid})

    def _start_app(self, temporary: str, port: int) -> subprocess.Popen[str]:
        env = os.environ.copy()
        env.update({
            "CASHBACK_HOST": "0.0.0.0",
            "CASHBACK_PORT": str(port),
            "CASHBACK_DB_PATH": str(Path(temporary) / "events.sqlite3"),
            "CASHBACK_DASHBOARD_PATH": str(Path(temporary) / "dashboard.json"),
            "CASHBACK_INGEST_TOKEN": "machine-token",
            "CASHBACK_PUBLIC_URL": "https://cashback.example",
            "CASHBACK_ACCESS_ISSUER": self.issuer,
            "CASHBACK_ACCESS_AUDIENCE": self.audience,
            "CASHBACK_ACCESS_JWKS_URL": self.jwks_url,
            "CASHBACK_REFRESH_SECONDS": "0",
        })
        return subprocess.Popen(
            [sys.executable, str(ROOT / "apps" / "cashback-control" / "server.py")],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

    @staticmethod
    def _request(
        port: int,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        payload: object | None = None,
    ) -> int:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=body,
            headers={"Host": "cashback.example", **(headers or {})},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                response.read()
                return response.status
        except urllib.error.HTTPError as error:
            error.close()
            return error.code

    @staticmethod
    def _counts(database: Path) -> tuple[int, int]:
        with sqlite3.connect(database) as connection:
            return tuple(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in ("alert_acknowledgements", "push_subscriptions")
            )  # type: ignore[return-value]

    def test_browser_gate_denies_invalid_requests_before_any_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            port = self._free_port()
            process = self._start_app(temporary, port)
            database = Path(temporary) / "events.sqlite3"
            try:
                health_url = f"http://127.0.0.1:{port}/api/health"
                for _ in range(50):
                    try:
                        with urllib.request.urlopen(health_url, timeout=0.2):
                            break
                    except (OSError, urllib.error.URLError):
                        time.sleep(0.05)
                else:
                    self.fail("Cashback server did not become ready")

                origin = "https://cashback.example"
                body = {"alert_key": "alert-1", "acknowledged": True}
                valid = self._token()
                denied = (
                    ({"Content-Type": "application/json", "Origin": origin}, "missing"),
                    ({"Content-Type": "application/json", "Origin": origin, "Authorization": "Bearer machine-token"}, "bearer"),
                    ({"Content-Type": "application/json", "Origin": "http://127.0.0.1:5010", "Cf-Access-Jwt-Assertion": valid}, "direct-origin"),
                    ({"Host": "127.0.0.1:%d" % port, "Content-Type": "application/json", "Origin": origin, "Cf-Access-Jwt-Assertion": valid}, "spoofed-host"),
                    ({"Content-Type": "application/json", "Origin": origin, "Cf-Access-Jwt-Assertion": "forged"}, "forged"),
                    ({"Content-Type": "application/json", "Origin": origin, "Cf-Access-Jwt-Assertion": self._token(iss="https://other.example")}, "issuer"),
                    ({"Content-Type": "application/json", "Origin": origin, "Cf-Access-Jwt-Assertion": self._token(aud="other")}, "audience"),
                    ({"Content-Type": "application/json", "Origin": origin, "Cf-Access-Jwt-Assertion": self._token(exp=int(time.time()) - 1)}, "expired"),
                    ({"Content-Type": "application/json", "Origin": origin, "Cf-Access-Jwt-Assertion": self._token(key=rsa.generate_private_key(public_exponent=65537, key_size=2048))}, "key"),
                )
                for headers, name in denied:
                    with self.subTest(name=name):
                        self.assertEqual(self._request(port, "/api/alerts/ack", headers=headers, payload=body), 403)
                        self.assertEqual(self._counts(database), (0, 0))

                push = {
                    "action": "subscribe",
                    "subscription": {"endpoint": "https://push.example/1", "keys": {"p256dh": "x", "auth": "y"}},
                }
                self.assertEqual(
                    self._request(
                        port,
                        "/api/push/subscriptions",
                        headers={"Content-Type": "application/json", "Origin": origin, "Cf-Access-Jwt-Assertion": "forged"},
                        payload=push,
                    ),
                    403,
                )
                self.assertEqual(self._counts(database), (0, 0))

                self.assertEqual(
                    self._request(
                        port,
                        "/api/alerts/ack",
                        headers={"Content-Type": "application/json", "Origin": origin, "Cf-Access-Jwt-Assertion": valid},
                        payload=body,
                    ),
                    200,
                )
                self.assertEqual(self._counts(database), (1, 0))
                self.assertEqual(
                    self._request(
                        port,
                        "/api/push/subscriptions",
                        headers={"Content-Type": "application/json", "Origin": origin, "Cf-Access-Jwt-Assertion": valid},
                        payload={"action": "unsubscribe", "subscription": {"endpoint": "https://push.example/1"}},
                    ),
                    200,
                )
                self.assertEqual(self._counts(database), (1, 0))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def test_public_mode_without_access_settings_exits_before_serving(self) -> None:
        env = os.environ.copy()
        env.update({
            "CASHBACK_HOST": "0.0.0.0",
            "CASHBACK_PORT": str(self._free_port()),
            "CASHBACK_PUBLIC_URL": "https://cashback.example",
        })
        for name in (
            "CASHBACK_ACCESS_ISSUER",
            "CASHBACK_ACCESS_AUDIENCE",
            "CASHBACK_ACCESS_JWKS_URL",
        ):
            env.pop(name, None)
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "apps" / "cashback-control" / "server.py")],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            self.assertNotEqual(process.wait(timeout=5), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
