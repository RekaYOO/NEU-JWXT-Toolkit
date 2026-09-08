"""In-app replacement for remote recovery links in the Android profile."""

from __future__ import annotations

from typing import Any, Callable


class MobileAuthRecoveryService:
    def __init__(self, notifications: Any) -> None:
        self.notifications = notifications
        self._callbacks: dict[str, list[Callable[[str], None]]] = {}

    def stop(self) -> None:
        return None

    def register_recovered_callback(self, source: str, callback: Callable[[str], None]) -> None:
        self._callbacks.setdefault(str(source), []).append(callback)

    def request_notification(
        self,
        *,
        source: str,
        target_service: str,
        account_id: str,
        subject: str,
        body: str,
        dedupe_key: str,
        html_body: str = "",
    ) -> bool:
        del target_service, account_id, html_body
        return self.notifications.queue_notification(
            "auth_recovery",
            subject,
            body + "\n\n请打开本机应用重新登录。",
            f"mobile-login:{source}:{dedupe_key}",
            template_metadata={"route": "/login"},
            priority=True,
        )

    def invalidate_matching(self, *, source: str, **_values: Any) -> None:
        self.notifications.discard(dedupe_prefix=f"mobile-login:{source}:")

    def invalidate_all(self, *, clear_state: bool = False) -> None:
        del clear_state
        self.notifications.discard(source="auth_recovery")

    def notify_foreground_login(self, account_id: str) -> None:
        self.notifications.discard(source="auth_recovery")
        for callbacks in self._callbacks.values():
            for callback in callbacks:
                callback(str(account_id))

    def get_config(self) -> dict[str, Any]:
        return {"enabled": False, "mode": "local_notification"}

    def update_config(self, _values: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("Android 本地版不提供远程登录恢复")
