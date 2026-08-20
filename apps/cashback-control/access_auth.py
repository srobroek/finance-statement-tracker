"""Cloudflare Access verification for browser-only Cashback mutations."""

from __future__ import annotations

import ipaddress
import json
import math
import os
from dataclasses import dataclass
from typing import Any, Mapping
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


def _secure_or_loopback_url(value: str) -> bool:
    """Require TLS except where a loopback endpoint is intentionally local."""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            return False
        return parsed.scheme == "https" or _loopback_host(str(parsed.hostname))
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
    if not math.isfinite(result) or result <= 0:
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

    def __post_init__(self) -> None:
        for value, name in (
            (self.cache_ttl_seconds, "cache TTL"),
            (self.timeout_seconds, "timeout"),
        ):
            if (
                not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise AccessConfigurationError(f"{name} must be a positive number")
        if self.max_jwks_bytes <= 0:
            raise AccessConfigurationError("JWKS maximum size must be a positive integer")

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
        if not _valid_origin(issuer) or not _secure_or_loopback_url(issuer):
            raise AccessConfigurationError("CASHBACK_ACCESS_ISSUER must be an origin URL")
        try:
            jwks_parts = urlsplit(jwks_url)
            if (
                jwks_parts.scheme not in {"http", "https"}
                or not jwks_parts.netloc
                or jwks_parts.username
                or jwks_parts.password
                or jwks_parts.fragment
                or not _secure_or_loopback_url(jwks_url)
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
    if not _valid_origin(public_url) or not _secure_or_loopback_url(public_url):
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


class BoundedPyJWKClient(jwt.PyJWKClient):
    """Use PyJWT's cache and rotation logic with bounded JWKS transport."""

    def __init__(
        self,
        uri: str,
        *,
        cache_ttl_seconds: float,
        timeout_seconds: float,
        max_jwks_bytes: int,
    ) -> None:
        self.max_jwks_bytes = max_jwks_bytes
        super().__init__(
            uri,
            cache_jwk_set=True,
            lifespan=cache_ttl_seconds,
            headers={"Accept": "application/json"},
            timeout=timeout_seconds,
        )

    def fetch_data(self) -> Any:
        """Fetch a bounded payload; PyJWT owns cache and one-refresh behavior."""
        request = Request(url=self.uri, headers=self.headers)
        try:
            with urlopen(request, timeout=self.timeout, context=self.ssl_context) as response:
                body = response.read(self.max_jwks_bytes + 1)
        except (OSError, TimeoutError) as error:
            raise jwt.PyJWKClientConnectionError(
                "Cloudflare Access JWKS is unavailable"
            ) from error
        if len(body) > self.max_jwks_bytes:
            raise jwt.PyJWKClientError("Cloudflare Access JWKS is too large")
        try:
            payload = json.loads(body)
        except (TypeError, json.JSONDecodeError) as error:
            raise jwt.PyJWKClientError("Cloudflare Access JWKS is invalid") from error
        keys = payload.get("keys") if isinstance(payload, dict) else None
        if not isinstance(keys, list) or not keys or len(keys) > MAX_JWKS_KEYS:
            raise jwt.PyJWKClientError("Cloudflare Access JWKS is invalid")
        if self.jwk_set_cache is not None:
            self.jwk_set_cache.put(payload)
        return payload


class CloudflareAccessVerifier:
    """Verify Access assertions through PyJWT's bounded, rotatable JWKS client."""

    def __init__(
        self,
        settings: AccessSettings,
        *,
        jwks_client: jwt.PyJWKClient | None = None,
    ) -> None:
        self.settings = settings
        self._jwks_client = jwks_client or BoundedPyJWKClient(
            settings.jwks_url,
            cache_ttl_seconds=settings.cache_ttl_seconds,
            timeout_seconds=settings.timeout_seconds,
            max_jwks_bytes=settings.max_jwks_bytes,
        )

    def verify(self, assertion: str | None) -> dict[str, Any]:
        """Return claims only after strict header, key, signature, and claim checks."""
        if not assertion or len(assertion) > 32_768:
            raise AccessVerificationError("Cloudflare Access assertion is missing")
        try:
            header = jwt.get_unverified_header(assertion)
            algorithm = header.get("alg")
            kid = header.get("kid")
            if (
                algorithm not in ALLOWED_ALGORITHMS
                or not isinstance(kid, str)
                or not kid
                or len(kid) > 256
            ):
                raise AccessVerificationError("Cloudflare Access assertion header is invalid")
            signing_key = self._jwks_client.get_signing_key_from_jwt(assertion)
            key_data = getattr(signing_key, "_jwk_data", {})
            key_ops = key_data.get("key_ops")
            if (
                signing_key.key_id != kid
                or signing_key.algorithm_name != algorithm
                or signing_key.algorithm_name not in ALLOWED_ALGORITHMS
                or signing_key.key_type not in {"RSA", "EC"}
                or signing_key.public_key_use not in {None, "sig"}
                or (
                    key_ops is not None
                    and (not isinstance(key_ops, list) or "verify" not in key_ops)
                )
            ):
                raise AccessVerificationError("Cloudflare Access algorithm is not trusted")
            claims = jwt.decode(
                assertion,
                key=signing_key.key,
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
