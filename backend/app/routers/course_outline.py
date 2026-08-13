"""Read-only course-outline routes and minimal metadata synchronization."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response

from backend.app.dependencies import (
    get_course_outline_sync_service,
    get_cache_coordinator,
    require_cached_auth_identity,
    require_serialized_auth,
)
from backend.app.schemas.course_outline import (
    CourseOutlineAttachmentRequest,
    CourseOutlineDetailRequest,
    CourseOutlineMetadataReadRequest,
    CourseOutlineMetadataSyncRequest,
    CourseOutlineSearchRequest,
    CourseOutlineSectionsRequest,
)
from backend.core.auth import NEUAuthClient
from backend.core.cache import CacheKey
from backend.core.course_outline import CourseOutlineAPI
from backend.core.course_outline.service import course_variant
from backend.core.log import log_application_error


router = APIRouter(prefix="/course-outlines", tags=["course-outlines"])
NO_STORE = {"Cache-Control": "no-store"}


def _failure(operation: str, error: Exception) -> HTTPException:
    error_id = log_application_error(f"course_outline.{operation}", error, 502)
    return HTTPException(status_code=502, detail=f"课程大纲服务暂时不可用（错误编号：{error_id}）")


@router.get("/search-schema")
def search_schema(response: Response, auth: NEUAuthClient = Depends(require_serialized_auth)):
    response.headers.update(NO_STORE)
    try:
        return CourseOutlineAPI(auth).search_schema()
    except Exception as exc:
        raise _failure("schema", exc) from exc


@router.post("/search")
def search(request: CourseOutlineSearchRequest, response: Response,
           auth: NEUAuthClient = Depends(require_serialized_auth)):
    response.headers.update(NO_STORE)
    try:
        return CourseOutlineAPI(auth).search(**request.model_dump())
    except Exception as exc:
        raise _failure("search", exc) from exc


@router.post("/detail/overview")
def detail_overview(request: CourseOutlineDetailRequest, response: Response,
                    auth: NEUAuthClient = Depends(require_serialized_auth)):
    response.headers.update(NO_STORE)
    try:
        return CourseOutlineAPI(auth).overview(request.course_code)
    except Exception as exc:
        raise _failure("overview", exc) from exc


@router.post("/detail/sections")
def detail_sections(request: CourseOutlineSectionsRequest, response: Response,
                    auth: NEUAuthClient = Depends(require_serialized_auth)):
    response.headers.update(NO_STORE)
    try:
        return CourseOutlineAPI(auth).sections(request.course_code, request.group)
    except Exception as exc:
        raise _failure("sections", exc) from exc


@router.post("/attachments/download")
def download_attachment(request: CourseOutlineAttachmentRequest,
                        auth: NEUAuthClient = Depends(require_serialized_auth)):
    try:
        remote = auth.get(
            "https://jwxt.neu.edu.cn/jwapp/sys/emapcomponent/file/"
            f"getUploadedAttachment/{request.token}.do",
            stream=True,
        )
        remote.raise_for_status()
        content_type = remote.headers.get("Content-Type", "application/octet-stream")
        safe_name = request.filename.replace("\r", "").replace("\n", "")
        return Response(
            content=remote.content,
            media_type=content_type,
            headers={
                **NO_STORE,
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(safe_name)}",
            },
        )
    except Exception as exc:
        raise _failure("attachment", exc) from exc


@router.get("/metadata/plan")
def plan_metadata(auth: NEUAuthClient = Depends(require_cached_auth_identity)):
    coordinator = get_cache_coordinator()
    report, _ = coordinator.read(account_id=auth.username, resource="academic-report")
    codes: set[str] = set()

    def collect(value):
        if isinstance(value, dict):
            code = str(value.get("course_code") or "").strip()
            if code:
                codes.add(code)
            for nested in value.values():
                collect(nested)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)

    if report:
        collect(report.payload)
    items = []
    for code in sorted(codes):
        entry = coordinator.store.get(CacheKey(auth.username, "course-outline-metadata", course_variant(code)))
        if entry:
            items.append(entry.payload)
    return {"items": items}


@router.post("/metadata/read")
def read_metadata(
    request: CourseOutlineMetadataReadRequest,
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
):
    """Read only the requested account-local outline metadata variants.

    Unlike the plan endpoint, this also supports courses that appear in the
    score history but are absent from the current training plan.
    """
    coordinator = get_cache_coordinator()
    items = []
    for code in request.course_codes:
        entry = coordinator.store.get(
            CacheKey(auth.username, "course-outline-metadata", course_variant(code))
        )
        if entry:
            items.append(entry.payload)
    return {"items": items}


@router.post("/metadata/sync")
def sync_metadata(request: CourseOutlineMetadataSyncRequest,
                  auth: NEUAuthClient = Depends(require_cached_auth_identity)):
    return get_course_outline_sync_service().start(auth.username, request.courses, force=request.force)


@router.get("/metadata/sync/status")
def sync_status(auth: NEUAuthClient = Depends(require_cached_auth_identity)):
    return get_course_outline_sync_service().status(auth.username)


@router.post("/metadata/sync/cancel")
def sync_cancel(auth: NEUAuthClient = Depends(require_cached_auth_identity)):
    return get_course_outline_sync_service().cancel(auth.username)
