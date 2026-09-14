"""Low-frequency primary-session verification and automatic recovery."""

from __future__ import annotations

import threading
from typing import Any, Callable

from backend.core.auth.session_manager import RemoteSessionQueueTimeout


class AuthKeepaliveService:
    """Drive the shared recovery chain without blocking browser status calls."""

    def __init__(
        self,
        *,
        recover: Callable[..., Any],
        should_attempt: Callable[[], bool],
        recovery_status: Callable[[], dict[str, Any]],
        logger: Any,
        interval_seconds: float = 90.0,
    ) -> None:
        self._recover = recover
        self._should_attempt = should_attempt
        self._recovery_status = recovery_status
        self._logger = logger
        self._interval_seconds = max(15.0, float(interval_seconds))
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._wake.set()
            self._thread = threading.Thread(
                target=self._run,
                name="auth-keepalive",
                daemon=True,
            )
            self._thread.start()

    def wake(self) -> None:
        """Request a prompt recovery pass; multiple callers collapse together."""
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout=3)
        with self._lock:
            if self._thread is thread and not thread.is_alive():
                self._thread = None

    def _next_delay(self) -> float:
        state = self._recovery_status()
        status = str(state.get("status") or "idle")
        if status == "retry_wait":
            return min(30.0, max(1.0, float(state.get("retry_after_seconds") or 1)))
        if status in {"interaction_required", "credentials_invalid", "manual_required"}:
            return 60.0
        return self._interval_seconds

    def _run(self) -> None:
        delay = 0.25
        while not self._stop.is_set():
            self._wake.wait(timeout=delay)
            self._wake.clear()
            if self._stop.is_set():
                break
            if not self._should_attempt():
                delay = self._interval_seconds
                continue
            try:
                self._recover(queue_timeout=2.0)
            except RemoteSessionQueueTimeout:
                delay = 2.0
                continue
            except Exception as error:
                self._logger.warning(
                    "[Auth] 后台保活检查失败: %s", type(error).__name__,
                )
            delay = self._next_delay()
