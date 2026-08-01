"""Minimal single-user access gateway for the Linux server profile."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .config import RuntimeConfig


COOKIE_NAME = "neu_jwxt_access"
COOKIE_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 5 * 60
MAX_TRACKED_SOURCES = 4096
MAX_PASSWORD_LENGTH = 256
MAX_COOKIE_LENGTH = 2048


def hash_access_password(password: str, salt: bytes | None = None) -> dict[str, str]:
    if len(password) < 8:
        raise ValueError("访问密码至少需要 8 个字符")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"访问密码不能超过 {MAX_PASSWORD_LENGTH} 个字符")
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
    if not password or len(password) > MAX_PASSWORD_LENGTH:
        return False
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
        {
            "issued_at": int(now or time.time()),
            "session_id": secrets.token_urlsafe(12),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    payload_text = _encode(payload)
    signature = hmac.new(secret.encode("utf-8"), payload_text.encode("ascii"), hashlib.sha256)
    return f"{payload_text}.{_encode(signature.digest())}"


def access_cookie_payload(
    token: str,
    secret: str,
    now: int | None = None,
) -> dict[str, str | int] | None:
    if (
        not token
        or len(token) > MAX_COOKIE_LENGTH
        or not secret
        or "." not in token
    ):
        return None
    try:
        payload_text, signature_text = token.split(".", 1)
        expected = hmac.new(
            secret.encode("utf-8"), payload_text.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _decode(signature_text)):
            return None
        payload = json.loads(_decode(payload_text))
        issued_at = int(payload["issued_at"])
        current = int(now or time.time())
        session_id = str(payload.get("session_id") or "")
        if not session_id:
            session_id = "legacy-" + hashlib.sha256(token.encode("ascii")).hexdigest()[:16]
        if len(session_id) > 64:
            return None
        if not 0 <= current - issued_at <= COOKIE_TTL_SECONDS:
            return None
        return {"issued_at": issued_at, "session_id": session_id}
    except (
        ValueError,
        KeyError,
        TypeError,
        OverflowError,
        binascii.Error,
        json.JSONDecodeError,
    ):
        return None


def validate_access_cookie(token: str, secret: str, now: int | None = None) -> bool:
    return access_cookie_payload(token, secret, now) is not None


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
        forwarded = [
            item.strip()
            for item in request.headers.get("x-forwarded-for", "").split(",")
            if item.strip()
        ]
        # 从最靠近本服务的一端反向查找第一个非可信代理地址，避免客户端
        # 预置 X-Forwarded-For 左侧内容绕过限流。
        for candidate in reversed(forwarded):
            try:
                normalized = str(ipaddress.ip_address(candidate))
            except ValueError:
                continue
            if normalized not in config.trusted_proxies:
                return normalized
    try:
        return str(ipaddress.ip_address(peer))
    except ValueError:
        return str(peer)[:64] or "unknown"


class LoginRateLimiter:
    def __init__(self) -> None:
        self.failures: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune_unlocked(self, source: str, now: float) -> Deque[float]:
        values = self.failures[source]
        while values and now - values[0] > FAILURE_WINDOW_SECONDS:
            values.popleft()
        if not values:
            self.failures.pop(source, None)
            return deque()
        return values

    def is_blocked(self, source: str, now: float | None = None) -> bool:
        current = now or time.time()
        with self._lock:
            return len(self._prune_unlocked(source, current)) >= MAX_FAILURES

    def register_failure(self, source: str, now: float | None = None) -> None:
        current = now or time.time()
        with self._lock:
            if source not in self.failures and len(self.failures) >= MAX_TRACKED_SOURCES:
                for tracked_source in list(self.failures):
                    self._prune_unlocked(tracked_source, current)
                while len(self.failures) >= MAX_TRACKED_SOURCES:
                    self.failures.pop(next(iter(self.failures)))
            self.failures[source].append(current)

    def clear(self, source: str) -> None:
        with self._lock:
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
            response = await call_next(request)
        else:
            token = request.cookies.get(COOKIE_NAME, "")
            if validate_access_cookie(token, self.config.session_secret):
                response = await call_next(request)
            else:
                response = JSONResponse(
                    status_code=401,
                    content={"detail": "需要先验证服务器访问密码", "code": "ACCESS_REQUIRED"},
                )

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response
