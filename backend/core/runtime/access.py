"""Minimal single-user access gateway for the Linux server profile."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .config import RuntimeConfig


COOKIE_NAME = "neu_jwxt_access"
COOKIE_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 5 * 60


def hash_access_password(password: str, salt: bytes | None = None) -> dict[str, str]:
    if len(password) < 8:
        raise ValueError("访问密码至少需要 8 个字符")
    salt = salt or __import__("secrets").token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return {
        "salt": base64.urlsafe_b64encode(salt).decode("ascii"),
        "hash": base64.urlsafe_b64encode(derived).decode("ascii"),
    }


def verify_access_password(password: str, salt_text: str, hash_text: str) -> bool:
    try:
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(hash_text.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_access_cookie(secret: str, now: int | None = None) -> str:
    payload = json.dumps(
        {"issued_at": int(now or time.time())},
        separators=(",", ":"),
    ).encode("utf-8")
    payload_text = _encode(payload)
    signature = hmac.new(secret.encode("utf-8"), payload_text.encode("ascii"), hashlib.sha256)
    return f"{payload_text}.{_encode(signature.digest())}"


def validate_access_cookie(token: str, secret: str, now: int | None = None) -> bool:
    if not token or not secret or "." not in token:
        return False
    try:
        payload_text, signature_text = token.split(".", 1)
        expected = hmac.new(
            secret.encode("utf-8"), payload_text.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _decode(signature_text)):
            return False
        payload = json.loads(_decode(payload_text))
        issued_at = int(payload["issued_at"])
        current = int(now or time.time())
        return 0 <= current - issued_at <= COOKIE_TTL_SECONDS
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def request_uses_https(request: Request, config: RuntimeConfig) -> bool:
    if request.url.scheme == "https":
        return True
    peer = request.client.host if request.client else ""
    if peer in config.trusted_proxies:
        return request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"
    return False


def request_source(request: Request, config: RuntimeConfig) -> str:
    peer = request.client.host if request.client else "unknown"
    if peer in config.trusted_proxies:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer


@dataclass
class LoginRateLimiter:
    failures: dict[str, Deque[float]]

    def __init__(self) -> None:
        self.failures = defaultdict(deque)

    def _prune(self, source: str, now: float) -> Deque[float]:
        values = self.failures[source]
        while values and now - values[0] > FAILURE_WINDOW_SECONDS:
            values.popleft()
        return values

    def is_blocked(self, source: str, now: float | None = None) -> bool:
        current = now or time.time()
        return len(self._prune(source, current)) >= MAX_FAILURES

    def register_failure(self, source: str, now: float | None = None) -> None:
        current = now or time.time()
        self._prune(source, current).append(current)

    def clear(self, source: str) -> None:
        self.failures.pop(source, None)


PUBLIC_API_PATHS = {
    "/api/health",
    "/api/access/status",
    "/api/access/login",
    "/api/access/logout",
}


def is_public_api_path(path: str) -> bool:
    return (
        path in PUBLIC_API_PATHS
        or path.startswith("/api/grade-tracking/recovery/")
    )


class AccessGatewayMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, config: RuntimeConfig):
        super().__init__(app)
        self.config = config

    async def dispatch(self, request: Request, call_next):
        if (
            not self.config.access_gateway_enabled
            or not request.url.path.startswith("/api/")
            or is_public_api_path(request.url.path)
        ):
            return await call_next(request)

        token = request.cookies.get(COOKIE_NAME, "")
        if validate_access_cookie(token, self.config.session_secret):
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"detail": "需要先验证服务器访问密码", "code": "ACCESS_REQUIRED"},
        )
