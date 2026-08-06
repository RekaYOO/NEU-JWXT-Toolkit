from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.dependencies import (
    _cache_coordinator,
    _research_storage,
    get_auth_generation,
    require_cached_auth_identity,
    require_serialized_auth,
)
from backend.app.cache_support import read_cache, submit_refresh, wait_for_job
from backend.app.schemas.research import (
    ResearchCancellationRequest,
    ResearchCacheResponse,
    ResearchEnrollmentRequest,
    ResearchFavoriteRequest,
    ResearchFavoriteResponse,
)
from backend.core.log import log_application_error
from backend.core.academic.research_training import (
    ResearchTrainingAPI,
    ResearchTrainingError,
)
from backend.core.auth import NEUAuthClient
from backend.core.cache import mutation_policy
from backend.app.presenters import research_cache_response


router = APIRouter()


def _api(auth: NEUAuthClient) -> ResearchTrainingAPI:
    return ResearchTrainingAPI(auth)


def _username(auth: NEUAuthClient) -> str:
    username = str(getattr(auth, "username", "") or "")
    if not username:
        raise HTTPException(status_code=401, detail="无法确认当前登录账号")
    return username


def _cache_response(
    username: str,
    entry,
    *,
    is_stale: bool,
    update_available: bool = False,
    changes: dict | None = None,
) -> dict:
    """Compatibility wrapper; shared mapping lives in app.presenters."""
    return research_cache_response(
        _research_storage,
        username,
        entry,
        is_stale=is_stale,
        update_available=update_available,
        changes=changes,
    )


@router.get("/research-training/cache", response_model=ResearchCacheResponse)
def get_research_training_cache(
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
):
    username = _username(auth)
    entry, stale = read_cache(username, "research-training")
    return _cache_response(username, entry, is_stale=stale)


@router.post("/research-training/refresh", response_model=ResearchCacheResponse)
def refresh_research_training(
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
):
    try:
        username = _username(auth)
        submission = submit_refresh(
            username,
            "research-training",
            force=True,
            reason="manual",
        )
        job = wait_for_job(submission.job_id)
        entry, stale = read_cache(username, "research-training")
        if entry is None:
            raise HTTPException(
                status_code=503,
                detail=f"刷新科研训练失败: {getattr(job, 'error_kind', None) or 'unknown'}",
            )
        raw = dict(getattr(job, "changes", {}) or {})
        changes = {
            "added": len(raw.get("added_topic_ids") or []),
            "updated": len(raw.get("changed_topic_ids") or []),
            "removed": len(raw.get("removed_topic_ids") or []),
            "new_batch": bool(raw.get("batch_changed")),
            "confirmed_changed": bool(raw.get("confirmed_changed")),
        }
        return _cache_response(
            username,
            entry,
            is_stale=stale,
            update_available=bool(getattr(job, "changed", False)),
            changes=changes,
        )
    except HTTPException:
        raise
    except ResearchTrainingError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        error_id = log_application_error("research.refresh", error, 500)
        raise HTTPException(
            status_code=500,
            detail=f"刷新科研训练课题失败（错误编号：{error_id}）",
        ) from error


@router.post(
    "/research-training/favorite",
    response_model=ResearchFavoriteResponse,
)
def set_research_topic_favorite(
    request: ResearchFavoriteRequest,
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
):
    username = _username(auth)
    entry, _stale = read_cache(username, "research-training")
    if not entry:
        raise HTTPException(status_code=404, detail="尚无科研训练课题缓存")
    snapshot = entry.payload
    batch_id = str((snapshot.get("batch") or {}).get("batch_id") or "")
    if request.favorite and request.batch_id != batch_id:
        raise HTTPException(status_code=409, detail="科研训练批次已变化，请刷新后重试")
    topics = {
        str(topic.get("topic_id") or ""): topic
        for topic in snapshot.get("topics") or []
        if isinstance(topic, dict) and topic.get("topic_id")
    }
    if request.favorite and request.topic_id not in topics:
        raise HTTPException(status_code=404, detail="当前缓存中不存在该课题")
    _research_storage.set_favorite(
        username,
        (
            snapshot.get("batch") or {}
            if request.favorite
            else {"batch_id": request.batch_id}
        ),
        topics.get(request.topic_id) or {"topic_id": request.topic_id},
        request.favorite,
    )
    favorites = _research_storage.favorite_ids(username, batch_id)
    return {
        "success": True,
        "favorite": request.favorite,
        "favorite_topic_ids": favorites,
        "favorite_topics": _research_storage.favorite_topics(username, snapshot),
    }


@router.get("/research-training")
def get_research_training(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    keyword: str = Query("", max_length=100),
    project_name: str = Query("", max_length=100),
    advisor_name: str = Query("", max_length=50),
    auth: NEUAuthClient = Depends(require_serialized_auth),
):
    try:
        api = _api(auth)
        batch = api.get_current_batch()
        eligibility = api.get_eligibility(batch.batch_id)
        topics = api.get_topics(
            batch.batch_id,
            page=page,
            page_size=page_size,
            keyword=keyword,
            project_name=project_name,
            advisor_name=advisor_name,
        )
        return {
            "batch": batch.__dict__,
            "eligibility": eligibility.__dict__,
            **topics,
        }
    except ResearchTrainingError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        error_id = log_application_error("research.list_topics", error, 500)
        raise HTTPException(status_code=500, detail=f"获取科研训练课题失败（错误编号：{error_id}）") from error


@router.get("/research-training/topics/{topic_id}")
def get_research_topic(
    topic_id: str,
    auth: NEUAuthClient = Depends(require_serialized_auth),
):
    try:
        return _api(auth).get_topic_detail(topic_id)
    except ResearchTrainingError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/research-training/confirmed")
def get_confirmed_research_topics(
    auth: NEUAuthClient = Depends(require_serialized_auth),
):
    try:
        api = _api(auth)
        batch = api.get_current_batch()
        topics = api.get_confirmed_topics(batch.batch_id)
        return {"batch": batch.__dict__, "topics": topics, "total": len(topics)}
    except ResearchTrainingError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/research-training/enroll")
def enroll_research_topic(
    request: ResearchEnrollmentRequest,
    auth: NEUAuthClient = Depends(require_serialized_auth),
):
    try:
        policy = mutation_policy("research.enroll")
        result = _api(auth).enroll(
            request.topic_id,
            batch_id=request.batch_id,
            phone=request.phone,
            email=request.email,
            reason=request.reason,
        )
        username = _username(auth)
        for resource in policy.invalidations:
            _cache_coordinator.invalidate(
                account_id=username,
                resource=resource,
            )
        _cache_coordinator.submit(
            account_id=username,
            resource="research-training",
            identity_epoch=get_auth_generation(),
            force=True,
            reason="foreground_mutation",
        )
        return result
    except ResearchTrainingError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/research-training/cancel")
def cancel_research_enrollment(
    request: ResearchCancellationRequest,
    auth: NEUAuthClient = Depends(require_serialized_auth),
):
    try:
        policy = mutation_policy("research.cancel")
        result = _api(auth).cancel_enrollment(request.topic_id)
        username = _username(auth)
        for resource in policy.invalidations:
            _cache_coordinator.invalidate(
                account_id=username,
                resource=resource,
            )
        _cache_coordinator.submit(
            account_id=username,
            resource="research-training",
            identity_epoch=get_auth_generation(),
            force=True,
            reason="foreground_mutation",
        )
        return result
    except ResearchTrainingError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
