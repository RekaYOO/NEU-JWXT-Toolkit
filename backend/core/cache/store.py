"""SQLite-backed authoritative store for rebuildable cache data."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from .models import CacheEntry, CacheEvent, CacheKey, PayloadType, utc_now


UTC = timezone.utc
DB_SCHEMA_VERSION = 1


def _timestamp(value: datetime | None) -> float | None:
    return value.timestamp() if value else None


def _datetime(value: float | None) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if value is not None else None


class CacheStore:
    """Thread-safe-by-connection SQLite cache.

    Every operation opens a short-lived connection. Network work must happen
    outside these methods so no database transaction spans remote I/O.
    """

    def __init__(self, path: Path | str, *, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self.path.parent.chmod(stat.S_IRWXU)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cache_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cache_entries (
                    account_id TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    variant TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    revision_algorithm_version INTEGER NOT NULL,
                    payload_type TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    revision TEXT NOT NULL,
                    saved_at REAL NOT NULL,
                    last_checked_at REAL,
                    last_attempt_at REAL,
                    last_error_kind TEXT,
                    dependency_revisions TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (account_id, resource, variant)
                );
                CREATE TABLE IF NOT EXISTS cache_events (
                    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    variant TEXT NOT NULL,
                    previous_revision TEXT,
                    revision TEXT NOT NULL,
                    changed INTEGER NOT NULL,
                    changes_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS cache_events_account_cursor
                    ON cache_events(account_id, cursor);
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO cache_meta(key, value) VALUES('schema_version', ?)",
                (str(DB_SCHEMA_VERSION),),
            )
        finally:
            connection.close()
        if os.name != "nt":
            self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    @staticmethod
    def _encode(payload_type: PayloadType, payload: Any) -> bytes:
        if payload_type == PayloadType.BLOB:
            if not isinstance(payload, (bytes, bytearray, memoryview)):
                raise TypeError("BLOB cache payload must be bytes-like")
            return bytes(payload)
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    @staticmethod
    def _decode(payload_type: PayloadType, payload: bytes) -> Any:
        if payload_type == PayloadType.BLOB:
            return bytes(payload)
        return json.loads(payload.decode("utf-8"))

    @classmethod
    def _row_to_entry(cls, row: sqlite3.Row) -> CacheEntry:
        payload_type = PayloadType(row["payload_type"])
        return CacheEntry(
            key=CacheKey(row["account_id"], row["resource"], row["variant"]),
            schema_version=row["schema_version"],
            revision_algorithm_version=row["revision_algorithm_version"],
            payload_type=payload_type,
            payload=cls._decode(payload_type, row["payload"]),
            revision=row["revision"],
            saved_at=_datetime(row["saved_at"]),  # type: ignore[arg-type]
            last_checked_at=_datetime(row["last_checked_at"]),
            last_attempt_at=_datetime(row["last_attempt_at"]),
            last_error_kind=row["last_error_kind"],
            dependency_revisions=json.loads(row["dependency_revisions"]),
        )

    def get(self, key: CacheKey) -> CacheEntry | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT * FROM cache_entries
                WHERE account_id = ? AND resource = ? AND variant = ?
                """,
                (key.account_id, key.resource, key.variant),
            ).fetchone()
            return self._row_to_entry(row) if row else None
        finally:
            connection.close()

    def commit_success(
        self,
        *,
        key: CacheKey,
        schema_version: int,
        revision_algorithm_version: int,
        payload_type: PayloadType,
        payload: Any,
        revision: str,
        dependency_revisions: Mapping[str, str],
        changes: Mapping[str, Any],
        reason: str,
        checked_at: datetime | None = None,
    ) -> CacheEvent:
        checked_at = checked_at or utc_now()
        encoded = self._encode(payload_type, payload)
        dependencies_json = json.dumps(dict(dependency_revisions), sort_keys=True)
        with self._transaction() as connection:
            previous_row = connection.execute(
                """
                SELECT revision, saved_at FROM cache_entries
                WHERE account_id = ? AND resource = ? AND variant = ?
                """,
                (key.account_id, key.resource, key.variant),
            ).fetchone()
            previous_revision = previous_row["revision"] if previous_row else None
            changed = previous_revision != revision
            effective_changes = dict(changes) if changed else {}
            changes_json = json.dumps(
                effective_changes, ensure_ascii=False, sort_keys=True
            )
            saved_at = checked_at if changed else _datetime(previous_row["saved_at"])
            connection.execute(
                """
                INSERT INTO cache_entries(
                    account_id, resource, variant, schema_version,
                    revision_algorithm_version, payload_type, payload, revision,
                    saved_at, last_checked_at, last_attempt_at, last_error_kind,
                    dependency_revisions
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                ON CONFLICT(account_id, resource, variant) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    revision_algorithm_version = excluded.revision_algorithm_version,
                    payload_type = excluded.payload_type,
                    payload = CASE
                        WHEN cache_entries.revision != excluded.revision
                          OR cache_entries.schema_version != excluded.schema_version
                          OR cache_entries.revision_algorithm_version
                             != excluded.revision_algorithm_version
                          OR cache_entries.payload_type != excluded.payload_type
                        THEN excluded.payload ELSE cache_entries.payload END,
                    revision = excluded.revision,
                    saved_at = CASE WHEN cache_entries.revision != excluded.revision
                                    THEN excluded.saved_at ELSE cache_entries.saved_at END,
                    last_checked_at = excluded.last_checked_at,
                    last_attempt_at = excluded.last_attempt_at,
                    last_error_kind = NULL,
                    dependency_revisions = excluded.dependency_revisions
                """,
                (
                    key.account_id,
                    key.resource,
                    key.variant,
                    schema_version,
                    revision_algorithm_version,
                    payload_type.value,
                    encoded,
                    revision,
                    _timestamp(saved_at),
                    _timestamp(checked_at),
                    _timestamp(checked_at),
                    dependencies_json,
                ),
            )
            cursor = connection.execute(
                """
                INSERT INTO cache_events(
                    account_id, resource, variant, previous_revision, revision,
                    changed, changes_json, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key.account_id,
                    key.resource,
                    key.variant,
                    previous_revision,
                    revision,
                    int(changed),
                    changes_json,
                    reason,
                    _timestamp(checked_at),
                ),
            ).lastrowid
            # Cache events are a short-lived UI synchronization feed, not a
            # reliable queue. Bound each account so unchanged checks cannot grow
            # the database forever.
            connection.execute(
                """
                DELETE FROM cache_events
                WHERE account_id = ? AND cursor NOT IN (
                    SELECT cursor FROM cache_events
                    WHERE account_id = ?
                    ORDER BY cursor DESC LIMIT 500
                )
                """,
                (key.account_id, key.account_id),
            )
        return CacheEvent(
            cursor=int(cursor),
            key=key,
            previous_revision=previous_revision,
            revision=revision,
            changed=changed,
            changes=effective_changes,
            reason=reason,
            created_at=checked_at,
        )

    def mark_attempt(
        self, key: CacheKey, *, attempted_at: datetime | None = None
    ) -> None:
        attempted_at = attempted_at or utc_now()
        connection = self._connect()
        try:
            connection.execute(
                """
                UPDATE cache_entries SET last_attempt_at = ?
                WHERE account_id = ? AND resource = ? AND variant = ?
                """,
                (_timestamp(attempted_at), key.account_id, key.resource, key.variant),
            )
        finally:
            connection.close()

    def mark_failure(
        self,
        key: CacheKey,
        error_kind: str,
        *,
        attempted_at: datetime | None = None,
    ) -> None:
        attempted_at = attempted_at or utc_now()
        connection = self._connect()
        try:
            connection.execute(
                """
                UPDATE cache_entries
                SET last_attempt_at = ?, last_error_kind = ?
                WHERE account_id = ? AND resource = ? AND variant = ?
                """,
                (
                    _timestamp(attempted_at),
                    error_kind[:128],
                    key.account_id,
                    key.resource,
                    key.variant,
                ),
            )
        finally:
            connection.close()

    def mark_skip_success(
        self, key: CacheKey, *, checked_at: datetime | None = None
    ) -> None:
        """Record a successful no-commit fetch without changing cached data."""
        checked_at = checked_at or utc_now()
        connection = self._connect()
        try:
            connection.execute(
                """
                UPDATE cache_entries
                SET last_checked_at = ?, last_attempt_at = ?, last_error_kind = NULL
                WHERE account_id = ? AND resource = ? AND variant = ?
                """,
                (
                    _timestamp(checked_at),
                    _timestamp(checked_at),
                    key.account_id,
                    key.resource,
                    key.variant,
                ),
            )
        finally:
            connection.close()

    def invalidate(self, key: CacheKey) -> bool:
        connection = self._connect()
        try:
            result = connection.execute(
                """
                UPDATE cache_entries SET last_checked_at = NULL
                WHERE account_id = ? AND resource = ? AND variant = ?
                """,
                (key.account_id, key.resource, key.variant),
            )
            return result.rowcount > 0
        finally:
            connection.close()

    def delete(self, key: CacheKey) -> bool:
        connection = self._connect()
        try:
            result = connection.execute(
                """
                DELETE FROM cache_entries
                WHERE account_id = ? AND resource = ? AND variant = ?
                """,
                (key.account_id, key.resource, key.variant),
            )
            return result.rowcount > 0
        finally:
            connection.close()

    def delete_account(self, account_id: str) -> int:
        with self._transaction() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM cache_entries WHERE account_id = ?",
                (account_id,),
            ).fetchone()[0]
            connection.execute(
                "DELETE FROM cache_entries WHERE account_id = ?", (account_id,)
            )
            connection.execute(
                "DELETE FROM cache_events WHERE account_id = ?", (account_id,)
            )
        return int(count)

    def events_after(
        self, account_id: str, cursor: int = 0, *, limit: int = 100
    ) -> list[CacheEvent]:
        if limit < 1 or limit > 1000:
            raise ValueError("Event limit must be between 1 and 1000")
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM cache_events
                WHERE account_id = ? AND cursor > ?
                ORDER BY cursor ASC LIMIT ?
                """,
                (account_id, max(0, cursor), limit),
            ).fetchall()
        finally:
            connection.close()
        return [
            CacheEvent(
                cursor=row["cursor"],
                key=CacheKey(row["account_id"], row["resource"], row["variant"]),
                previous_revision=row["previous_revision"],
                revision=row["revision"],
                changed=bool(row["changed"]),
                changes=json.loads(row["changes_json"]),
                reason=row["reason"],
                created_at=_datetime(row["created_at"]),  # type: ignore[arg-type]
            )
            for row in rows
        ]

    def latest_event_cursor(self, account_id: str) -> int:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT COALESCE(MAX(cursor), 0) FROM cache_events WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            connection.close()

    def latest_account_for(self, resources: tuple[str, ...]) -> str | None:
        """Return the most recently checked account having one of the resources."""
        if not resources:
            return None
        placeholders = ",".join("?" for _ in resources)
        connection = self._connect()
        try:
            row = connection.execute(
                f"""
                SELECT account_id FROM cache_entries
                WHERE resource IN ({placeholders})
                ORDER BY COALESCE(last_checked_at, saved_at) DESC
                LIMIT 1
                """,
                resources,
            ).fetchone()
            return str(row[0]) if row else None
        finally:
            connection.close()
