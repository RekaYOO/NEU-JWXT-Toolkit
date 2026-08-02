"""Generic cache refresh, job and revision-event APIs."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.dependencies import (
    _cache_coordinator,
    _cache_registry,
    _cache_store,
    get_auth_generation,
    require_cached_auth_identity,
)
from backend.core.auth import NEUAuthClient
from backend.core.cache.models import RefreshStatus, utc_now


router = APIRouter()
PUBLIC_RESOURCES = frozenset(("scores", "academic-report", "research-training", "festival-activities", "avatar"))


def _account(auth: NEUAuthClient) -> str:
    account = str(getattr(auth, "username", "") or "")
    if not account:
        raise HTTPException(status_code=401, detail="无法确认当前登录账号")
    return account


def _job_response(job) -> dict:
    return {
        "job_id": job.job_id,
        "resource": job.key.resource,
        "variant": job.key.variant,
        "status": job.status.value,
        "reason": job.reason,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "previous_revision": job.previous_revision,
        "revision": job.revision,
        "changed": job.changed,
        "diff": dict(job.changes),
        "error_kind": job.error_kind,
    }


@router.post("/cache/refresh/{resource}")
def refresh_cache_resource(
    resource: str,
    variant: str = Query("default", min_length=1, max_length=128),
    force: bool = Query(False),
    reason: Literal["page_swr", "manual"] = Query("page_swr"),
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
):
    if resource not in PUBLIC_RESOURCES:
        raise HTTPException(status_code=404, detail="未知或不可直接刷新的缓存资源")
    if variant != "default":
        raise HTTPException(status_code=400, detail="该资源不支持自定义缓存变体")
    account = _account(auth)
    try:
        submission = _cache_coordinator.submit(
            account_id=account,
            resource=resource,
            variant=variant,
            identity_epoch=get_auth_generation(),
            force=force,
            reason="manual" if force or reason == "manual" else "page_swr",
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    response = {
        "status": submission.status.value,
        "resource": submission.key.resource,
        "variant": submission.key.variant,
        "job_id": submission.job_id,
        "revision": submission.revision,
        "is_stale": submission.is_stale,
    }
    if submission.status == RefreshStatus.THROTTLED:
        entry = _cache_store.get(submission.key)
        response["error_kind"] = (
            entry.last_error_kind if entry is not None else "recent_failure"
        )
        if entry is not None and entry.last_attempt_at is not None:
            elapsed = utc_now() - entry.last_attempt_at
            response["retry_after_seconds"] = max(
                1,
                int(
                    (
                        _cache_coordinator.failure_backoff - elapsed
                    ).total_seconds()
                ),
            )
        else:
            response["retry_after_seconds"] = int(
                _cache_coordinator.failure_backoff.total_seconds()
            )
    return response


@router.get("/cache/jobs/{job_id}")
def get_cache_job(
    job_id: str,
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
):
    job = _cache_coordinator.get_job(job_id)
    if job is None or job.key.account_id != _account(auth):
        raise HTTPException(status_code=404, detail="缓存任务不存在")
    return _job_response(job)


@router.get("/cache/events")
def get_cache_events(
    after: Optional[int] = Query(None, ge=0),
    limit: int = Query(100, ge=1, le=200),
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
):
    account = _account(auth)
    if after is None:
        return {
            "events": [],
            "cursor": _cache_store.latest_event_cursor(account),
        }
    events = _cache_store.events_after(account, after, limit=limit)
    items = [
        {
            "cursor": event.cursor,
            "resource": event.key.resource,
            "variant": event.key.variant,
            "previous_revision": event.previous_revision,
            "revision": event.revision,
            "changed": event.changed,
            # Browser polling receives only version-level summaries. Full course
            # or profile payload remains available solely through typed APIs.
            "changes": (
                {"counts": dict(event.changes.get("counts") or {})}
                if event.key.resource == "scores"
                else {
                    key: value
                    for key, value in event.changes.items()
                    if key.endswith("_changed") or key == "initial"
                }
            ),
            "reason": event.reason,
            "created_at": event.created_at.isoformat(),
        }
        for event in events
    ]
    return {
        "events": items,
        "cursor": items[-1]["cursor"] if items else after,
    }
