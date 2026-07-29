"""Integration helpers between legacy local files and the shared cache."""

from __future__ import annotations

import hashlib
import json
import time
import logging
from datetime import datetime, timezone
from typing import Any

from backend.app.dependencies import (
    _cache_coordinator,
    _cache_registry,
    _cache_store,
    _identity_commit_guard,
    _local_cache_import_guard,
    _report_storage,
    _research_storage,
    _storage,
    get_auth_generation,
    auth_generation_is_current,
)
from backend.core.cache import CacheEntry, CacheKey, JobStatus, PayloadType
from backend.core.cache.resources import (
    avatar_payload,
    canonicalize_academic_report,
    canonicalize_avatar,
    canonicalize_research_training,
    canonicalize_scores,
    score_to_dict,
)


UTC = timezone.utc
logger = logging.getLogger(__name__)


def entry_is_compatible(entry: Any, resource: str) -> bool:
    if entry is None:
        return False
    spec = _cache_registry.get(resource)
    return bool(
        entry.schema_version == spec.schema_version
        and entry.revision_algorithm_version == spec.revision_algorithm_version
        and entry.payload_type == spec.payload_type
    )


def _revision(payload: Any, resource: str) -> str:
    spec = _cache_registry.get(resource)
    if spec.payload_type == PayloadType.BLOB:
        encoded = bytes(payload)
    else:
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    return f"v{spec.revision_algorithm_version}:{hashlib.sha256(encoded).hexdigest()}"


def _legacy_checked_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            parsed = datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.astimezone(UTC)


def _commit_legacy(
    account: str,
    resource: str,
    payload: Any,
    checked_at: Any,
    *,
    offline: bool = False,
) -> None:
    spec = _cache_registry.get(resource)
    canonical = spec.canonicalize(payload)
    epoch = get_auth_generation()
    guard = (
        _local_cache_import_guard(account)
        if offline
        else _identity_commit_guard(account, epoch)
    )
    with guard:
        if not offline and not auth_generation_is_current(epoch, account):
            return
        _cache_store.commit_success(
            key=CacheKey(account, resource),
            schema_version=spec.schema_version,
            revision_algorithm_version=spec.revision_algorithm_version,
            payload_type=spec.payload_type,
            payload=canonical,
            revision=_revision(canonical, resource),
            dependency_revisions={},
            changes={"legacy_migrated": True},
            reason="legacy_migration",
            checked_at=_legacy_checked_at(checked_at),
        )
        # An imported file proves only that cached content exists; it is not a
        # successful check under the new protocol.
        _cache_store.invalidate(CacheKey(account, resource))


def _legacy_candidate(account: str, resource: str):
    if resource == "scores":
        legacy = _storage.load_scores_with_meta()
        meta = legacy.get("meta") or {}
        scores = legacy.get("scores") or []
        if str(meta.get("username") or "") != account or not scores:
            return None
        return ({
                "scores": [score_to_dict(score) for score in scores],
                "overall_gpa": meta.get("overall_gpa"),
            },
            meta.get("fetch_time") or _storage.get_last_update_time(),
        )
    if resource == "academic-report":
        legacy = _report_storage.load_report() or {}
        if (
            str(legacy.get("username") or "") != account
            or not legacy.get("report")
        ):
            return None
        return legacy["report"], legacy.get("saved_at")
    if resource == "research-training":
        legacy = _research_storage.load_snapshot(account)
        if not legacy:
            return None
        return ({
                "batch": legacy.get("batch") or {},
                "eligibility": legacy.get("eligibility") or {},
                "topics": legacy.get("topics") or [],
                "confirmed_topics": legacy.get("confirmed_topics") or [],
            },
            legacy.get("saved_at"),
        )
    if resource == "avatar":
        meta = _storage.get_avatar_meta() or {}
        image = _storage.load_avatar()
        if (
            str(meta.get("username") or "") != account
            or not image
            or not meta.get("avatar_token")
        ):
            return None
        return avatar_payload(str(meta["avatar_token"]), image), meta.get("saved_at")
    return None


def migrate_legacy_resource(
    account: str, resource: str, *, offline: bool = False
) -> None:
    """Idempotently import one trusted, account-bound legacy cache."""
    existing = _cache_store.get(CacheKey(account, resource))
    if entry_is_compatible(existing, resource):
        return
    candidate = _legacy_candidate(account, resource)
    if candidate:
        payload, checked_at = candidate
        _commit_legacy(
            account, resource, payload, checked_at, offline=offline
        )


def read_cache(account: str, resource: str):
    try:
        migrate_legacy_resource(account, resource)
    except Exception as error:
        logger.warning(
            "Legacy cache migration failed for resource=%s error=%s",
            resource,
            type(error).__name__,
        )
    entry, stale = _cache_coordinator.read(
        account_id=account,
        resource=resource,
    )
    if not entry_is_compatible(entry, resource):
        # One-release compatibility fallback. It is deliberately always stale
        # and never becomes a second persisted truth.
        try:
            candidate = _legacy_candidate(account, resource)
            if candidate:
                payload, checked_at = candidate
                spec = _cache_registry.get(resource)
                canonical = spec.canonicalize(payload)
                checked = _legacy_checked_at(checked_at)
                return CacheEntry(
                    key=CacheKey(account, resource),
                    schema_version=spec.schema_version,
                    revision_algorithm_version=spec.revision_algorithm_version,
                    payload_type=spec.payload_type,
                    payload=canonical,
                    revision=_revision(canonical, resource),
                    saved_at=checked,
                    last_checked_at=None,
                    last_attempt_at=None,
                    dependency_revisions={},
                ), True
        except Exception:
            pass
        return None, True
    return entry, stale


def read_cache_offline(account: str, resource: str):
    """Read/migrate an offline-readable resource without creating remote work."""
    spec = _cache_registry.get(resource)
    if not spec.offline_readable:
        raise ValueError(f"resource is not offline readable: {resource}")
    try:
        migrate_legacy_resource(account, resource, offline=True)
    except Exception as error:
        logger.warning(
            "Offline legacy cache migration failed for resource=%s error=%s",
            resource,
            type(error).__name__,
        )
    entry, stale = _cache_coordinator.read(
        account_id=account,
        resource=resource,
    )
    if entry_is_compatible(entry, resource):
        return entry, stale
    try:
        with _local_cache_import_guard(account):
            candidate = _legacy_candidate(account, resource)
            if not candidate:
                return None, True
            payload, checked_at = candidate
            canonical = spec.canonicalize(payload)
            checked = _legacy_checked_at(checked_at)
            return CacheEntry(
                key=CacheKey(account, resource),
                schema_version=spec.schema_version,
                revision_algorithm_version=spec.revision_algorithm_version,
                payload_type=spec.payload_type,
                payload=canonical,
                revision=_revision(canonical, resource),
                saved_at=checked,
                last_checked_at=None,
                last_attempt_at=None,
                dependency_revisions={},
            ), True
    except Exception:
        return None, True


def submit_refresh(
    account: str,
    resource: str,
    *,
    force: bool,
    reason: str,
):
    return _cache_coordinator.submit(
        account_id=account,
        resource=resource,
        identity_epoch=get_auth_generation(),
        force=force,
        reason=reason,
    )


def wait_for_job(job_id: str | None, timeout: float = 90.0):
    if not job_id:
        return None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = _cache_coordinator.get_job(job_id)
        if job is None or job.status in {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            return job
        time.sleep(0.05)
    return _cache_coordinator.get_job(job_id)
