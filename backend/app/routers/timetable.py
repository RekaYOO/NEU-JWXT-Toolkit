"""Read-only timetable API routes."""

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.cache_support import wait_for_job
from backend.app.dependencies import (
    auth_generation_is_current,
    get_auth_generation,
    get_cache_coordinator,
    remote_session_guard,
    require_cached_auth_identity,
    require_serialized_auth,
)
from backend.app.schemas.timetable import (
    TimetableContextRequest,
    TimetableContextResponse,
    TimetableScheduleRequest,
    TimetableScheduleResponse,
    TimetableTargetSearchRequest,
    TimetableTargetSearchResponse,
    TimetableTargetFilterOptionsRequest,
    TimetableTargetFilterOptionsResponse,
    TimetableTermsResponse,
    PersonalTimetableResponse,
)
from backend.core.auth import NEUAuthClient
from backend.core.auth.client import NEULoginError
from backend.core.log import log_application_error
from backend.core.cache.resources import personal_timetable_variant
from backend.core.scheduling import meeting_extension, normalize_meeting
from backend.core.timetable import TimetableError


router = APIRouter(prefix="/timetable", tags=["timetable"])


def _require_target(mode: str, target_id: str) -> None:
    if mode != "personal" and not target_id.strip():
        raise HTTPException(status_code=422, detail="班级、教师或教室课表必须选择查询对象")


def _remote_failure(operation: str, error: Exception) -> HTTPException:
    error_id = log_application_error(f"timetable.{operation}", error, 502)
    return HTTPException(
        status_code=502,
        detail=f"教务系统课表服务暂时不可用（错误编号：{error_id}）",
    )


def _authentication_failure() -> HTTPException:
    return HTTPException(status_code=401, detail="登录状态已失效，请重新登录")


def _personal_response(entry, stale: bool) -> PersonalTimetableResponse:
    return PersonalTimetableResponse(
        **entry.payload,
        source="local",
        is_fresh=not stale,
        last_update=entry.saved_at,
        cache=entry.metadata(is_stale=stale),
    )


def _cache_entry_is_compatible(entry, spec) -> bool:
    return bool(
        entry is not None
        and entry.schema_version == spec.schema_version
        and entry.revision_algorithm_version == spec.revision_algorithm_version
        and entry.payload_type == spec.payload_type
    )


def _terms_for_auth(auth: NEUAuthClient):
    terms = auth.timetable.get_cached_terms()
    if terms is not None:
        return terms
    generation = get_auth_generation()
    account = str(auth.username)
    with remote_session_guard():
        if not auth_generation_is_current(generation, account):
            raise _authentication_failure()
        terms = auth.timetable.get_cached_terms()
        if terms is None:
            terms = auth.timetable.get_terms()
    return terms


def _require_current_personal_term(auth: NEUAuthClient, requested_term: str) -> str:
    try:
        terms = _terms_for_auth(auth)
    except NEULoginError as error:
        raise _authentication_failure() from error
    except TimetableError as error:
        raise _remote_failure("current_term", error) from error
    current_term = next(
        (str(item.get("code") or "") for item in terms if item.get("current")),
        "",
    )
    if not current_term:
        raise HTTPException(status_code=503, detail="教务系统未提供明确的当前学期，无法使用课表缓存")
    if requested_term != current_term:
        raise HTTPException(
            status_code=409,
            detail="个人课表缓存仅用于当前学期；其他学期请使用实时课表查询",
        )
    return current_term


@router.get("/terms", response_model=TimetableTermsResponse)
def get_timetable_terms(auth: NEUAuthClient = Depends(require_cached_auth_identity)):
    try:
        terms = _terms_for_auth(auth)
        current = next((item["code"] for item in terms if item["current"]), None)
        return TimetableTermsResponse(terms=terms, current=current)
    except NEULoginError as error:
        raise _authentication_failure() from error
    except TimetableError as error:
        raise _remote_failure("terms", error) from error


@router.get("/personal", response_model=PersonalTimetableResponse)
def get_personal_timetable(
    term_code: str = Query(
        ..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$"
    ),
    refresh: bool = Query(False),
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
):
    """Read/refresh the official current personal term through shared cache."""
    _require_current_personal_term(auth, term_code)
    coordinator = get_cache_coordinator()
    account = str(auth.username)
    variant = personal_timetable_variant(term_code)
    try:
        entry, stale = coordinator.read(
            account_id=account,
            resource="personal-timetable",
            variant=variant,
        )
        spec = coordinator.registry.get("personal-timetable")
        if entry is not None and not _cache_entry_is_compatible(entry, spec):
            entry = None
            stale = True
        submission = None
        if refresh or stale:
            submission = coordinator.submit(
                account_id=account,
                resource="personal-timetable",
                variant=variant,
                identity_epoch=get_auth_generation(),
                force=refresh,
                reason="manual" if refresh else "page_swr",
            )
        if entry is None or refresh:
            wait_for_job(submission.job_id if submission else None)
            entry, stale = coordinator.read(
                account_id=account,
                resource="personal-timetable",
                variant=variant,
            )
            # A failed/cancelled/timed-out refresh can leave the incompatible
            # database row in place. Never let that row cross the HTTP boundary.
            if not _cache_entry_is_compatible(entry, spec):
                entry = None
                stale = True
        if entry is None:
            raise HTTPException(status_code=503, detail="暂时无法获取个人课表且没有本地缓存")
        return _personal_response(entry, stale)
    except HTTPException:
        raise
    except Exception as error:
        error_id = log_application_error("timetable.personal", error, 500)
        raise HTTPException(
            status_code=500,
            detail=f"获取个人课表失败（错误编号：{error_id}）",
        ) from error


@router.post("/context", response_model=TimetableContextResponse)
def get_timetable_context(
    request: TimetableContextRequest,
    auth: NEUAuthClient = Depends(require_serialized_auth),
):
    _require_target(request.mode, request.target_id)
    try:
        campuses = auth.timetable.get_campuses(
            request.term_code,
            mode=request.mode,
            target_id=request.target_id,
        )
        available_codes = {item["code"] for item in campuses}
        campus_code = request.campus_code if request.campus_code in available_codes else ""
        if not campus_code and campuses:
            campus_code = campuses[0]["code"]
        sections = (
            auth.timetable.get_sections(
                request.term_code,
                mode=request.mode,
                campus_code=campus_code,
            )
            if campus_code
            else []
        )
        return TimetableContextResponse(
            campuses=campuses,
            weeks=auth.timetable.get_weeks(request.term_code),
            sections=sections,
        )
    except NEULoginError as error:
        raise _authentication_failure() from error
    except TimetableError as error:
        raise _remote_failure("context", error) from error


@router.post("/targets/search", response_model=TimetableTargetSearchResponse)
def search_timetable_targets(
    request: TimetableTargetSearchRequest,
    auth: NEUAuthClient = Depends(require_serialized_auth),
):
    try:
        return TimetableTargetSearchResponse(
            **auth.timetable.search_targets(
                request.mode,
                request.term_code,
                keyword=request.keyword,
                page=request.page,
                page_size=request.page_size,
                filters=request.filters.model_dump(exclude_none=True, exclude_defaults=True),
            )
        )
    except NEULoginError as error:
        raise _authentication_failure() from error
    except TimetableError as error:
        raise _remote_failure("target_search", error) from error


@router.post(
    "/targets/filter-options",
    response_model=TimetableTargetFilterOptionsResponse,
)
def get_timetable_target_filter_options(
    request: TimetableTargetFilterOptionsRequest,
    auth: NEUAuthClient = Depends(require_serialized_auth),
):
    try:
        return TimetableTargetFilterOptionsResponse(
            **auth.timetable.get_target_filter_options(
                request.mode,
                request.term_code,
                keys=request.keys,
                filters=request.filters.model_dump(exclude_none=True, exclude_defaults=True),
            )
        )
    except NEULoginError as error:
        raise _authentication_failure() from error
    except TimetableError as error:
        raise _remote_failure("target_filter_options", error) from error


@router.post("/schedule", response_model=TimetableScheduleResponse)
def get_timetable_schedule(
    request: TimetableScheduleRequest,
    auth: NEUAuthClient = Depends(require_serialized_auth),
):
    _require_target(request.mode, request.target_id)
    try:
        payload = auth.timetable.get_schedule(
            mode=request.mode,
            term_code=request.term_code,
            campus_code=request.campus_code,
            target_id=request.target_id,
            week=request.week,
        )
        payload["courses"] = [
            {
                **course,
                **meeting_extension(
                    normalize_meeting(
                        course,
                        term_code=request.term_code,
                        default_week=request.week,
                        default_source="personal_timetable"
                        if request.mode == "personal"
                        else f"{request.mode}_timetable",
                    )
                ),
            }
            for course in payload["courses"]
        ]
        return TimetableScheduleResponse(
            mode=request.mode,
            term_code=request.term_code,
            campus_code=request.campus_code,
            target_id=request.target_id,
            week=request.week,
            **payload,
        )
    except NEULoginError as error:
        raise _authentication_failure() from error
    except TimetableError as error:
        raise _remote_failure("schedule", error) from error
