"""Read-only discovery and user-triggered generation of official documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class AcademicDocumentError(RuntimeError):
    """Raised when the official proof-printing service cannot fulfil a request."""


@dataclass(frozen=True)
class AcademicDocument:
    document_id: str
    name: str
    category: str
    semester_limit: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.document_id,
            "name": self.name,
            "category": self.category,
            "semester_limit": self.semester_limit,
        }


class AcademicDocumentAPI:
    """Adapter for ``学籍 -> 证明打印`` in the official JWXT application."""

    APP_ORIGIN = "https://jwxt.neu.edu.cn"
    APP_BASE = f"{APP_ORIGIN}/jwapp/sys/zmdyneu"
    INDEX_URL = f"{APP_BASE}/*default/index.do?THEME=golden&forceApp=zmdyneu#/zmdy"
    LIST_URL = f"{APP_BASE}/zmdy/queryAllZmdy.do"
    PREFLIGHT_URL = f"{APP_BASE}/zmdy/printBefore.do"
    RENDER_URL = f"{APP_BASE}/zmdy/printZm.do"
    HEADERS = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": INDEX_URL,
        "X-Requested-With": "XMLHttpRequest",
    }

    def __init__(self, auth_client):
        self.auth = auth_client

    def _initialize(self) -> None:
        response = self.auth.get(self.INDEX_URL)
        response.raise_for_status()

    def _post_json(self, url: str, data: dict[str, Any]) -> dict[str, Any]:
        response = self.auth.post(url, data=data, headers=self.HEADERS)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise AcademicDocumentError("证明打印服务返回了无法解析的数据") from exc
        if not isinstance(payload, dict):
            raise AcademicDocumentError("证明打印服务响应格式异常")
        return payload

    @staticmethod
    def _limit(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def list_documents(self) -> list[AcademicDocument]:
        self._initialize()
        payload = self._post_json(self.LIST_URL, {})
        if payload.get("success") is not True:
            raise AcademicDocumentError(str(payload.get("msg") or "无法读取可打印证明"))

        documents: list[AcademicDocument] = []
        for group in payload.get("datas") or []:
            if not isinstance(group, dict):
                continue
            category = str(group.get("CDMC") or "其他证明").strip() or "其他证明"
            for item in group.get("REPORTS") or []:
                if not isinstance(item, dict):
                    continue
                document_id = str(item.get("WID") or "").strip()
                name = str(item.get("CDMC") or "").strip()
                if not document_id or not name:
                    continue
                documents.append(AcademicDocument(
                    document_id=document_id,
                    name=name,
                    category=category,
                    semester_limit=self._limit(item.get("XNXQWD_DBDX")),
                ))
        return documents

    def resolve_document(self, document_id: str) -> AcademicDocument:
        document = next(
            (item for item in self.list_documents() if item.document_id == document_id),
            None,
        )
        if document is None:
            raise AcademicDocumentError("该证明当前不可用，请刷新列表后重试")
        return document

    def generate(self, document_id: str):
        """Generate exactly once after a separate, non-consuming permission check.

        The render call is deliberately not retried here: the official service may
        count a successful render against the per-semester quota.
        """
        document = self.resolve_document(document_id)
        preflight = self._post_json(self.PREFLIGHT_URL, {"wid": document.document_id})
        if preflight.get("success") is not True:
            raise AcademicDocumentError(str(preflight.get("msg") or "证明打印预检失败"))
        if str(preflight.get("fwlx") or "1") == "2":
            raise AcademicDocumentError("该证明需要在教务系统提交申请，暂不支持直接生成")
        if preflight.get("hasQx") is not True:
            raise AcademicDocumentError(str(preflight.get("errMsg") or "当前无权打印该证明"))

        response = self.auth.get(
            self.RENDER_URL,
            params={"wid": document.document_id},
            headers={"Accept": "application/pdf,text/html,*/*", "Referer": self.INDEX_URL},
            stream=True,
        )
        response.raise_for_status()
        return document, response

