"""Persistent cache and favorite archives for research-training topics."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core.runtime.config import secure_file


class ResearchTrainingStorage:
    """Store account-bound snapshots in the shared runtime data directory."""

    CACHE_FILENAME = "research_training_cache.json"
    FAVORITES_FILENAME = "research_training_favorites.json"
    FORMAT_VERSION = 1

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path = self.data_dir / self.CACHE_FILENAME
        self.favorites_path = self.data_dir / self.FAVORITES_FILENAME
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()

    @contextmanager
    def refresh_guard(self, *, blocking: bool = True):
        """Coordinate refreshes and optionally skip an already running refresh."""
        acquired = self._refresh_lock.acquire(blocking=blocking)
        try:
            yield acquired
        finally:
            if acquired:
                self._refresh_lock.release()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _read_object(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    @staticmethod
    def _write_object(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        secure_file(temporary)
        os.replace(temporary, path)
        secure_file(path)

    @staticmethod
    def _content(snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            "batch": snapshot.get("batch") or {},
            "eligibility": snapshot.get("eligibility") or {},
            "topics": snapshot.get("topics") or [],
            "confirmed_topics": snapshot.get("confirmed_topics") or [],
        }

    @staticmethod
    def _topics_by_id(topics: list[Any]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for topic in topics:
            if not isinstance(topic, dict):
                continue
            topic_id = str(topic.get("topic_id") or "")
            if topic_id:
                result[topic_id] = topic
        return result

    @classmethod
    def _revision(cls, snapshot: dict[str, Any]) -> str:
        canonical = cls._content(snapshot)
        canonical["topics"] = sorted(
            canonical["topics"],
            key=lambda item: str(item.get("topic_id") or ""),
        )
        canonical["confirmed_topics"] = sorted(
            canonical["confirmed_topics"],
            key=lambda item: (
                str(item.get("batch_id") or ""),
                str(item.get("topic_id") or ""),
                str(item.get("record_id") or ""),
            ),
        )
        encoded = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def load_snapshot(self, username: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._read_object(self.cache_path)
        if (
            value.get("version") != self.FORMAT_VERSION
            or str(value.get("username") or "") != str(username)
            or not isinstance(value.get("batch"), dict)
            or not isinstance(value.get("topics"), list)
            or not isinstance(value.get("confirmed_topics"), list)
        ):
            return None
        return value

    def _favorite_records(self, username: str) -> list[dict[str, Any]]:
        value = self._read_object(self.favorites_path)
        users = value.get("users")
        if not isinstance(users, dict):
            return []
        user_data = users.get(str(username))
        if not isinstance(user_data, dict):
            return []
        records = user_data.get("records")
        if not isinstance(records, list):
            return []
        return [record for record in records if isinstance(record, dict)]

    def _write_favorite_records(
        self,
        username: str,
        records: list[dict[str, Any]],
    ) -> None:
        value = self._read_object(self.favorites_path)
        users = value.get("users")
        if not isinstance(users, dict):
            users = {}
        if records:
            users[str(username)] = {"records": records}
        else:
            users.pop(str(username), None)
        self._write_object(
            self.favorites_path,
            {"version": self.FORMAT_VERSION, "users": users},
        )

    def _refresh_favorite_archives(
        self,
        username: str,
        snapshot: dict[str, Any],
    ) -> None:
        records = self._favorite_records(username)
        if not records:
            return
        batch = snapshot.get("batch") or {}
        batch_id = str(batch.get("batch_id") or "")
        topics = self._topics_by_id(snapshot.get("topics") or [])
        changed = False
        for record in records:
            if str(record.get("batch_id") or "") != batch_id:
                continue
            topic_id = str(record.get("topic_id") or "")
            if topic_id in topics and record.get("topic") != topics[topic_id]:
                record["topic"] = topics[topic_id]
                record["batch_name"] = str(batch.get("name") or "")
                record["updated_at"] = self._now()
                changed = True
        if changed:
            self._write_favorite_records(username, records)

    def sync_favorite_archives(
        self,
        username: str,
        snapshot: dict[str, Any],
    ) -> None:
        """Refresh authoritative favorite archives from a committed catalog."""
        with self._lock:
            self._refresh_favorite_archives(username, snapshot)

    def delete_account(self, username: str) -> None:
        """Remove only one account's favorite state."""
        with self._lock:
            value = self._read_object(self.favorites_path)
            users = value.get("users")
            if not isinstance(users, dict) or str(username) not in users:
                return
            users.pop(str(username), None)
            self._write_object(
                self.favorites_path,
                {"version": self.FORMAT_VERSION, "users": users},
            )

    def save_snapshot(
        self,
        username: str,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "version": self.FORMAT_VERSION,
            "username": str(username),
            "saved_at": self._now(),
            "revision": self._revision(snapshot),
            **self._content(snapshot),
        }
        with self._lock:
            self._write_object(self.cache_path, payload)
            self._refresh_favorite_archives(username, payload)
        return payload

    def update_snapshot(
        self,
        username: str,
        snapshot: dict[str, Any],
    ) -> tuple[dict[str, Any], bool, dict[str, Any]]:
        previous = self.load_snapshot(username)
        previous_content = self._content(previous or {})
        next_content = self._content(snapshot)
        previous_revision = str((previous or {}).get("revision") or "")
        next_revision = self._revision(snapshot)
        changed = bool(previous and previous_revision != next_revision)

        old_topics = self._topics_by_id(previous_content["topics"])
        new_topics = self._topics_by_id(next_content["topics"])
        added = set(new_topics) - set(old_topics)
        removed = set(old_topics) - set(new_topics)
        updated = {
            topic_id
            for topic_id in set(old_topics) & set(new_topics)
            if old_topics[topic_id] != new_topics[topic_id]
        }
        old_batch = str((previous_content.get("batch") or {}).get("batch_id") or "")
        new_batch = str((next_content.get("batch") or {}).get("batch_id") or "")
        saved = self.save_snapshot(username, snapshot)
        return saved, changed, {
            "added": len(added),
            "updated": len(updated),
            "removed": len(removed),
            "new_batch": bool(previous and old_batch != new_batch),
            "confirmed_changed": bool(
                previous
                and previous_content["confirmed_topics"]
                != next_content["confirmed_topics"]
            ),
        }

    def favorite_ids(self, username: str, batch_id: str) -> list[str]:
        with self._lock:
            records = self._favorite_records(username)
        return sorted({
            str(record.get("topic_id") or "")
            for record in records
            if str(record.get("batch_id") or "") == str(batch_id)
            and record.get("topic_id")
        })

    def favorite_topics(
        self,
        username: str,
        current_snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        with self._lock:
            records = self._favorite_records(username)
        batch = current_snapshot.get("batch") or {}
        current_batch_id = str(batch.get("batch_id") or "")
        current_topics = self._topics_by_id(current_snapshot.get("topics") or [])
        favorites: list[dict[str, Any]] = []
        for record in records:
            batch_id = str(record.get("batch_id") or "")
            topic_id = str(record.get("topic_id") or "")
            is_current = batch_id == current_batch_id and topic_id in current_topics
            topic = current_topics.get(topic_id) if is_current else record.get("topic")
            if not isinstance(topic, dict):
                continue
            favorites.append({
                **topic,
                "favorite_batch_id": batch_id,
                "favorite_batch_name": str(record.get("batch_name") or ""),
                "favorited_at": str(record.get("favorited_at") or ""),
                "expired": not is_current,
            })
        return favorites

    def set_favorite(
        self,
        username: str,
        batch: dict[str, Any],
        topic: dict[str, Any],
        favorite: bool,
    ) -> list[str]:
        batch_id = str(batch.get("batch_id") or "")
        topic_id = str(topic.get("topic_id") or "")
        with self._lock:
            records = self._favorite_records(username)
            records = [
                record
                for record in records
                if not (
                    str(record.get("batch_id") or "") == batch_id
                    and str(record.get("topic_id") or "") == topic_id
                )
            ]
            if favorite:
                records.append({
                    "batch_id": batch_id,
                    "batch_name": str(batch.get("name") or ""),
                    "topic_id": topic_id,
                    "topic": topic,
                    "favorited_at": self._now(),
                })
            self._write_favorite_records(username, records)
        return self.favorite_ids(username, batch_id)
