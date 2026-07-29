"""Process-wide identity epoch and shared requests.Session coordination."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterator


class AuthSessionManager:
    """Own the active client, identity fencing and remote serialization."""

    def __init__(self) -> None:
        self._client: Any | None = None
        self._identity_epoch = 0
        self._state_lock = threading.RLock()
        self._remote_lock = threading.Lock()

    @contextmanager
    def remote_guard(self) -> Iterator[None]:
        with self._remote_lock:
            yield

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
            if cleanup and account:
                cleanup(account)
            return account
