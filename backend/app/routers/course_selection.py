"""Stateless API for the new course-weight game model."""

from __future__ import annotations

import logging
import math
from threading import BoundedSemaphore
import time
from datetime import datetime
import requests

from fastapi import APIRouter, HTTPException, Response, Depends, Query

from backend.app.schemas.course_selection import (
    CourseSelectionOptimizeRequest,
    CourseSelectionOptimizeResponse,
    JwxkSettingsUpdate,
    JwxkStatusResponse,
    JwxkBatchRequest,
    JwxkCourseSearchRequest,
    JwxkCourseSearchResponse,
    JwxkSelectedResponse,
    JwxkBatchConfirmRequest,
    JwxkCourseSelectRequest,
    JwxkCourseDeselectRequest,
    JwxkMutationResponse,
    JwxkCatalogSearchRequest,
    JwxkCatalogSearchResponse,
    JwxkCatalogDetailRequest,
    JwxkCatalogDetailResponse,
    JwxkEligibilityRequest,
    JwxkEligibilityResponse,
    JwxkSelectionScheduleResponse,
    JwxkPlanPreviewRequest,
    JwxkPlanPreviewResponse,
    JwxkSavedPlanRequest,
    JwxkWeightPlanRequest,
    JwxkWeightConfigResponse,
    JwxkAutomationTaskRequest,
    JwxkAutomationTaskAction,
)
from backend.app.dependencies import (
    get_storage, peek_auth_client, remote_session_guard, require_cached_auth_identity, require_serialized_auth,
    get_cache_coordinator,
    get_course_selection_automation_service,
)
from backend.core.auth.client import NEUAuthClient, NEULoginError
from backend.core.cache import mutation_policy
from backend.core.course_selection import (
    CourseMarket,
    CourseSelectionError,
    MarketSnapshot,
    SelectionPolicy,
    optimize_course_weights,
    JWXK_CAS_SERVICE,
    JwxkError,
    JwxkPublicClient,
    JwxkSessionClient,
    group_course_rows,
    jwxk_campus_label,
    normalize_saved_plan_items,
    normalize_jwxk_campus_code,
    resolve_network_mode,
    WeightCandidate,
    WeightGroupTarget,
    WeightMarketCourse,
    WeightOptimizationError,
    WeightPolicy,
    optimize_grouped_weights,
)
from backend.core.storage import Storage
from backend.core.course_selection.model import TieRule
from backend.core.log import log_application_error
from backend.core.cache.resources import personal_timetable_variant
from backend.core.scheduling import check_conflicts, normalize_meeting


router = APIRouter(prefix="/course-selection", tags=["course-selection"])
logger = logging.getLogger(__name__)
_solver_slots = BoundedSemaphore(value=2)
_JWXK_CONFIG_KEY = "course_selection"
_JWXK_SCOPE_NAMES = {
    "TJKC": "任务推荐班课程", "FANKC": "培养方案内课",
    "FAWKC": "培养方案外课程", "XGKC": "通识选修课",
    "CXKC": "重修课程", "TYKC": "体育项目", "FXKC": "辅修课程",
    "ALLKC": "全校课程查询", "BYKC": "本研课程", "ZYNKC": "专业内课程",
}


def _read_jwxk_preference(storage: Storage) -> str:
    config = storage.load_config()
    selection = config.get(_JWXK_CONFIG_KEY) if isinstance(config, dict) else None
    value = selection.get("network_mode") if isinstance(selection, dict) else "follow"
    return value if value in {"follow", "direct", "webvpn"} else "follow"


@router.get("/jwxk/status", response_model=JwxkStatusResponse)
def get_jwxk_status(
    response: Response,
    storage: Storage = Depends(get_storage),
) -> JwxkStatusResponse:
    response.headers["Cache-Control"] = "no-store"
    preference = _read_jwxk_preference(storage)
    primary = peek_auth_client()
    primary_mode = str(getattr(primary, "active_mode", "direct") or "direct")
    effective = resolve_network_mode(preference, primary_mode)
    primary_authenticated = bool(primary and getattr(primary, "is_logged_in", False))
    service_authenticated = False
    authenticated_batches = None
    account_context = {}
    if primary_authenticated:
        try:
            with remote_session_guard():
                if peek_auth_client() is primary:
                    context = JwxkSessionClient(primary, network_mode=effective).get_context()
                    account_context = context
                    authenticated_batches = context["batches"]
                    service_authenticated = True
        except (NEULoginError, JwxkError, requests.RequestException, ValueError, RuntimeError) as error:
            logger.info("jwxk service session unavailable error=%s", type(error).__name__)
    try:
        source_batches = authenticated_batches
        if source_batches is None:
            source_batches = JwxkPublicClient().get_batches()
        batches = [item.to_dict() for item in source_batches]
        return JwxkStatusResponse(
            available=True,
            network_mode=preference,
            effective_network_mode=effective,
            cas_service=JWXK_CAS_SERVICE,
            primary_authenticated=primary_authenticated,
            service_authenticated=service_authenticated,
            authenticated=service_authenticated,
            official_time=str(account_context.get("official_time") or ""),
            online_count=account_context.get("online_count"),
            current_campus=str(account_context.get("current_campus") or ""),
            current_campus_name=str(account_context.get("current_campus_name") or ""),
            batches=batches,
            message=(
                "已按账号资格和官方时间读取全部轮次。"
                if service_authenticated
                else "当前仅展示公开批次；登录后可读取账号轮次和课程。"
            ),
        )
    except (JwxkError, requests.RequestException) as error:
        logger.warning("jwxk public status unavailable error=%s", type(error).__name__)
        return JwxkStatusResponse(
            available=False,
            network_mode=preference,
            effective_network_mode=effective,
            cas_service=JWXK_CAS_SERVICE,
            primary_authenticated=primary_authenticated,
            service_authenticated=service_authenticated,
            authenticated=service_authenticated,
            official_time=str(account_context.get("official_time") or ""),
            online_count=account_context.get("online_count"),
            current_campus=str(account_context.get("current_campus") or ""),
            current_campus_name=str(account_context.get("current_campus_name") or ""),
            batches=[],
            message="暂时无法读取选课系统批次，请稍后重试。",
        )


@router.put("/jwxk/settings", response_model=JwxkStatusResponse)
def update_jwxk_settings(
    request: JwxkSettingsUpdate,
    response: Response,
    storage: Storage = Depends(get_storage),
) -> JwxkStatusResponse:
    config = storage.load_config()
    config = dict(config) if isinstance(config, dict) else {}
    config[_JWXK_CONFIG_KEY] = {"network_mode": request.network_mode}
    storage.save_config(config)
    return get_jwxk_status(response, storage)


def _run_jwxk_read(storage: Storage, operation):
    primary = peek_auth_client()
    if not primary or not getattr(primary, "is_logged_in", False):
        raise HTTPException(status_code=401, detail="请先登录后再访问选课系统")
    preference = _read_jwxk_preference(storage)
    effective = resolve_network_mode(
        preference, str(getattr(primary, "active_mode", "direct") or "direct")
    )
    try:
        with remote_session_guard():
            if peek_auth_client() is not primary:
                raise NEULoginError("登录身份已切换，请重试")
            client = JwxkSessionClient(primary, network_mode=effective)
            return operation(client)
    except NEULoginError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except (JwxkError, requests.RequestException, ValueError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


def _merge_catalog_option_values(options: dict, archive: dict | None) -> dict:
    merged = dict(options or {})
    courses = archive.get("courses") if isinstance(archive, dict) else []
    for option_key, course_key in (
        ("course_natures", "course_nature"),
        ("course_categories", "course_category"),
        ("general_elective_categories", "general_elective_category"),
        ("departments", "department"),
    ):
        values = {}
        for item in merged.get(option_key) or []:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value") or "").strip()
            if value:
                values[value] = {**item, "value": value, "label": str(item.get("label") or value)}
        for course in courses or []:
            value = str(course.get(course_key) or "").strip()
            if value:
                values.setdefault(value, {"value": value, "label": value})
        merged[option_key] = sorted(values.values(), key=lambda item: str(item.get("label") or ""))
    campus_values = {}
    for item in merged.get("campuses") or []:
        if not isinstance(item, dict):
            continue
        code = normalize_jwxk_campus_code(item.get("value"))
        if code:
            campus_values[code] = {
                **item, "value": code,
                "label": jwxk_campus_label(code, item.get("label")),
            }
    for course in courses or []:
        candidates = [{
            "code": course.get("campus"), "name": course.get("campus_name"),
        }, *[
            {"code": item.get("campus"), "name": item.get("campus_name")}
            for item in course.get("schedules") or [] if isinstance(item, dict)
        ]]
        for candidate in candidates:
            code = normalize_jwxk_campus_code(candidate.get("code"))
            name = jwxk_campus_label(code, candidate.get("name"))
            if code:
                campus_values[code] = {
                    "value": code,
                    "label": name or campus_values.get(code, {}).get("label") or code,
                }
    merged["campuses"] = sorted(
        campus_values.values(), key=lambda item: str(item.get("label") or "")
    )
    return merged


def _archive_scope_options(archive: dict | None) -> list[dict[str, str]]:
    courses = archive.get("courses") if isinstance(archive, dict) else []
    codes = sorted({
        str(scope or "")
        for course in courses or []
        for scope in [
            course.get("teaching_class_type"),
            *(course.get("source_scopes") or []),
        ]
        if str(scope or "") not in {"", "ALL", "ROUND"}
    })
    return [
        {"code": "ALL", "name": "所有课程"},
        {"code": "ROUND", "name": "本轮课程"},
        *[{"code": code, "name": _JWXK_SCOPE_NAMES.get(code, code)} for code in codes],
    ]


@router.post("/jwxk/courses/search", response_model=JwxkCourseSearchResponse)
def search_jwxk_courses(
    request: JwxkCourseSearchRequest,
    response: Response,
    storage: Storage = Depends(get_storage),
) -> JwxkCourseSearchResponse:
    response.headers["Cache-Control"] = "no-store"
    result = _run_jwxk_read(storage, lambda client: client.search_courses(
        batch_code=request.batch_code,
        teaching_class_type=request.teaching_class_type,
        page_number=request.page_number,
        page_size=request.page_size,
        keyword=request.keyword.strip(),
        campus=request.campus,
        order_by=request.order_by,
        filters=request.filters,
    ))
    return JwxkCourseSearchResponse.model_validate(result)


@router.post("/jwxk/selected", response_model=JwxkSelectedResponse)
def get_jwxk_selected(
    request: JwxkBatchRequest,
    response: Response,
    include_market: bool = Query(default=True),
    storage: Storage = Depends(get_storage),
) -> JwxkSelectedResponse:
    response.headers["Cache-Control"] = "no-store"
    def load(client):
        result = client.get_selected(batch_code=request.batch_code)
        rows = [
            item for key in ("selected", "volunteered", "withdrawal")
            for item in result.get(key) or [] if isinstance(item, dict)
        ]
        if not include_market:
            return result
        live_by_class: dict[str, dict] = {}
        seen: set[tuple[str, str]] = set()
        for item in rows:
            course_code = str(item.get("course_code") or "").strip()
            class_type = str(item.get("teaching_class_type") or "ALLKC").strip() or "ALLKC"
            lookup = (course_code.upper(), class_type)
            if not course_code or lookup in seen:
                continue
            seen.add(lookup)
            try:
                live = client.search_courses(
                    batch_code=request.batch_code,
                    teaching_class_type=class_type,
                    page_number=1,
                    page_size=50,
                    keyword=course_code,
                    campus="",
                    order_by="",
                    filters={},
                )
            except (NEULoginError, JwxkError, requests.RequestException):
                continue
            for course in live.get("courses") or []:
                class_id = str(course.get("class_id") or "")
                if class_id:
                    live_by_class[class_id] = course
        dynamic_fields = (
            "capacity", "selected_count", "first_choice_count",
            "weight_participant_count", "market_participant_count",
            "market_participant_label", "capacity_updated_at", "full",
        )
        for key in ("selected", "volunteered", "withdrawal"):
            result[key] = [{
                **item,
                **{
                    field: live_by_class[str(item.get("class_id") or "")][field]
                    for field in dynamic_fields
                    if str(item.get("class_id") or "") in live_by_class
                    and field in live_by_class[str(item.get("class_id") or "")]
                },
            } for item in result.get(key) or []]
        return result

    result = _run_jwxk_read(storage, load)
    return JwxkSelectedResponse.model_validate(result)


@router.post("/jwxk/catalog/search", response_model=JwxkCatalogSearchResponse)
def search_jwxk_catalog(
    request: JwxkCatalogSearchRequest,
    response: Response,
    storage: Storage = Depends(get_storage),
) -> JwxkCatalogSearchResponse:
    response.headers["Cache-Control"] = "no-store"
    automation = get_course_selection_automation_service()
    if request.local_only:
        primary = peek_auth_client()
        if not primary or not getattr(primary, "is_logged_in", False):
            raise HTTPException(status_code=401, detail="请先登录后再访问选课系统")
        account = str(getattr(primary, "username", "") or "")
        archive = automation.get_catalog_archive_view(account, request.batch_code)
        archived = automation.query_catalog_archive(
            account,
            batch_code=request.batch_code,
            page_number=request.page_number,
            page_size=request.page_size,
            scope=request.scope,
            keyword=request.keyword.strip(),
            campus=request.campus,
            filters=request.filters,
            time_slot=request.time_slot.model_dump() if request.time_slot else None,
        )
        return JwxkCatalogSearchResponse.model_validate({
            "total": int((archived or {}).get("total") or 0),
            "scope": request.scope,
            "scope_options": _archive_scope_options(archive),
            "groups": (archived or {}).get("groups") or [],
            "cache_hit": archived is not None,
            "data_source": "local",
            "sync_status": str((archived or {}).get("sync_status") or ""),
        })
    result = _run_jwxk_read(storage, lambda client: client.search_catalog(
        batch_code=request.batch_code,
        page_number=request.page_number,
        page_size=request.page_size,
        keyword=request.keyword.strip(),
        scope=request.scope,
        campus=request.campus,
        order_by=request.order_by,
        filters=request.filters,
        time_slot=request.time_slot.model_dump() if request.time_slot else None,
    ))
    account = str(result.pop("_account", "") or "")
    batch = result.pop("_batch", {})
    automation.merge_catalog_archive(
        account,
        batch=batch,
        scope=str(result.get("scope") or request.scope),
        groups=result.get("groups") or [],
    )
    automation.schedule_catalog_sync(account, batch=batch)
    archived = automation.query_catalog_archive(
        account,
        batch_code=request.batch_code,
        page_number=request.page_number,
        page_size=request.page_size,
        scope=str(result.get("scope") or request.scope),
        keyword=request.keyword.strip(),
        campus=request.campus,
        filters=request.filters,
        time_slot=request.time_slot.model_dump() if request.time_slot else None,
    )
    if archived and (
        archived.get("catalog_complete")
        or archived.get("sync_status") == "complete"
        or not result.get("groups")
    ):
        result["total"] = archived["total"]
        result["groups"] = archived["groups"]
    result.update({
        "cache_hit": False,
        "data_source": "remote",
        "sync_status": str((archived or {}).get("sync_status") or ""),
    })
    return JwxkCatalogSearchResponse.model_validate(result)


@router.post("/jwxk/catalog/classes")
def get_jwxk_catalog_classes(
    request: JwxkCatalogSearchRequest,
    response: Response,
    storage: Storage = Depends(get_storage),
):
    response.headers["Cache-Control"] = "no-store"
    result = _run_jwxk_read(storage, lambda client: client.search_catalog(
        batch_code=request.batch_code, page_number=1, page_size=50,
        keyword=request.keyword.strip(), scope=request.scope,
        campus=request.campus, order_by=request.order_by, filters=request.filters,
        time_slot=request.time_slot.model_dump() if request.time_slot else None,
    ))
    account = str(result.pop("_account", "") or "")
    batch = result.pop("_batch", {})
    get_course_selection_automation_service().merge_catalog_archive(
        account,
        batch=batch,
        scope=str(result.get("scope") or request.scope),
        groups=result.get("groups") or [],
    )
    return {"groups": result.get("groups", [])}


@router.post("/jwxk/catalog/detail", response_model=JwxkCatalogDetailResponse)
def get_jwxk_catalog_detail(
    request: JwxkCatalogDetailRequest,
    response: Response,
    storage: Storage = Depends(get_storage),
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
) -> JwxkCatalogDetailResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        result = _run_jwxk_read(storage, lambda client: client.get_catalog_detail(
            batch_code=request.batch_code,
            teaching_class_type=request.teaching_class_type,
            course_code=request.course_code,
            class_id=request.class_id,
        ))
    except HTTPException as error:
        if error.status_code != 502:
            raise
        archive = next((item for item in (
            get_course_selection_automation_service().list_catalog_archives(str(auth.username))
        ) if item.get("batch_code") == request.batch_code), None)
        archived_class = next((item for item in (archive or {}).get("courses") or [] if (
            str(item.get("class_id") or "") == request.class_id
            and str(item.get("course_code") or "") == request.course_code
        )), None)
        if archived_class is None:
            raise
        group = group_course_rows([archived_class])[0]
        teaching_class = group["classes"][0]
        result = {
            "course": {
                "course_code": group.get("course_code", ""),
                "course_name": group.get("course_name", ""),
                "credits": group.get("credits", ""),
                "hours": group.get("hours", ""),
                "department": group.get("department", ""),
                "course_nature": group.get("course_nature", ""),
                "course_category": group.get("course_category", ""),
                "course_categories": group.get("course_categories", []),
                "normalized_course_category": group.get("normalized_course_category", ""),
                "general_elective_category_code": group.get("general_elective_category_code", ""),
                "general_elective_category": group.get("general_elective_category", ""),
                "exam_type_code": group.get("exam_type_code", ""),
                "exam_type": group.get("exam_type", ""),
                "score_scale_code": group.get("score_scale_code", ""),
                "score_scale": group.get("score_scale", ""),
            },
            "teaching_class": teaching_class,
        }
    return JwxkCatalogDetailResponse.model_validate(result)


@router.post("/jwxk/catalog/filter-options")
def get_jwxk_catalog_filter_options(
    request: JwxkBatchRequest,
    response: Response,
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
    storage: Storage = Depends(get_storage),
):
    response.headers["Cache-Control"] = "no-store"
    archive = get_course_selection_automation_service().get_catalog_archive_view(
        str(auth.username), request.batch_code,
    )
    if archive and archive.get("courses"):
        return _merge_catalog_option_values({
            "scopes": _archive_scope_options(archive),
            "availability": [
                {"value": "selectable", "label": "本轮可选"},
                {"value": "available", "label": "仍有余量"},
                {"value": "conflict_free", "label": "官方无冲突"},
                {"value": "selected", "label": "已经选择"},
            ],
            "weekdays": [
                {"value": str(day), "label": f"周{label}"}
                for day, label in enumerate("一二三四五六日", 1)
            ],
            "sections": [
                {"value": str(section), "label": f"第{section}节"}
                for section in range(1, 31)
            ],
        }, archive)
    result = _run_jwxk_read(
        storage, lambda client: client.get_catalog_filter_options(batch_code=request.batch_code)
    )
    return _merge_catalog_option_values(result, archive)


@router.post("/jwxk/catalog/eligibility", response_model=JwxkEligibilityResponse)
def check_jwxk_catalog_eligibility(
    request: JwxkEligibilityRequest,
    response: Response,
    storage: Storage = Depends(get_storage),
) -> JwxkEligibilityResponse:
    response.headers["Cache-Control"] = "no-store"
    result = _run_jwxk_read(
        storage,
        lambda client: client.check_course_eligibility(
            batch_code=request.batch_code,
            class_ids=request.class_ids,
        ),
    )
    account = str(result.pop("_account", "") or "")
    get_course_selection_automation_service().update_archive_eligibility(
        account,
        batch_code=request.batch_code,
        results=result.get("results") or [],
    )
    return JwxkEligibilityResponse.model_validate(result)


@router.get("/jwxk/catalog/archives")
def list_jwxk_catalog_archives(
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
):
    archives = get_course_selection_automation_service().list_catalog_archives(str(auth.username))
    return {"archives": [
        {key: value for key, value in archive.items() if key != "account"}
        for archive in archives
    ]}


@router.delete("/jwxk/catalog/archives/{archive_id}")
def delete_jwxk_catalog_archive(
    archive_id: str,
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
):
    if len(archive_id) != 32 or not archive_id.isalnum():
        raise HTTPException(status_code=422, detail="历史记录标识无效")
    deleted = get_course_selection_automation_service().delete_catalog_archive(
        str(auth.username), archive_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="历史记录不存在")
    return {"deleted": True}


@router.post("/jwxk/schedule", response_model=JwxkSelectionScheduleResponse)
def get_jwxk_schedule(
    request: JwxkBatchRequest,
    response: Response,
    storage: Storage = Depends(get_storage),
) -> JwxkSelectionScheduleResponse:
    response.headers["Cache-Control"] = "no-store"
    return JwxkSelectionScheduleResponse.model_validate(_run_jwxk_read(
        storage, lambda client: client.get_selection_schedule(batch_code=request.batch_code)
    ))


@router.post("/jwxk/plan/preview", response_model=JwxkPlanPreviewResponse)
def preview_jwxk_plan(
    request: JwxkPlanPreviewRequest,
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
) -> JwxkPlanPreviewResponse:
    entry, baseline_stale = get_cache_coordinator().read(
        account_id=str(auth.username),
        resource="personal-timetable",
        variant=personal_timetable_variant(request.term_code),
    )
    payload = (
        entry.payload
        if entry is not None
        and isinstance(entry.payload, dict)
        and str(entry.payload.get("term_code") or "") == request.term_code
        else None
    )
    baseline_available = isinstance(payload, dict) and isinstance(payload.get("courses"), list)
    baseline = [
        normalize_meeting(item, term_code=request.term_code, default_source="personal_timetable")
        for item in (payload.get("courses") if baseline_available else []) if isinstance(item, dict)
    ]
    candidates = [
        normalize_meeting(item, term_code=request.term_code, default_source="jwxk_plan")
        for item in request.meetings if isinstance(item, dict)
    ]
    personal_results = check_conflicts(baseline, candidates, ignore_same_course=True) if baseline_available else ()
    internal_results = check_conflicts(candidates, candidates, ignore_same_course=True)
    results = []
    for index, candidate in enumerate(candidates):
        personal = personal_results[index] if baseline_available else None
        internal = internal_results[index]
        personal_matches = list(personal.matches if personal else ())
        internal_matches = list(internal.matches)
        statuses = [internal.status.value, personal.status.value if personal else "unknown"]
        status = "conflict" if "conflict" in statuses else "unknown" if "unknown" in statuses else "clear"
        results.append({
            "candidate_id": candidate.source_id or candidate.meeting_id,
            "candidate_meeting_id": candidate.meeting_id,
            "status": status,
            "matches": [{
                "baseline_meeting_id": match.baseline_meeting_id,
                "baseline_course_name": match.baseline_course_name,
                "status": match.status.value,
                "reason": match.reason,
                "source": source,
                "overlapping_weeks": list(match.overlapping_weeks),
                "weekday": match.weekday,
                "start_section": match.start_section,
                "end_section": match.end_section,
            } for source, matches in (
                ("personal_timetable", personal_matches),
                ("selection_plan", internal_matches),
            ) for match in matches],
        })
    return JwxkPlanPreviewResponse(
        term_code=request.term_code,
        baseline_available=baseline_available,
        baseline_stale=bool(baseline_stale),
        results=results,
    )


def _plan_config(storage: Storage) -> dict:
    config = storage.load_config()
    return dict(config) if isinstance(config, dict) else {}


def _archived_batch_snapshot(batch_code: str, archive: dict | None) -> dict | None:
    if not isinstance(archive, dict):
        return None
    selection_type_code = str(archive.get("selection_type_code") or "")
    return {
        "code": batch_code,
        "name": str(archive.get("batch_name") or "选课轮次"),
        "term_code": str(archive.get("term_code") or ""),
        "term_name": str(archive.get("term_name") or ""),
        "selection_type_code": selection_type_code,
        "selection_type": {"02": "抢选", "04": "权重"}.get(selection_type_code, "选课"),
        "begin_time": str(archive.get("begin_time") or ""),
        "end_time": str(archive.get("end_time") or ""),
    }


@router.post("/jwxk/plan/read")
def read_jwxk_plan(
    request: JwxkBatchRequest,
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
    storage: Storage = Depends(get_storage),
):
    config = _plan_config(storage)
    key = f"{auth.username}:{request.batch_code}"
    archive = get_course_selection_automation_service().get_catalog_archive_view(
        str(auth.username), request.batch_code,
    )
    batch_snapshot = _archived_batch_snapshot(request.batch_code, archive)
    payload = (config.get("course_selection_plans") or {}).get(key) or {
        "batch_code": request.batch_code,
        "term_code": str((archive or {}).get("term_code") or ""),
        "groups": [],
        "items": [],
    }
    # A payload written by an older frontend race must never leak into another
    # round merely because it happens to sit under the wrong config key.
    if str(payload.get("batch_code") or request.batch_code) != request.batch_code:
        return {
            "batch_code": request.batch_code,
            "term_code": str((archive or {}).get("term_code") or ""),
            "batch": batch_snapshot,
            "groups": [],
            "items": [],
        }
    return {
        **payload,
        "batch_code": request.batch_code,
        "term_code": str(payload.get("term_code") or (archive or {}).get("term_code") or ""),
        "batch": batch_snapshot,
        "groups": payload.get("groups") if isinstance(payload.get("groups"), list) else [],
        "items": normalize_saved_plan_items(payload.get("items")),
    }


@router.post("/jwxk/plan/save")
def save_jwxk_plan(
    request: JwxkSavedPlanRequest,
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
    storage: Storage = Depends(get_storage),
):
    config = _plan_config(storage)
    plans = dict(config.get("course_selection_plans") or {})
    key = f"{auth.username}:{request.batch_code}"
    plans[key] = request.model_dump()
    plans[key]["items"] = normalize_saved_plan_items(plans[key].get("items"))
    config["course_selection_plans"] = plans
    storage.save_config(config)
    return plans[key]


@router.post("/jwxk/automation/tasks")
def create_jwxk_automation_task(
    request: JwxkAutomationTaskRequest,
    auth: NEUAuthClient = Depends(require_serialized_auth),
    storage: Storage = Depends(get_storage),
):
    archive = next((item for item in (
        get_course_selection_automation_service().list_catalog_archives(str(auth.username))
    ) if item.get("batch_code") == request.batch_code), None)
    selection_type_code = str((archive or {}).get("selection_type_code") or "")
    if not selection_type_code:
        try:
            context = _jwxk_mutation_client(auth, storage).get_context()
        except JwxkError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except NEULoginError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except requests.RequestException as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        batch = next((item for item in context.get("batches") or [] if item.code == request.batch_code), None)
        selection_type_code = str(getattr(batch, "selection_type_code", "") or "")
    if request.task_type == "weight_strategy" and selection_type_code != "04":
        raise HTTPException(status_code=422, detail="策略投权只适用于权重选课轮次")
    if request.task_type in {"selection", "vacancy_swap"} and selection_type_code != "02":
        raise HTTPException(status_code=422, detail="自动抢课和空位追踪只适用于抢选轮次")
    return get_course_selection_automation_service().create(str(auth.username), request.model_dump())


@router.get("/jwxk/automation/tasks")
def list_jwxk_automation_tasks(
    batch_code: str = Query(default="", max_length=64, pattern=r"^[A-Za-z0-9_-]*$"),
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
):
    return {
        "tasks": get_course_selection_automation_service().list(
            str(auth.username), batch_code=batch_code,
        )
    }


@router.post("/jwxk/automation/tasks/{action}")
def action_jwxk_automation_task(
    action: str,
    request: JwxkAutomationTaskAction,
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
):
    if action not in {"start", "pause", "cancel"}:
        raise HTTPException(status_code=404, detail="未知任务操作")
    try:
        return get_course_selection_automation_service().action(str(auth.username), request.task_id, action)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="自动抢课任务不存在") from error


@router.post("/jwxk/weights/budget")
def get_jwxk_weight_budget(
    request: JwxkBatchRequest,
    response: Response,
    auth: NEUAuthClient = Depends(require_serialized_auth),
    storage: Storage = Depends(get_storage),
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return _jwxk_mutation_client(auth, storage).get_weight_budget(
            batch_code=request.batch_code,
        )
    except JwxkError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except NEULoginError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


def _weight_targets(items: list[dict]) -> list[dict]:
    """Collapse teaching-class alternatives into one model target per course."""

    grouped: dict[str, list[dict]] = {}
    for raw in items:
        item = dict(raw)
        course_key = str(item.get("course_code") or item.get("group_id") or item.get("class_id") or "").strip().upper()
        if not course_key:
            continue
        grouped.setdefault(course_key, []).append(item)
    targets = []
    for course_key, alternatives in grouped.items():
        alternatives.sort(key=lambda item: (int(item.get("priority") or 999), str(item.get("class_id") or "")))
        representative = alternatives[0]
        participants = max(
            int(item.get("weight_participant_count") or item.get("first_choice_count") or item.get("selected_count") or 0)
            for item in alternatives
        )
        capacity = max(
            1,
            max(int(item.get("capacity") or 0) for item in alternatives),
        )
        targets.append({
            **representative,
            "course_key": course_key,
            "model_id": str(representative.get("class_id") or course_key),
            "participants": participants,
            "capacity": capacity,
            "alternatives": alternatives,
            "group_ids": sorted({
                str(item.get("plan_group_id") or "") for item in alternatives
                if str(item.get("plan_group_id") or "")
            }),
            "utility": max(float(item.get("utility") or 5) for item in alternatives),
            "already_selected": any(bool(item.get("course_already_selected") or item.get("already_selected")) for item in alternatives),
            "time_unknown": any(
                not item.get("schedules")
                or any(meeting.get("recurrence_unknown") for meeting in item.get("schedules") or [])
                for item in alternatives
            ),
        })
    return targets


def _weight_grade_sizes(storage: Storage) -> tuple[dict, dict]:
    config = _plan_config(storage)
    values = dict(config.get("course_selection_weight_grade_sizes") or {})
    return config, values


@router.get("/jwxk/weights/config", response_model=JwxkWeightConfigResponse)
def get_jwxk_weight_config(
    term_code: str = Query(min_length=1, max_length=32),
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
    storage: Storage = Depends(get_storage),
):
    _, values = _weight_grade_sizes(storage)
    value = values.get(f"{auth.username}:{term_code}")
    return JwxkWeightConfigResponse(term_code=term_code, grade_size=int(value) if value else None)


def _weight_market_archive(account: str, batch_code: str) -> dict:
    archive = next((item for item in (
        get_course_selection_automation_service().list_catalog_archives(account)
    ) if item.get("batch_code") == batch_code), None)
    if not archive or not archive.get("courses"):
        raise HTTPException(status_code=409, detail="轮次课程数据仍在后台同步，请稍后再生成策略")
    if not (archive.get("catalog_complete") or archive.get("sync_status") == "complete"):
        raise HTTPException(status_code=409, detail="完整轮次课程数据尚未同步完成，请稍后再生成策略")
    return archive


def _weight_conflicts(targets: list[dict], term_code: str) -> list[tuple[str, str]]:
    normalized: dict[str, list] = {}
    for target in targets:
        meetings = []
        for raw in target.get("schedules") or []:
            meetings.append(normalize_meeting({
                **raw,
                "source_id": target["model_id"],
                "teaching_class_id": target["model_id"],
                "course_code": target.get("course_code") or target["course_key"],
                "course_name": target.get("course_name") or target["course_key"],
            }, term_code=term_code, default_source="jwxk_weight_plan"))
        normalized[target["model_id"]] = meetings
    result = []
    for index, left in enumerate(targets):
        for right in targets[index + 1:]:
            left_rows = normalized[left["model_id"]]
            right_rows = normalized[right["model_id"]]
            if not left_rows or not right_rows:
                continue
            checked = check_conflicts(left_rows, right_rows, ignore_same_course=True)
            if any(item.status.value == "conflict" for item in checked):
                result.append((left["model_id"], right["model_id"]))
    return result


@router.post("/jwxk/weights/plan")
def plan_jwxk_weights(
    request: JwxkWeightPlanRequest,
    response: Response,
    auth: NEUAuthClient = Depends(require_serialized_auth),
    storage: Storage = Depends(get_storage),
):
    response.headers["Cache-Control"] = "no-store"
    targets = _weight_targets([dict(item) for item in request.items])
    if not targets:
        raise HTTPException(status_code=422, detail="请先向方案中加入课程")
    client = _jwxk_mutation_client(auth, storage)
    try:
        budget_info = client.get_weight_budget(batch_code=request.batch_code)
    except JwxkError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except NEULoginError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    budget = int(budget_info["remaining"])
    minimum = int(budget_info["minimum"])
    step = int(budget_info["step"])
    if budget < minimum:
        raise HTTPException(status_code=422, detail="当前剩余权重不足以投放任何课程")

    config, grade_sizes = _weight_grade_sizes(storage)
    grade_sizes[f"{auth.username}:{request.term_code}"] = request.grade_size
    config["course_selection_weight_grade_sizes"] = grade_sizes
    storage.save_config(config)

    archive = _weight_market_archive(str(auth.username), request.batch_code)
    archive_rows = [dict(item) for item in archive.get("courses") or []]
    archive_by_class = {
        str(item.get("class_id") or "").strip(): item
        for item in archive_rows
        if str(item.get("class_id") or "").strip()
    }
    # The saved plan may contain an older participant snapshot.  Before model
    # calculation, overlay the latest archived market values so the advice and
    # the numbers shown beside it describe the same observation.
    for target in targets:
        snapshots = [
            archive_by_class.get(str(item.get("class_id") or "").strip())
            for item in target.get("alternatives") or []
        ]
        snapshots = [item for item in snapshots if item]
        if not snapshots:
            continue
        def _snapshot_participants(item):
            value = item.get("weight_participant_count")
            if value is None:
                value = item.get("market_participant_count")
            return max(0, int(value or 0))
        target["participants"] = max(_snapshot_participants(item) for item in snapshots)
        target["capacity"] = max(1, max(int(item.get("capacity") or 0) for item in snapshots))
        target["current_participant_count"] = target["participants"]
        target["current_participant_label"] = "已投注人数"
        target["current_capacity"] = target["capacity"]
        target["capacity_updated_at"] = max(
            (str(item.get("capacity_updated_at") or "") for item in snapshots),
            default="",
        )
    market_courses = []
    for item in archive_rows:
        class_id = str(item.get("class_id") or "").strip()
        capacity_value = int(item.get("capacity") or 0)
        if not class_id or capacity_value <= 0:
            continue
        market_courses.append(WeightMarketCourse(
            course_id=class_id,
            capacity=capacity_value,
            bidders=max(0, int(item.get("weight_participant_count") or item.get("market_participant_count") or 0)),
        ))
    group_models = [WeightGroupTarget(group.group_id, group.name, group.target_count) for group in request.groups]
    candidate_models = [WeightCandidate(
        course_id=item["model_id"],
        name=str(item.get("course_name") or item["course_key"]),
        capacity=int(item["capacity"]),
        bidders=int(item["participants"]),
        utility=float(item["utility"]),
        group_ids=tuple(item["group_ids"]),
        already_selected=bool(item["already_selected"]),
        time_unknown=bool(item["time_unknown"]),
    ) for item in targets]
    try:
        if not _solver_slots.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="模型计算繁忙，请稍后重试")
        try:
            optimized = optimize_grouped_weights(
                policy=WeightPolicy(budget=budget, min_bid=minimum, bid_step=step),
                grade_size=request.grade_size,
                market_courses=market_courses,
                candidates=candidate_models,
                groups=group_models,
                conflicts=_weight_conflicts(targets, request.term_code),
            )
        finally:
            _solver_slots.release()
    except WeightOptimizationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    by_model_id = {item["model_id"]: item for item in targets}
    course_results = []
    allocations = []
    for course in optimized["courses"]:
        target = by_model_id[course["course_id"]]
        result = {
            **{key: value for key, value in target.items() if key not in {
                "alternatives", "participants", "course_key", "model_id", "group_ids",
            }},
            **course,
            "weight": int(course["bid"]),
            "current_participant_count": int(target.get("current_participant_count", target.get("participants", 0))),
            "current_participant_label": target.get("current_participant_label", "已投注人数"),
            "current_capacity": int(target.get("current_capacity", target.get("capacity", 0))),
            "capacity_updated_at": target.get("capacity_updated_at", ""),
        }
        course_results.append(result)
        if course["bid"] > 0 and not course["already_selected"]:
            allocations.append(result)
    return {
        "model_version": optimized["model_version"],
        "budget": budget,
        "official_total": budget_info["total"],
        "official_used": budget_info["used"],
        "minimum": minimum,
        "step": step,
        "used": optimized["budget_used"],
        "items": allocations,
        "courses": course_results,
        "groups": optimized["groups"],
        "approximate": optimized["approximate"],
        "diagnostics": optimized["diagnostics"],
        "warnings": optimized.get("warnings") or [],
    }


@router.post("/jwxk/weights/apply")
def apply_jwxk_weights(
    request: JwxkSavedPlanRequest,
    auth: NEUAuthClient = Depends(require_serialized_auth),
    storage: Storage = Depends(get_storage),
):
    mutation_policy("jwxk.select")
    results = []
    client = _jwxk_mutation_client(auth, storage)
    try:
        budget_info = client.get_weight_budget(batch_code=request.batch_code)
    except JwxkError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except NEULoginError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    course_codes = [str(item.get("course_code") or "").strip().upper() for item in request.items]
    if not all(course_codes) or len(course_codes) != len(set(course_codes)):
        raise HTTPException(status_code=422, detail="同一课程只能投放一次权重")
    minimum = int(budget_info["minimum"])
    step = int(budget_info["step"])
    weights = [int(item.get("weight") or 0) for item in request.items]
    if any(weight < minimum or weight % step for weight in weights):
        raise HTTPException(status_code=422, detail=f"每门课程权重至少为 {minimum}，且必须按 {step} 递增")
    if sum(weights) > int(budget_info["remaining"]):
        raise HTTPException(status_code=422, detail=f"投放总额超过官方剩余权重 {budget_info['remaining']}")
    for item in request.items:
        try:
            result = client.select_course(
                batch_code=request.batch_code,
                teaching_class_type=str(item.get("teaching_class_type") or "ALLKC"),
                class_id=str(item.get("class_id") or ""),
                course_code=str(item.get("course_code") or ""),
                weight=int(item.get("weight") or 0),
                confirm_risk=True,
            )
            results.append({"class_id": item.get("class_id"), **result})
            if not result.get("success"):
                break
        except (JwxkError, NEULoginError, requests.RequestException) as error:
            results.append({"class_id": item.get("class_id"), "success": False, "queued": False, "requires_confirmation": False, "code": "stopped", "message": str(error)})
            break
    if results and all(item.get("success") for item in results):
        _invalidate_jwxk_timetable(auth, request.term_code, "jwxk.select")
    return {"results": results, "completed": len(results) == len(request.items) and all(item.get("success") for item in results)}


def _jwxk_mutation_client(auth: NEUAuthClient, storage: Storage) -> JwxkSessionClient:
    preference = _read_jwxk_preference(storage)
    effective = resolve_network_mode(
        preference, str(getattr(auth, "active_mode", "direct") or "direct")
    )
    # Recover CAS/JWXK before any mutation-specific lookup starts.  The final
    # write still uses retry_on_auth=False and is never replayed after sending.
    auth.ensure_service_session("jwxk", network_mode_override=effective)
    return JwxkSessionClient(auth, network_mode=effective)


def _invalidate_jwxk_timetable(auth: NEUAuthClient, term_code: str, operation: str) -> None:
    policy = mutation_policy(operation)
    if not term_code or "personal-timetable" not in policy.invalidations:
        return
    coordinator = get_cache_coordinator()
    if "personal-timetable" not in coordinator.registry.resources():
        return
    coordinator.invalidate(
        account_id=str(auth.username),
        resource="personal-timetable",
        variant=personal_timetable_variant(term_code),
    )


@router.post("/jwxk/batches/confirm", response_model=JwxkMutationResponse)
def confirm_jwxk_batch(
    request: JwxkBatchConfirmRequest,
    response: Response,
    auth: NEUAuthClient = Depends(require_serialized_auth),
    storage: Storage = Depends(get_storage),
) -> JwxkMutationResponse:
    response.headers["Cache-Control"] = "no-store"
    mutation_policy("jwxk.confirm")
    try:
        result = _jwxk_mutation_client(auth, storage).confirm_batch(
            batch_code=request.batch_code
        )
        return JwxkMutationResponse.model_validate(result)
    except JwxkError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except NEULoginError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.post("/jwxk/courses/select", response_model=JwxkMutationResponse)
def select_jwxk_course(
    request: JwxkCourseSelectRequest,
    response: Response,
    auth: NEUAuthClient = Depends(require_serialized_auth),
    storage: Storage = Depends(get_storage),
) -> JwxkMutationResponse:
    response.headers["Cache-Control"] = "no-store"
    mutation_policy("jwxk.select")
    try:
        result = _jwxk_mutation_client(auth, storage).select_course(
            batch_code=request.batch_code,
            teaching_class_type=request.teaching_class_type,
            class_id=request.class_id,
            course_code=request.course_code,
            weight=request.weight,
            confirm_risk=request.confirm_risk,
            skip_preflight_checks=request.preflight_verified,
        )
        term_code = str(result.pop("_term_code", ""))
        if result.get("success"):
            _invalidate_jwxk_timetable(auth, term_code, "jwxk.select")
        return JwxkMutationResponse.model_validate(result)
    except JwxkError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except NEULoginError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.post("/jwxk/courses/deselect", response_model=JwxkMutationResponse)
def deselect_jwxk_course(
    request: JwxkCourseDeselectRequest,
    response: Response,
    auth: NEUAuthClient = Depends(require_serialized_auth),
    storage: Storage = Depends(get_storage),
) -> JwxkMutationResponse:
    response.headers["Cache-Control"] = "no-store"
    mutation_policy("jwxk.deselect")
    try:
        result = _jwxk_mutation_client(auth, storage).deselect_course(
            batch_code=request.batch_code,
            class_id=request.class_id,
            selection_source=request.selection_source,
            confirm_risk=request.confirm_risk,
        )
        term_code = str(result.pop("_term_code", ""))
        if result.get("success"):
            _invalidate_jwxk_timetable(auth, term_code, "jwxk.deselect")
        return JwxkMutationResponse.model_validate(result)
    except JwxkError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except NEULoginError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.post("/optimize", response_model=CourseSelectionOptimizeResponse)
def optimize_course_selection(
    request: CourseSelectionOptimizeRequest,
    response: Response,
) -> CourseSelectionOptimizeResponse:
    """Calculate model strategies without reading or persisting user data."""

    started_at = time.perf_counter()
    response.headers["Cache-Control"] = "no-store"
    try:
        policy = SelectionPolicy(
            budget=request.policy.budget,
            min_bid=request.policy.min_bid,
            bid_step=request.policy.bid_step,
            tie_rule=TieRule(request.policy.tie_rule),
            max_selected_courses=request.policy.max_selected_courses,
            demand_multipliers=request.policy.demand_multipliers,
        )
        market = MarketSnapshot(
            cohort_size=request.market.cohort_size,
            captured_at=request.market.captured_at,
            is_complete=request.market.is_complete,
            courses=tuple(
                CourseMarket(
                    course_id=course.course_id,
                    name=course.name,
                    capacity=course.capacity,
                    current_participants=course.current_participants,
                    target_included=course.target_included,
                    target_interested=course.target_interested,
                    target_utility=course.target_utility,
                )
                for course in request.market.courses
            ),
        )
        if not _solver_slots.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="模型计算繁忙，请稍后重试")
        try:
            result = optimize_course_weights(policy, market)
        finally:
            _solver_slots.release()
    except HTTPException:
        raise
    except CourseSelectionError as exc:
        logger.info(
            "course-selection model rejected input error=%s courses=%s",
            type(exc).__name__,
            len(request.market.courses),
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        error_id = log_application_error("course_selection.optimize", exc, 500)
        raise HTTPException(status_code=500, detail=f"模型计算失败（错误编号：{error_id}）") from exc

    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    logger.info(
        "course-selection model=%s courses=%s status=%s elapsed_ms=%.1f",
        result["model_version"],
        len(request.market.courses),
        result["solution_status"],
        elapsed_ms,
    )
    return CourseSelectionOptimizeResponse.model_validate(result)
