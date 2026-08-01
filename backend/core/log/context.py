"""Async-safe request context shared by all log categories."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Dict


_request_context: ContextVar[Dict[str, Any] | None] = ContextVar(
    "neu_request_context",
    default=None,
)


def bind_request_context(**values: Any) -> Token:
    return _request_context.set(dict(values))


def update_request_context(**values: Any) -> None:
    context = dict(_request_context.get() or {})
    context.update(values)
    _request_context.set(context)


def set_request_context(**values: Any) -> None:
    update_request_context(**values)


def get_request_context() -> Dict[str, Any]:
    return dict(_request_context.get() or {})


def reset_request_context(token: Token) -> None:
    _request_context.reset(token)


def clear_request_context() -> None:
    _request_context.set(None)
