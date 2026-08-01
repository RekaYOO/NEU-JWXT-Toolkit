"""Structured security and authentication event logging."""

from __future__ import annotations

import json
import logging
import re
import traceback
from pathlib import Path
from typing import Any, Optional

from .context import get_request_context
from .logger import LogCategory, get_logger


_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]+")
_ALLOWED_FIELDS = {
    "auth_method",
    "clear_data",
    "error_code",
    "error_type",
    "network_mode",
    "remember",
    "trust_device",
}


def _safe_text(value: Any, limit: int = 128) -> str:
    return _CONTROL_CHARACTERS.sub(" ", str(value or "")).strip()[:limit]


def log_security_event(
    event: str,
    outcome: str,
    *,
    subject: Optional[str] = None,
    reason: Optional[str] = None,
    **details: Any,
) -> None:
    """Record an auth event without accepting secret-bearing arbitrary fields."""
    request_context = get_request_context()
    log_data = {
        "event": _safe_text(event, 64),
        "outcome": _safe_text(outcome, 32),
        "request_id": request_context.get("request_id"),
        "client_ip": request_context.get("client_ip", "unknown"),
        "peer_ip": request_context.get("peer_ip", "unknown"),
        "access_session_id": request_context.get("access_session_id"),
        "user_agent": request_context.get("user_agent", ""),
        "subject": _safe_text(subject) or None,
        "reason": _safe_text(reason, 256) or None,
    }
    for key in _ALLOWED_FIELDS:
        if key in details:
            value = details[key]
            log_data[key] = value if isinstance(value, bool) else _safe_text(value)

    level = logging.INFO
    if outcome in {"failure", "blocked", "denied"}:
        level = logging.WARNING
    elif outcome == "error":
        level = logging.ERROR
    logger = get_logger("security", LogCategory.LOGIN)
    logger.log(
        level,
        "AUTH %s",
        json.dumps(log_data, ensure_ascii=False, separators=(",", ":")),
        extra={"extra": log_data},
    )


def log_application_error(component: str, error: BaseException, status_code: int) -> str:
    """Persist a safe error reference while keeping exception details off clients."""
    request_context = get_request_context()
    error_id = _safe_text(request_context.get("request_id"), 64)
    if not error_id:
        import uuid
        error_id = uuid.uuid4().hex[:12]
    payload = {
        "event": "application_error",
        "error_id": error_id,
        "component": _safe_text(component, 96),
        "error_type": type(error).__name__,
        "status_code": int(status_code),
        "request_id": request_context.get("request_id"),
        "client_ip": request_context.get("client_ip", "unknown"),
        "peer_ip": request_context.get("peer_ip", "unknown"),
        "session_user": _safe_text(request_context.get("session_user"), 128) or None,
        "user_agent": _safe_text(request_context.get("user_agent"), 512),
        "trace": [
            {
                "file": Path(frame.filename).name,
                "line": frame.lineno,
                "function": _safe_text(frame.name, 128),
            }
            for frame in traceback.extract_tb(error.__traceback__)[-12:]
        ],
    }
    get_logger("application", LogCategory.ERROR).error(
        "ERROR %s",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        extra={"extra": payload},
    )
    return error_id
