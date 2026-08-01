"""HTTP access logging and request-scoped audit context."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from functools import wraps
from typing import Any, Callable, Dict, Optional

from starlette.requests import Request

from backend.core.runtime.access import (
    COOKIE_NAME,
    access_cookie_payload,
    request_source,
    request_uses_https,
)
from backend.core.runtime.config import RuntimeConfig

from .logger import LogCategory, LogConfig, get_logger
from .context import (
    bind_request_context,
    clear_request_context,
    get_request_context,
    reset_request_context,
    set_request_context,
    update_request_context,
)


_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]+")
def _safe_text(value: Any, limit: int) -> str:
    text = _CONTROL_CHARACTERS.sub(" ", str(value or "")).strip()
    return text[:limit]


def redact_sensitive_path(path: str) -> str:
    path = _safe_text(path, 2048)
    if "/grade-tracking/recovery/" not in path:
        return path
    prefix, _, suffix = path.partition("/grade-tracking/recovery/")
    trailing = suffix.split("/", 1)[1] if "/" in suffix else ""
    redacted = f"{prefix}/grade-tracking/recovery/<redacted>"
    return f"{redacted}/{trailing}" if trailing else redacted


class AccessLogger:
    """Write one structured, searchable record for every HTTP request."""

    def __init__(self, config: Optional[LogConfig] = None):
        self.logger = get_logger("access", LogCategory.ACCESS, config)

    def log_request(
        self,
        method: str,
        path: str,
        client_ip: str,
        user_agent: str,
        status_code: int,
        response_time_ms: float,
        user_id: Optional[str] = None,
        request_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        log_data = {
            "event": "http_access",
            "request_id": request_id or str(uuid.uuid4())[:12],
            "method": _safe_text(method, 16),
            "path": redact_sensitive_path(path),
            "status_code": int(status_code),
            "response_time_ms": round(response_time_ms, 2),
            "client_ip": _safe_text(client_ip, 64),
            "user_agent": _safe_text(user_agent, 512),
            "user_id": _safe_text(user_id, 128) or None,
        }
        if extra:
            for key in {
                "peer_ip",
                "scheme",
                "gateway_state",
                "response_size_bytes",
                "access_session_id",
            }:
                if key in extra:
                    value = extra[key]
                    if value is None:
                        log_data[key] = None
                    elif key == "response_size_bytes":
                        log_data[key] = int(value)
                    else:
                        log_data[key] = _safe_text(value, 128)

        level = logging.INFO
        if status_code >= 500:
            level = logging.ERROR
        elif status_code >= 400:
            level = logging.WARNING
        self.logger.log(
            level,
            "HTTP %s",
            json.dumps(log_data, ensure_ascii=False, separators=(",", ":")),
            extra={"extra": log_data},
        )


class FastAPILogMiddleware:
    """Outermost ASGI middleware for access and unhandled-error auditing."""

    def __init__(
        self,
        app,
        config: Optional[LogConfig] = None,
        runtime_config: Optional[RuntimeConfig] = None,
        user_provider: Optional[Callable[[], Optional[str]]] = None,
    ):
        self.app = app
        self.access_logger = AccessLogger(config)
        self.system_logger = get_logger("middleware", LogCategory.SYSTEM, config)
        self.runtime_config = runtime_config
        self.user_provider = user_provider

    def _current_user(self) -> Optional[str]:
        if self.user_provider is None:
            return None
        try:
            return _safe_text(self.user_provider(), 128) or None
        except Exception:
            return None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started_at = time.monotonic()
        request_id = uuid.uuid4().hex[:12]
        method = _safe_text(scope.get("method", ""), 16)
        path = redact_sensitive_path(scope.get("path", ""))
        headers = dict(scope.get("headers", []))
        peer_ip = _safe_text(scope.get("client", ("unknown", 0))[0], 64)
        user_agent = _safe_text(
            headers.get(b"user-agent", b"").decode("utf-8", errors="ignore"),
            512,
        )
        request = Request(scope)
        client_ip = (
            request_source(request, self.runtime_config)
            if self.runtime_config is not None
            else peer_ip
        )
        session_user = self._current_user()
        gateway_state = "not_required"
        access_session_id = None
        scheme = scope.get("scheme", "http")
        if self.runtime_config is not None:
            scheme = "https" if request_uses_https(request, self.runtime_config) else "http"
            if self.runtime_config.access_gateway_enabled:
                token = request.cookies.get(COOKIE_NAME, "")
                cookie_payload = access_cookie_payload(
                    token,
                    self.runtime_config.session_secret,
                )
                gateway_state = "authenticated" if cookie_payload else "anonymous"
                access_session_id = (
                    cookie_payload.get("session_id") if cookie_payload else None
                )

        context_token = bind_request_context(
            request_id=request_id,
            client_ip=client_ip,
            peer_ip=peer_ip,
            user_agent=user_agent,
            method=method,
            path=path,
            scheme=scheme,
            gateway_state=gateway_state,
            access_session_id=access_session_id,
            session_user=session_user,
        )
        status_code = 500
        response_size_bytes = 0

        async def wrapped_send(message):
            nonlocal response_size_bytes, status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
            elif message["type"] == "http.response.body":
                response_size_bytes += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, wrapped_send)
        except Exception:
            error_context = get_request_context()
            error_context.update(
                {
                    "event": "unhandled_request_error",
                    "status_code": 500,
                    "session_user": self._current_user() or session_user,
                }
            )
            self.system_logger.exception(
                "Unhandled HTTP request %s",
                json.dumps(error_context, ensure_ascii=False, separators=(",", ":")),
                extra={"extra": error_context},
            )
            raise
        finally:
            elapsed = (time.monotonic() - started_at) * 1000
            self.access_logger.log_request(
                method=method,
                path=path,
                client_ip=client_ip,
                user_agent=user_agent,
                status_code=status_code,
                response_time_ms=elapsed,
                user_id=self._current_user() or session_user,
                request_id=request_id,
                extra={
                    "peer_ip": peer_ip,
                    "scheme": scheme,
                    "gateway_state": gateway_state,
                    "access_session_id": access_session_id,
                    "response_size_bytes": response_size_bytes,
                },
            )
            reset_request_context(context_token)


def log_api_call(func: Callable) -> Callable:
    """Compatibility decorator for non-HTTP service calls."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        started_at = time.monotonic()
        logger = get_logger("api_call", LogCategory.ACCESS)
        try:
            result = func(*args, **kwargs)
            logger.info(
                "CALL %s status=success duration_ms=%.2f",
                func.__qualname__,
                (time.monotonic() - started_at) * 1000,
            )
            return result
        except Exception:
            logger.exception(
                "CALL %s status=error duration_ms=%.2f",
                func.__qualname__,
                (time.monotonic() - started_at) * 1000,
            )
            raise

    return wrapper
