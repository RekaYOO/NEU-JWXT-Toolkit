from __future__ import annotations

import re
import tempfile
import zipfile
from calendar import monthrange
from datetime import datetime
from urllib.parse import parse_qsl, quote, unquote, urlparse

from fastapi import APIRouter, Depends, HTTPException, Response
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from backend.app.cache_support import read_cache
from backend.app.dependencies import (
    _cache_coordinator,
    get_auth_generation,
    require_cached_auth_identity,
    require_serialized_auth,
)
from backend.app.schemas.festival_activities import CertificateArchiveRequest, FestivalActivitiesResponse
from backend.core.auth import NEUAuthClient
from backend.core.auth.client import NEULoginError
from backend.core.festival_activities import fetch_festival_activities
from backend.app.presenters import festival_cache_response, festival_remote_response


router = APIRouter()
# One-cycle compatibility for callers of the former router-local presenter.
_remote_response = festival_remote_response
MAX_CERTIFICATES = 200
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_IMAGE_BYTES = 100 * 1024 * 1024
CERTIFICATE_PREFIXES = (
    "/static/uploads/res/certificate/",
    "/static/uploads/res/originalitycert/",
    "/static/uploads/res/popsciencecert/",
    "/static/uploads/res/technicalcert/",
    "/static/uploads/res/businesscert/",
)


def _username(auth: NEUAuthClient) -> str:
    value = str(getattr(auth, "username", "") or "")
    if not value:
        raise HTTPException(status_code=401, detail="无法确认当前登录账号")
    return value


def _authentication_failure(error: NEULoginError) -> HTTPException:
    return HTTPException(status_code=401, detail="统一认证会话已过期，请重新登录")


def _retry_authenticated_read(operation):
    """Replay one idempotent cxcy read after the client attempted auth recovery."""
    try:
        return operation()
    except NEULoginError:
        return operation()


def _cache_response(username: str, entry, stale: bool, source: str = "cache") -> dict:
    """Compatibility wrapper; shared mapping lives in app.presenters."""
    return festival_cache_response(username, entry, stale, source)


@router.get("/export/festival-activities/cache", response_model=FestivalActivitiesResponse)
def get_festival_activities_cache(
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
):
    username = _username(auth)
    entry, stale = read_cache(username, "festival-activities")
    return _cache_response(username, entry, stale)


@router.delete("/export/festival-activities/cache")
def delete_festival_activities_cache(
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
):
    username = _username(auth)
    try:
        deleted, cancelled = _cache_coordinator.delete_resource(
            account_id=username,
            resource="festival-activities",
            identity_epoch=get_auth_generation(),
        )
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail="登录账号已变化，请重试") from error
    return {
        "success": True,
        "deleted": deleted,
        "cancelled_jobs": cancelled,
    }


@router.get("/export/festival-activities", response_model=FestivalActivitiesResponse)
def get_festival_activities(
    response: Response,
    auth: NEUAuthClient = Depends(require_serialized_auth),
):
    response.headers["Cache-Control"] = "no-store"
    username = _username(auth)
    try:
        payload = _retry_authenticated_read(lambda: fetch_festival_activities(auth))
    except NEULoginError as error:
        raise _authentication_failure(error) from error
    return festival_remote_response(username, payload)


def _safe_certificate_path(value: str) -> str:
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("证书地址端口非法") from error
    if (
        parsed.scheme not in {"", "https"}
        or (parsed.hostname and parsed.hostname != "cxcy.neu.edu.cn")
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise ValueError("证书地址域名不受信任")
    path = parsed.path
    decoded_path = unquote(path)
    if unquote(decoded_path) != decoded_path:
        raise ValueError("证书地址包含重复编码")
    if "\\" in decoded_path or any(
        part in {".", ".."} for part in decoded_path.split("/")
    ) or any(ord(char) < 32 for char in decoded_path):
        raise ValueError("证书地址非法")
    legacy_path = any(
        decoded_path.lower().startswith(prefix)
        for prefix in CERTIFICATE_PREFIXES[1:]
    )
    generic_path = bool(re.fullmatch(
        r"/static/uploads/res/certificate/[A-Za-z0-9_-]{1,128}/[^/]+\.(?:png|jpe?g|webp)",
        decoded_path,
        flags=re.IGNORECASE,
    ))
    if (
        not (legacy_path or generic_path)
        or not re.search(r"\.(?:png|jpe?g|webp)$", decoded_path, re.IGNORECASE)
    ):
        raise ValueError("证书地址不在允许目录")
    if any(
        key.lower() not in {"t", "ts", "timestamp", "_"}
        or not re.fullmatch(r"\d{1,20}", value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    ):
        raise ValueError("证书地址查询参数不受支持")
    return path + (("?" + parsed.query) if parsed.query else "")


def _image_type(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif", "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    raise ValueError("响应不是支持的图片")


def _filename(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value).strip(" ._")
    return (cleaned[:120] or "活动证书")


def _archive_scope_label(start_date, end_date) -> str:
    if (
        start_date.month == 8
        and start_date.day == 31
        and end_date.year == start_date.year + 1
        and end_date.month == 8
        and end_date.day == 30
    ):
        return f"{start_date.year}-{end_date.year}学年"
    if (
        start_date.month == 3
        and start_date.day == 1
        and end_date.year == start_date.year
        and end_date.month == 8
        and end_date.day == 31
    ):
        return f"{start_date.year - 1}-{start_date.year}春季学期"
    if (
        start_date.month == 9
        and start_date.day == 1
        and end_date.year == start_date.year + 1
        and end_date.month == 2
        and end_date.day == monthrange(end_date.year, 2)[1]
    ):
        return f"{start_date.year}-{end_date.year}秋季学期"
    return f"{start_date.isoformat()}_{end_date.isoformat()}"


@router.post("/export/festival-activities/certificates/archive")
def download_certificate_archive(
    request: CertificateArchiveRequest,
    auth: NEUAuthClient = Depends(require_serialized_auth),
):
    _username(auth)
    try:
        payload = _retry_authenticated_read(lambda: fetch_festival_activities(auth))
    except NEULoginError as error:
        raise _authentication_failure(error) from error
    candidates = []
    for item in payload.get("activities") or []:
        try:
            start = datetime.fromisoformat(str(item.get("start_time") or "")).date()
        except ValueError:
            continue
        if request.start_date <= start <= request.end_date and item.get("certificate_available") and item.get("certificate_url"):
            candidates.append((start, item))
    if not candidates:
        raise HTTPException(status_code=404, detail="所选范围内没有可下载的证书")
    if len(candidates) > MAX_CERTIFICATES:
        raise HTTPException(status_code=413, detail="证书数量超过安全限制")

    spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    failures: list[str] = []
    successes = 0
    total_bytes = 0
    names: set[str] = set()
    authentication_error: NEULoginError | None = None
    with zipfile.ZipFile(spool, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for activity_date, item in candidates:
            try:
                path = _safe_certificate_path(str(item.get("certificate_url") or ""))
                response = _retry_authenticated_read(
                    lambda: auth.request_service("cxcy", "GET", path, stream=True)
                )
                try:
                    response.raise_for_status()
                    # The shared service client limits redirect hosts, but a
                    # certificate response must also remain inside a certificate
                    # directory after the final same-origin redirect.
                    _safe_certificate_path(str(getattr(response, "url", "") or ""))
                    declared = response.headers.get("Content-Length")
                    if declared and int(declared) > MAX_IMAGE_BYTES:
                        raise ValueError("图片超过大小限制")
                    body = bytearray()
                    for chunk in response.iter_content(64 * 1024):
                        body.extend(chunk)
                        if len(body) > MAX_IMAGE_BYTES:
                            raise ValueError("图片超过大小限制")
                finally:
                    response.close()
                extension, expected_content_type = _image_type(bytes(body))
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if not content_type.startswith("image/") or content_type != expected_content_type:
                    raise ValueError("图片类型校验失败")
                total_bytes += len(body)
                if total_bytes > MAX_ARCHIVE_IMAGE_BYTES:
                    raise ValueError("证书包超过总大小限制")
                stem = _filename(f"{activity_date.isoformat()}_{item.get('section', '')}_{item.get('name', '')}")
                name = f"{stem}.{extension}"
                suffix = 2
                while name in names:
                    name = f"{stem}_{suffix}.{extension}"
                    suffix += 1
                names.add(name)
                archive.writestr(name, bytes(body))
                successes += 1
            except NEULoginError as exc:
                authentication_error = exc
                break
            except Exception as exc:
                failures.append(f"{item.get('name') or '未命名活动'}：{type(exc).__name__}")
        if failures:
            archive.writestr("下载说明.txt", "以下证书下载失败：\n" + "\n".join(failures))
    if authentication_error is not None:
        spool.close()
        raise _authentication_failure(authentication_error) from authentication_error
    if successes == 0:
        spool.close()
        raise HTTPException(status_code=502, detail="证书下载全部失败")
    spool.seek(0)
    archive_name = (
        f"四节活动证书_{_archive_scope_label(request.start_date, request.end_date)}.zip"
    )
    return StreamingResponse(
        spool, media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=festival-certificates.zip; filename*=UTF-8''{quote(archive_name)}",
            "X-Certificate-Succeeded": str(successes),
            "X-Certificate-Failed": str(len(failures)),
        },
        background=BackgroundTask(spool.close),
    )
