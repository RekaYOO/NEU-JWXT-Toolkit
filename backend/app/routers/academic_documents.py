"""Official transcript and enrolment-certificate export routes."""

from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response

from backend.app.dependencies import require_serialized_auth
from backend.app.schemas.academic_documents import AcademicDocumentGenerateRequest
from backend.core.academic_documents import AcademicDocumentAPI, AcademicDocumentError
from backend.core.auth import NEUAuthClient
from backend.core.log import log_application_error


router = APIRouter(prefix="/export/academic-documents", tags=["academic-documents"])
NO_STORE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"}
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024


def _safe_filename(name: str, extension: str) -> str:
    stem = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name).strip(" ._") or "教务证明"
    return f"{stem}.{extension}"


def _failure(operation: str, error: Exception) -> HTTPException:
    if isinstance(error, AcademicDocumentError):
        return HTTPException(status_code=409, detail=str(error))
    error_id = log_application_error(f"academic_documents.{operation}", error, 502)
    return HTTPException(status_code=502, detail=f"证明打印服务暂时不可用（错误编号：{error_id}）")


@router.get("")
def list_academic_documents(
    response: Response,
    auth: NEUAuthClient = Depends(require_serialized_auth),
):
    response.headers.update(NO_STORE)
    try:
        documents = AcademicDocumentAPI(auth).list_documents()
        groups: list[dict] = []
        for document in documents:
            group = next((item for item in groups if item["name"] == document.category), None)
            if group is None:
                group = {"name": document.category, "documents": []}
                groups.append(group)
            group["documents"].append(document.as_dict())
        return {"groups": groups, "documents": [item.as_dict() for item in documents]}
    except Exception as exc:
        raise _failure("list", exc) from exc


@router.post("/generate")
def generate_academic_document(
    request: AcademicDocumentGenerateRequest,
    auth: NEUAuthClient = Depends(require_serialized_auth),
):
    try:
        document, remote = AcademicDocumentAPI(auth).generate(request.document_id)
        content = remote.content
        content_type = str(remote.headers.get("Content-Type") or "application/octet-stream")
        remote.close()
        if len(content) > MAX_DOCUMENT_BYTES:
            raise AcademicDocumentError("证明文件过大，已停止传输")
        normalized = content_type.lower()
        if "application/json" in normalized:
            raise AcademicDocumentError("证明生成失败，请稍后重试")
        extension = "pdf" if "pdf" in normalized or content.startswith(b"%PDF-") else "html"
        if extension == "html":
            # The official controller returns a report page on some deployments.
            # Give relative FineReport assets their original origin when the page
            # is opened from an object URL in the desktop frontend.
            encoding = getattr(remote, "encoding", None) or "utf-8"
            html = content.decode(encoding, errors="replace")
            base = '<base href="https://jwxt.neu.edu.cn/jwapp/sys/zmdyneu/">'
            if "<head" in html.lower():
                head_end = html.lower().find(">", html.lower().find("<head"))
                html = html[:head_end + 1] + base + html[head_end + 1:]
            else:
                html = base + html
            content = html.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        filename = _safe_filename(document.name, extension)
        return Response(
            content=content,
            media_type=content_type,
            headers={
                **NO_STORE,
                "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
                "X-Academic-Document-Name": quote(document.name),
                "X-Academic-Document-Format": extension,
            },
        )
    except Exception as exc:
        raise _failure("generate", exc) from exc
