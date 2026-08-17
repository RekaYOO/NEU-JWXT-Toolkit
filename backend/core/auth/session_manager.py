"""Process-wide identity epoch and shared requests.Session coordination."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator


logger = logging.getLogger(__name__)

_REMOTE_PRIORITIES = {
    "mutation": 0,
    "foreground": 1,
    "background": 2,
}


class AuthSessionManager:
    """Own the active client, identity fencing and remote serialization."""

    def __init__(self) -> None:
        self._client: Any | None = None
        self._pending_client: Any | None = None
        self._identity_epoch = 0
        self._state_lock = threading.RLock()
        self._remote_condition = threading.Condition(threading.Lock())
        self._remote_active = False
        self._remote_ticket = 0
        self._remote_waiting: dict[str, list[int]] = {
            priority: [] for priority in _REMOTE_PRIORITIES
        }
        self._remote_foreground_streak = 0

    @contextmanager
    def remote_guard(
        self,
        *,
        priority: str = "foreground",
        label: str = "remote-operation",
    ) -> Iterator[dict[str, float | str]]:
        """Serialize the shared Session while letting user writes skip queued reads.

        The active request is never interrupted.  Once it releases the Session,
        mutations win over foreground reads, which win over background scans.
        After a bounded foreground streak, one background request may proceed so
        cache/archive work cannot starve forever.
        """
        if priority not in _REMOTE_PRIORITIES:
            raise ValueError(f"unknown remote priority: {priority}")
        started = time.monotonic()
        with self._remote_condition:
            ticket = self._remote_ticket
            self._remote_ticket += 1
            self._remote_waiting[priority].append(ticket)
            while self._remote_active or not self._remote_turn(priority, ticket):
                self._remote_condition.wait()
            self._remote_waiting[priority].pop(0)
            self._remote_active = True
            if priority == "foreground":
                self._remote_foreground_streak += 1
            elif priority == "background":
                self._remote_foreground_streak = 0
        wait_ms = round((time.monotonic() - started) * 1000, 1)
        if wait_ms >= 250:
            logger.info(
                "remote session acquired priority=%s label=%s queue_wait_ms=%.1f",
                priority, label, wait_ms,
            )
        try:
            yield {"priority": priority, "label": label, "queue_wait_ms": wait_ms}
        finally:
            with self._remote_condition:
                self._remote_active = False
                self._remote_condition.notify_all()

    def _remote_turn(self, priority: str, ticket: int) -> bool:
        queue = self._remote_waiting[priority]
        if not queue or queue[0] != ticket:
            return False
        if self._remote_waiting["mutation"]:
            return priority == "mutation"
        if (
            self._remote_waiting["background"]
            and self._remote_foreground_streak >= 8
        ):
            return priority == "background"
        if self._remote_waiting["foreground"]:
            return priority == "foreground"
        return priority == "background"

    @contextmanager
    def identity_commit_guard(self) -> Iterator[None]:
        with self._state_lock:
            yield

    @contextmanager
    def local_cache_import_guard(self, account: str) -> Iterator[None]:
        """Fence an account-bound legacy import that performs no remote work.

        Offline startup has no active NEU client. Import is allowed in that
        state, or when the active client belongs to the same account. Holding
        the identity lock makes the check and SQLite commit atomic with respect
        to login, logout and clear-data.
        """
        with self._state_lock:
            active_account = (
                str(getattr(self._client, "username", "") or "")
                if self._client is not None
                else ""
            )
            if active_account and active_account != str(account):
                raise RuntimeError("legacy cache belongs to another account")
            yield

    def set_client(
        self,
        client: Any | None,
        *,
        force_epoch: bool = False,
    ) -> None:
        with self._state_lock:
            if force_epoch or self._client is not client:
                self._identity_epoch += 1
            self._client = client

    def peek_client(self) -> Any | None:
        with self._state_lock:
            return self._client

    def set_pending_client(self, client: Any | None) -> None:
        """Store one interactive login candidate without replacing identity."""
        with self._state_lock:
            self._pending_client = client

    def peek_pending_client(self) -> Any | None:
        with self._state_lock:
            return self._pending_client

    def clear_pending_client(self, expected: Any | None = None) -> Any | None:
        with self._state_lock:
            if expected is not None and self._pending_client is not expected:
                return None
            pending = self._pending_client
            self._pending_client = None
            return pending

    def epoch(self) -> int:
        with self._state_lock:
            return self._identity_epoch

    def is_current(self, epoch: int, account: str | None = None) -> bool:
        with self._state_lock:
            if epoch != self._identity_epoch:
                return False
            if account is None:
                return True
            return bool(
                self._client
                and str(getattr(self._client, "username", "") or "")
                == str(account)
            )

    def fence_and_clear(
        self,
        cleanup: Callable[[str], None] | None = None,
    ) -> str | None:
        """Atomically raise the epoch, clear identity, and run local cleanup."""
        with self._state_lock:
            account = (
                str(getattr(self._client, "username", "") or "") or None
            )
            self._identity_epoch += 1
            self._client = None
            self._pending_client = None
            if cleanup and account:
                cleanup(account)
            return account
