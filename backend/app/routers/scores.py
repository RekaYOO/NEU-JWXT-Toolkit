from typing import List
from fastapi import APIRouter, HTTPException, Depends, Query

from backend.app.schemas import (
    ColumnConfig,
    CourseScoreDetailResponse,
    CourseScoreModel,
    ScoreDetailQueryRequest,
    ScoresResponse,
    TermScoresModel,
)
from backend.core.auth import NEUAuthClient
from backend.app.dependencies import (
    _cache_coordinator,
    _cache_registry,
    _cache_store,
    get_auth_generation,
    require_cached_auth_identity,
)
from backend.app.cache_support import read_cache, submit_refresh, wait_for_job
from backend.core.log import log_application_error
from backend.core.cache import CacheKey
from backend.core.cache.resources import score_detail_variant
from backend.app.presenters import score_model

router = APIRouter()

# One-cycle compatibility for tests and extensions that imported the former
# router-local mapper. New code must import app.presenters.score_model.
_score_model = score_model


def _find_cached_score(account: str, course_code: str, term: str) -> dict | None:
    entry, _stale = read_cache(account, "scores")
    if entry is None:
        return None
    for score in entry.payload.get("scores") or []:
        if (
            isinstance(score, dict)
            and str(score.get("code") or "") == course_code
            and str(score.get("term") or "") == term
        ):
            return score
    return None


def _detail_response(account: str, course_code: str, term: str):
    variant = score_detail_variant(course_code, term)
    entry = _cache_store.get(CacheKey(account, "score-details", variant))
    spec = _cache_registry.get("score-details")
    if (
        entry is None
        or entry.schema_version != spec.schema_version
        or entry.revision_algorithm_version != spec.revision_algorithm_version
        or not isinstance(entry.payload, dict)
    ):
        return None
    source = _find_cached_score(account, course_code, term)
    stale = bool(
        source is None
        or str(entry.payload.get("source_score") or "")
        != str(source.get("score") or "")
        or entry.payload.get("source_gpa") != source.get("gpa")
    )
    return CourseScoreDetailResponse(
        course_code=course_code,
        term=term,
        score=str(entry.payload.get("score") or ""),
        grade_point=str(entry.payload.get("grade_point") or ""),
        pass_=entry.payload.get("pass"),
        item_scores=entry.payload.get("item_scores") or [],
        cached_at=entry.saved_at,
        is_stale=stale,
        cache=entry.metadata(is_stale=stale),
    )


def _scores_response(entry, is_stale: bool, source: str = "local") -> ScoresResponse:
    payload = entry.payload
    scores = payload.get("scores") or []
    score_models = [
        score_model(score)
        for score in scores
    ]
    total_credits = sum(float(score.get("credit") or 0) for score in scores)
    calculated_gpa = (
        sum(
            float(score.get("gpa") or 0) * float(score.get("credit") or 0)
            for score in scores
        ) / total_credits
        if total_credits > 0
        else 0.0
    )
    return ScoresResponse(
        total_courses=len(scores),
        overall_gpa=payload.get("overall_gpa"),
        calculated_gpa=calculated_gpa,
        source=source,
        is_fresh=not is_stale,
        last_update=entry.saved_at,
        cache=entry.metadata(is_stale=is_stale),
        scores=score_models
    )


@router.get("/scores/cache", response_model=ScoresResponse)
def get_cached_scores(
    auth: NEUAuthClient = Depends(require_cached_auth_identity)
):
    """Read the current account's score cache without a remote health check."""
    entry, stale = read_cache(auth.username, "scores")
    if entry is None:
        raise HTTPException(status_code=404, detail="当前账号没有可用的本地成绩缓存")
    return _scores_response(entry, stale)


@router.get("/scores", response_model=ScoresResponse)
def get_scores(
    refresh: bool = Query(False, description="强制刷新数据"),
    auth: NEUAuthClient = Depends(require_cached_auth_identity)
):
    """
    获取成绩列表 - 智能合并本地和远程数据

    - 默认使用本地缓存
    - 缓存超过 5 分钟或 refresh=true 时提交统一后台刷新
    """
    try:
        entry, stale = read_cache(auth.username, "scores")
        submission = None
        if refresh or stale:
            submission = submit_refresh(
                auth.username,
                "scores",
                force=refresh,
                reason="manual" if refresh else "page_swr",
            )
        if entry is None or refresh:
            wait_for_job(submission.job_id if submission else None)
            entry, stale = read_cache(auth.username, "scores")
        if entry is None:
            raise HTTPException(status_code=503, detail="暂时无法获取成绩且没有本地缓存")
        return _scores_response(entry, stale)

    except HTTPException:
        raise
    except Exception as e:
        error_id = log_application_error("scores.get", e, 500)
        raise HTTPException(status_code=500, detail=f"获取成绩失败（错误编号：{error_id}）") from e


@router.get("/scores/by-term", response_model=List[TermScoresModel])
def get_scores_by_term(auth: NEUAuthClient = Depends(require_cached_auth_identity)):
    """按学期获取成绩"""
    try:
        entry, _stale = read_cache(auth.username, "scores")
        if entry is None:
            raise HTTPException(status_code=404, detail="当前账号没有可用的成绩缓存")
        grouped = {}
        for score in entry.payload.get("scores") or []:
            grouped.setdefault(
                str(score.get("term") or ""),
                {"name": str(score.get("term_display") or ""), "scores": []},
            )["scores"].append(score)
        result = []
        for term_code, term in sorted(grouped.items(), reverse=True):
            courses = [score_model(score) for score in term["scores"]]
            credits = sum(course.credit for course in courses)
            result.append(TermScoresModel(
                term_code=term_code,
                term_name=term["name"],
                courses=courses,
                total_credits=credits,
                gpa=(
                    sum(course.gpa * course.credit for course in courses) / credits
                    if credits else 0
                ),
            ))

        return result

    except HTTPException:
        raise
    except Exception as e:
        error_id = log_application_error("scores.by_term", e, 500)
        raise HTTPException(status_code=500, detail=f"获取成绩失败（错误编号：{error_id}）") from e


@router.post("/scores/refresh")
def refresh_scores(auth: NEUAuthClient = Depends(require_cached_auth_identity)):
    """手动刷新成绩数据"""
    submission = submit_refresh(
        auth.username, "scores", force=True, reason="manual"
    )
    job = wait_for_job(submission.job_id)
    if job and job.status.value == "completed":
        return {
            "success": True,
            "job_id": job.job_id,
            "revision": job.revision,
            "changed": job.changed,
            "diff": dict(job.changes),
        }
    raise HTTPException(
        status_code=503,
        detail=f"刷新失败: {getattr(job, 'error_kind', None) or 'unknown'}",
    )


@router.get("/scores/details/cache", response_model=CourseScoreDetailResponse)
def get_score_detail_cache(
    course_code: str = Query(..., min_length=1, max_length=128),
    term: str = Query(..., min_length=1, max_length=64),
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
):
    detail = _detail_response(str(auth.username), course_code, term)
    if detail is None:
        raise HTTPException(status_code=404, detail="本地没有该课程的分项成绩缓存")
    return detail


@router.post("/scores/details/query")
def query_score_detail(
    request: ScoreDetailQueryRequest,
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
):
    account = str(auth.username or "")
    score = _find_cached_score(account, request.course_code, request.term)
    if score is None:
        raise HTTPException(status_code=404, detail="当前成绩总表中没有该课程")
    if not str(score.get("detail_ref") or ""):
        raise HTTPException(status_code=409, detail="课程详情标识不可用，请先刷新总成绩")
    submission = _cache_coordinator.submit(
        account_id=account,
        resource="score-details",
        variant=score_detail_variant(request.course_code, request.term),
        identity_epoch=get_auth_generation(),
        force=True,
        reason="manual",
    )
    return {
        "status": submission.status.value,
        "resource": "score-details",
        "job_id": submission.job_id,
        "revision": submission.revision,
        "is_stale": submission.is_stale,
    }


@router.get("/columns/default")
async def get_default_columns() -> List[ColumnConfig]:
    """获取默认列配置"""
    return [
        ColumnConfig(key="name", title="课程名称", visible=True, width=200),
        ColumnConfig(key="code", title="课程代码", visible=True, width=120),
        ColumnConfig(key="score", title="成绩", visible=True, width=80),
        ColumnConfig(key="gpa", title="绩点", visible=True, width=80),
        ColumnConfig(key="credit", title="学分", visible=True, width=80),
        ColumnConfig(key="term_display", title="学期", visible=True, width=180),
        ColumnConfig(key="course_type", title="课程性质", visible=True, width=100),
        ColumnConfig(key="course_category", title="课程类别", visible=False, width=150),
        ColumnConfig(key="general_category", title="通识类别", visible=False, width=150),
        ColumnConfig(key="exam_type", title="考核方式", visible=False, width=100),
        ColumnConfig(key="exam_status", title="考试状态", visible=False, width=100),
        ColumnConfig(key="is_passed", title="状态", visible=True, width=80),
    ]
