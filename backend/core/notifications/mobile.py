"""Durable notification channel consumed by the Android local application."""

from __future__ import annotations

import json
import os
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from backend.core.runtime.config import secure_file


CHINA_TZ = timezone(timedelta(hours=8))


def _iso() -> str:
    return datetime.now(CHINA_TZ).isoformat()


class MobileNotificationService:
    """Match the mail outbox contract while leaving delivery to Android."""

    channel = "android"

    def __init__(self, data_dir: str | Path, logger: Any) -> None:
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.outbox_path = root / "mobile_notification_outbox.json"
        self.logger = logger
        self._lock = threading.RLock()
        self._listeners: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self._validators: dict[str, Callable[[dict[str, Any]], bool]] = {}
        self._materializers: dict[str, Callable] = {}
        self._delivery_lock = threading.Lock()
        self._outbox = self._read()

    def _read(self) -> list[dict[str, Any]]:
        try:
            value = json.loads(self.outbox_path.read_text(encoding="utf-8"))
            messages = value.get("messages", []) if isinstance(value, dict) else []
            return [dict(item) for item in messages if isinstance(item, dict)]
        except (OSError, ValueError, TypeError):
            return []

    def _save(self, messages: list[dict[str, Any]]) -> None:
        temporary = self.outbox_path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            secure_file(temporary)
            json.dump({"messages": messages}, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.outbox_path)
        # Publish in memory only after the durable file has been replaced.
        self._outbox = messages

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def is_configured(self) -> bool:
        return True

    def get_config(self) -> dict[str, Any]:
        return {"channel": "android", "configured": True}

    def get_status(self) -> dict[str, Any]:
        return {
            "configured": True,
            "status": "Android 系统通知可用",
            "pending_notifications": self.pending_count(),
        }

    def update_config(self, _values: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("Android 本地版不使用 SMTP 配置")

    def test_email(self) -> None:
        raise ValueError("Android 本地版使用系统通知")

    def register_materializer(self, kind: str, provider: Callable) -> None:
        self._materializers[str(kind)] = provider

    def register_delivery_listener(
        self, source: str, listener: Callable[[dict[str, Any]], None],
    ) -> None:
        self._listeners.setdefault(str(source), []).append(listener)

    def register_delivery_validator(
        self, source: str, validator: Callable[[dict[str, Any]], bool],
    ) -> None:
        self._validators[str(source)] = validator

    def queue_notification(
        self,
        source: str,
        subject: str,
        body: str,
        dedupe_key: str,
        html_body: str = "",
        *,
        kind: str = "message",
        template_metadata: dict[str, Any] | None = None,
        priority: bool = False,
    ) -> bool:
        del html_body
        with self._lock:
            if any(item.get("dedupe_key") == dedupe_key for item in self._outbox):
                return False
            message = {
                "id": uuid.uuid4().hex,
                "source": str(source),
                "dedupe_key": str(dedupe_key),
                "title": str(subject),
                "subject": str(subject),
                "kind": str(kind),
                "template_metadata": deepcopy(template_metadata or {}),
                "body": str(body),
                "route": str((template_metadata or {}).get("route") or self._route(source)),
                "created_at": _iso(),
            }
            self._save([message, *self._outbox] if priority else [*self._outbox, message])
        return True

    @staticmethod
    def _route(source: str) -> str:
        return {
            "grade_tracking": "/scores",
            "course_selection": "/course-selection",
            "auth_recovery": "/login",
        }.get(str(source), "/")

    def pending_count(self, source: str | None = None) -> int:
        with self._lock:
            return sum(
                1 for item in self._outbox
                if source is None or item.get("source") == source
            )

    def has_pending(self, source: str, dedupe_key: str) -> bool:
        with self._lock:
            return any(
                item.get("source") == source and item.get("dedupe_key") == dedupe_key
                for item in self._outbox
            )

    def discard(
        self,
        *,
        source: str | None = None,
        dedupe_prefix: str = "",
        kind: str = "",
        template_context_ids: set[str] | None = None,
    ) -> None:
        context_ids = {str(value) for value in (template_context_ids or set()) if value}
        with self._lock:
            remaining = [
                item for item in self._outbox
                if not (
                    (source is None or item.get("source") == source)
                    and (
                        not dedupe_prefix
                        or str(item.get("dedupe_key") or "").startswith(dedupe_prefix)
                    )
                    and (not kind or str(item.get("kind") or "message") == kind)
                    and (not context_ids
                         or str((item.get("template_metadata") or {}).get("context_id") or "")
                         in context_ids)
                )
            ]
            self._save(remaining)

    def pending(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            snapshot = deepcopy(self._outbox)
        valid = []
        discarded = set()
        # Business callbacks may take the tracking lock, whose callers also queue notifications.
        # Never hold the outbox lock across those callbacks.
        for item in snapshot:
            validator = self._validators.get(str(item.get("source") or ""))
            try:
                if validator is not None and not validator(deepcopy(item)):
                    discarded.add(item["id"])
                    continue
                kind = str(item.get("kind") or "message")
                if kind != "message":
                    provider = self._materializers.get(kind)
                    rendered = provider(deepcopy(item)) if provider else None
                    if not rendered:
                        continue
                    item.update(rendered)
                    item["title"] = item.get("subject", item["title"])
                valid.append(item)
            except Exception:
                self.logger.exception("[移动通知] 校验或生成通知失败")
        with self._lock:
            if discarded:
                self._save([item for item in self._outbox if item["id"] not in discarded])
            existing = {item["id"] for item in self._outbox}
        return [item for item in valid if item["id"] in existing][: max(1, min(100, int(limit)))]

    def acknowledge(self, message_id: str) -> bool:
        with self._delivery_lock:
            with self._lock:
                item = next((deepcopy(row) for row in self._outbox if row.get("id") == message_id), None)
            if item is None:
                return False
            try:
                for listener in self._listeners.get(str(item.get("source") or ""), []):
                    listener(deepcopy(item))
            except Exception:
                self.logger.exception("[移动通知] 发送完成回调失败")
                return False
            with self._lock:
                self._save([row for row in self._outbox if row.get("id") != message_id])
            return True
