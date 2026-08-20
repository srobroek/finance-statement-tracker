"""Cloudflare Access verification for browser-only Cashback mutations."""

from __future__ import annotations

import ipaddress
import json
import os
import threading
import time
from dataclasses import dataclass
from http.client import HTTPResponse
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import jwt


ACCESS_ASSERTION_HEADER = "Cf-Access-Jwt-Assertion"
ALLOWED_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"})
DEFAULT_CACHE_TTL_SECONDS = 300.0
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_JWKS_BYTES = 1_048_576
MAX_JWKS_KEYS = 64


class AccessConfigurationError(RuntimeError):
    """Raised when a public deployment cannot be safely configured."""


class AccessVerificationError(RuntimeError):
    """Raised when a request does not carry a valid Access assertion."""


def _loopback_host(value: str) -> bool:
    host = value.strip().lower().strip("[]")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _valid_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def is_loopback_public_url(value: str) -> bool:
    """Return whether a configured origin is a complete loopback URL."""
    if not _valid_origin(value):
        return False
    try:
        return _loopback_host(str(urlsplit(value).hostname))
    except ValueError:
        return False


def local_access_exemption(bind_host: str, client_host: str, public_url: str) -> bool:
    """Allow JWT-free browser calls only for an entirely loopback service."""
    return (
        _loopback_host(bind_host)
        and _loopback_host(client_host)
        and is_loopback_public_url(public_url)
    )


def _positive_float(value: str, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise AccessConfigurationError(f"{name} must be a positive number") from error
    if result <= 0:
        raise AccessConfigurationError(f"{name} must be a positive number")
    return result


def _positive_int(value: str, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise AccessConfigurationError(f"{name} must be a positive integer") from error
    if result <= 0:
        raise AccessConfigurationError(f"{name} must be a positive integer")
    return result


@dataclass(frozen=True)
class AccessSettings:
    issuer: str
    audience: str
    jwks_url: str
    cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_jwks_bytes: int = DEFAULT_MAX_JWKS_BYTES

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "AccessSettings":
        source = os.environ if environ is None else environ
        issuer = source.get("CASHBACK_ACCESS_ISSUER", "").strip()
        audience = source.get("CASHBACK_ACCESS_AUDIENCE", "").strip()
        jwks_url = source.get("CASHBACK_ACCESS_JWKS_URL", "").strip()
        if not issuer or not audience or not jwks_url:
            raise AccessConfigurationError(
                "CASHBACK_ACCESS_ISSUER, CASHBACK_ACCESS_AUDIENCE, and "
                "CASHBACK_ACCESS_JWKS_URL are required"
            )
        if not _valid_origin(issuer) or urlsplit(issuer).path:
            raise AccessConfigurationError("CASHBACK_ACCESS_ISSUER must be an origin URL")
        try:
            jwks_parts = urlsplit(jwks_url)
            if (
                jwks_parts.scheme not in {"http", "https"}
                or not jwks_parts.netloc
                or jwks_parts.username
                or jwks_parts.password
                or jwks_parts.fragment
            ):
                raise ValueError
        except ValueError as error:
            raise AccessConfigurationError("CASHBACK_ACCESS_JWKS_URL must be an HTTP(S) URL") from error
        return cls(
            issuer=issuer,
            audience=audience,
            jwks_url=jwks_url,
            cache_ttl_seconds=_positive_float(
                source.get("CASHBACK_ACCESS_JWKS_CACHE_SECONDS", str(DEFAULT_CACHE_TTL_SECONDS)),
                "CASHBACK_ACCESS_JWKS_CACHE_SECONDS",
            ),
            timeout_seconds=_positive_float(
                source.get("CASHBACK_ACCESS_JWKS_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)),
                "CASHBACK_ACCESS_JWKS_TIMEOUT_SECONDS",
            ),
            max_jwks_bytes=_positive_int(
                source.get("CASHBACK_ACCESS_JWKS_MAX_BYTES", str(DEFAULT_MAX_JWKS_BYTES)),
                "CASHBACK_ACCESS_JWKS_MAX_BYTES",
            ),
        )


def build_access_verifier(
    *, bind_host: str, public_url: str, environ: Mapping[str, str] | None = None
) -> "CloudflareAccessVerifier | None":
    """Build the verifier, permitting omission only for an entirely local service."""
    if not _valid_origin(public_url):
        raise AccessConfigurationError("CASHBACK_PUBLIC_URL must be an origin URL")
    source = os.environ if environ is None else environ
    configured = any(source.get(name, "").strip() for name in (
        "CASHBACK_ACCESS_ISSUER",
        "CASHBACK_ACCESS_AUDIENCE",
        "CASHBACK_ACCESS_JWKS_URL",
    ))
    if not configured and local_access_exemption(bind_host, "127.0.0.1", public_url):
        return None
    return CloudflareAccessVerifier(AccessSettings.from_environment(source))


def _default_fetch_jwks(url: str, timeout_seconds: float, max_bytes: int) -> Mapping[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        response: HTTPResponse
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(max_bytes + 1)
    except Exception as error:
        raise AccessVerificationError("Cloudflare Access JWKS is unavailable") from error
    if len(body) > max_bytes:
        raise AccessVerificationError("Cloudflare Access JWKS is too large")
    try:
        payload = json.loads(body)
    except (TypeError, json.JSONDecodeError) as error:
        raise AccessVerificationError("Cloudflare Access JWKS is invalid") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
        raise AccessVerificationError("Cloudflare Access JWKS is invalid")
    return payload


class CloudflareAccessVerifier:
    """Verify Access assertions against a bounded, rotatable JWKS cache."""

    def __init__(
        self,
        settings: AccessSettings,
        *,
        fetch_jwks: Callable[[str, float, int], Mapping[str, Any]] = _default_fetch_jwks,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._fetch_jwks = fetch_jwks
        self._clock = clock
        self._cache: dict[str, dict[str, Any]] | None = None
        self._cached_at = 0.0
        self._lock = threading.Lock()

    def _load_locked(self) -> None:
        payload = self._fetch_jwks(
            self.settings.jwks_url,
            self.settings.timeout_seconds,
            self.settings.max_jwks_bytes,
        )
        raw_keys = payload.get("keys")
        if not isinstance(raw_keys, list) or len(raw_keys) > MAX_JWKS_KEYS:
            raise AccessVerificationError("Cloudflare Access JWKS is invalid")
        keys: dict[str, dict[str, Any]] = {}
        for candidate in raw_keys:
            if not isinstance(candidate, dict):
                continue
            kid = candidate.get("kid")
            if not isinstance(kid, str) or not kid or len(kid) > 256:
                continue
            if candidate.get("kty") not in {"RSA", "EC"}:
                continue
            if candidate.get("use") not in {None, "sig"}:
                continue
            key_ops = candidate.get("key_ops")
            if key_ops is not None and (not isinstance(key_ops, list) or "verify" not in key_ops):
                continue
            key_alg = candidate.get("alg")
            if key_alg is not None and key_alg not in ALLOWED_ALGORITHMS:
                continue
            keys[kid] = candidate
        if not keys:
            raise AccessVerificationError("Cloudflare Access JWKS contains no usable keys")
        self._cache = keys
        self._cached_at = self._clock()

    def _key_for(self, kid: str, algorithm: str) -> Any:
        with self._lock:
            cache_expired = (
                self._cache is None
                or self._clock() - self._cached_at >= self.settings.cache_ttl_seconds
            )
            if cache_expired:
                self._load_locked()
            assert self._cache is not None
            candidate = self._cache.get(kid)
            if candidate is None:
                # One bounded refresh handles a rotated Access signing key.
                self._load_locked()
                candidate = self._cache.get(kid)
            if candidate is None or candidate.get("alg") not in {None, algorithm}:
                raise AccessVerificationError("Cloudflare Access signing key is not trusted")
            try:
                jwk = jwt.PyJWK.from_dict(candidate, algorithm=algorithm)
            except Exception as error:
                raise AccessVerificationError("Cloudflare Access signing key is invalid") from error
            if jwk.algorithm_name != algorithm:
                raise AccessVerificationError("Cloudflare Access algorithm is not trusted")
            return jwk.key

    def verify(self, assertion: str | None) -> dict[str, Any]:
        """Return claims only after strict header, key, signature, and claim checks."""
        if not assertion or len(assertion) > 32_768:
            raise AccessVerificationError("Cloudflare Access assertion is missing")
        try:
            header = jwt.get_unverified_header(assertion)
            algorithm = header.get("alg")
            kid = header.get("kid")
            if algorithm not in ALLOWED_ALGORITHMS or not isinstance(kid, str) or not kid:
                raise AccessVerificationError("Cloudflare Access assertion header is invalid")
            key = self._key_for(kid, algorithm)
            claims = jwt.decode(
                assertion,
                key=key,
                algorithms=[algorithm],
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                options={
                    "require": ["exp", "iss", "aud"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
                leeway=0,
            )
        except AccessVerificationError:
            raise
        except Exception as error:
            raise AccessVerificationError("Cloudflare Access assertion is invalid") from error
        if not isinstance(claims, dict):
            raise AccessVerificationError("Cloudflare Access claims are invalid")
        audience = claims.get("aud")
        if isinstance(audience, str):
            audience_values = [audience]
        elif isinstance(audience, list) and all(isinstance(value, str) for value in audience):
            audience_values = audience
        else:
            raise AccessVerificationError("Cloudflare Access audience is invalid")
        if audience_values != [self.settings.audience]:
            raise AccessVerificationError("Cloudflare Access audience is not trusted")
        return claims
