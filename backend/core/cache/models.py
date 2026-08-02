"""Shared value types for the project cache infrastructure."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


class PayloadType(str, Enum):
    JSON = "json"
    BLOB = "blob"


class AccountScope(str, Enum):
    ACCOUNT = "account"
    GLOBAL = "global"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RefreshStatus(str, Enum):
    FRESH = "fresh"
    STARTED = "started"
    RUNNING = "running"
    THROTTLED = "throttled"


@dataclass(frozen=True)
class CacheKey:
    account_id: str
    resource: str
    variant: str = "default"


@dataclass(frozen=True)
class CacheEntry:
    key: CacheKey
    schema_version: int
    revision_algorithm_version: int
    payload_type: PayloadType
    payload: Any
    revision: str
    saved_at: datetime
    last_checked_at: datetime | None
    last_attempt_at: datetime | None
    last_error_kind: str | None = None
    dependency_revisions: Mapping[str, str] = field(default_factory=dict)

    def metadata(self, *, is_stale: bool) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return value.astimezone(UTC).isoformat() if value else None

        return {
            "schema_version": self.schema_version,
            "revision_algorithm_version": self.revision_algorithm_version,
            "revision": self.revision,
            "saved_at": iso(self.saved_at),
            "last_checked_at": iso(self.last_checked_at),
            "last_attempt_at": iso(self.last_attempt_at),
            "last_error_kind": self.last_error_kind,
            "dependency_revisions": dict(self.dependency_revisions),
            "is_stale": is_stale,
        }


@dataclass(frozen=True)
class CacheEvent:
    cursor: int
    key: CacheKey
    previous_revision: str | None
    revision: str
    changed: bool
    changes: Mapping[str, Any]
    reason: str
    created_at: datetime


@dataclass(frozen=True)
class FetchContext:
    key: CacheKey
    identity_epoch: int
    reason: str


@dataclass(frozen=True)
class CacheFetchSkipped:
    """A successful fetch that intentionally leaves the cached payload unchanged."""

    reason: str


@dataclass(frozen=True)
class CacheJob:
    job_id: str
    key: CacheKey
    identity_epoch: int
    reason: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    previous_revision: str | None = None
    revision: str | None = None
    changed: bool | None = None
    changes: Mapping[str, Any] = field(default_factory=dict)
    error_kind: str | None = None

    def with_updates(self, **changes: Any) -> "CacheJob":
        return replace(self, updated_at=utc_now(), **changes)


@dataclass(frozen=True)
class RefreshSubmission:
    status: RefreshStatus
    key: CacheKey
    job_id: str | None
    revision: str | None
    is_stale: bool
