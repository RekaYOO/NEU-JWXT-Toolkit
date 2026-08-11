from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

from backend.app.routers import academic_documents as router_module
from backend.app.schemas.academic_documents import AcademicDocumentGenerateRequest
from backend.core.academic_documents import AcademicDocumentAPI, AcademicDocumentError


class FakeResponse:
    def __init__(self, payload=None, *, content=b"", content_type="application/json"):
        self._payload = payload
        self.content = content
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def raise_for_status(self):
        return None

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def close(self):
        self.closed = True


class FakeAuth:
    username = "20240000"

    def __init__(self, list_payload, *, preflight=None, rendered=None):
        self.list_payload = list_payload
        self.preflight = preflight or {"success": True, "hasQx": True, "fwlx": "1"}
        self.rendered = rendered or FakeResponse(content=b"%PDF-example", content_type="application/pdf")
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if url == AcademicDocumentAPI.RENDER_URL:
            return self.rendered
        return FakeResponse(content=b"<html></html>", content_type="text/html")

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url == AcademicDocumentAPI.LIST_URL:
            return FakeResponse(self.list_payload)
        if url == AcademicDocumentAPI.PREFLIGHT_URL:
            return FakeResponse(self.preflight)
        raise AssertionError(url)


LIST_PAYLOAD = {
    "success": True,
    "datas": [
        {
            "CDMC": "成绩单",
            "REPORTS": [
                {"WID": "transcript-zh", "CDMC": "中文成绩单打印", "XNXQWD_DBDX": 50},
                {"WID": "transcript-en", "CDMC": "英文成绩单打印", "XNXQWD_DBDX": "50"},
            ],
        },
        {
            "CDMC": "学籍证明",
            "REPORTS": [
                {"WID": "enrolment-zh", "CDMC": "中文学籍证明", "XNXQWD_DBDX": None},
            ],
        },
    ],
}


def test_list_documents_preserves_official_groups_and_limits():
    auth = FakeAuth(LIST_PAYLOAD)
    items = AcademicDocumentAPI(auth).list_documents()

    assert [(item.name, item.category, item.semester_limit) for item in items] == [
        ("中文成绩单打印", "成绩单", 50),
        ("英文成绩单打印", "成绩单", 50),
        ("中文学籍证明", "学籍证明", None),
    ]
    assert auth.calls[0][0] == "GET"
    assert auth.calls[1][1] == AcademicDocumentAPI.LIST_URL


def test_generate_resolves_against_fresh_list_and_preflights_before_render():
    auth = FakeAuth(LIST_PAYLOAD)
    document, rendered = AcademicDocumentAPI(auth).generate("transcript-zh")

    assert document.name == "中文成绩单打印"
    assert rendered.content.startswith(b"%PDF-")
    assert [call[1] for call in auth.calls][-2:] == [
        AcademicDocumentAPI.PREFLIGHT_URL,
        AcademicDocumentAPI.RENDER_URL,
    ]
    assert auth.calls[-1][2]["params"] == {"wid": "transcript-zh"}


def test_generate_rejects_unlisted_and_application_only_documents_without_rendering():
    auth = FakeAuth(LIST_PAYLOAD)
    with pytest.raises(AcademicDocumentError, match="当前不可用"):
        AcademicDocumentAPI(auth).generate("unknown")
    assert all(call[1] != AcademicDocumentAPI.RENDER_URL for call in auth.calls)

    auth = FakeAuth(LIST_PAYLOAD, preflight={"success": True, "hasQx": True, "fwlx": "2"})
    with pytest.raises(AcademicDocumentError, match="提交申请"):
        AcademicDocumentAPI(auth).generate("transcript-zh")
    assert all(call[1] != AcademicDocumentAPI.RENDER_URL for call in auth.calls)


def test_list_route_groups_documents_and_disables_caching():
    response = Response()
    payload = router_module.list_academic_documents(response, FakeAuth(LIST_PAYLOAD))

    assert [group["name"] for group in payload["groups"]] == ["成绩单", "学籍证明"]
    assert len(payload["documents"]) == 3
    assert response.headers["cache-control"].startswith("no-store")


def test_generate_route_returns_inline_pdf_and_closes_remote():
    remote = FakeResponse(content=b"%PDF-example", content_type="application/octet-stream")
    auth = FakeAuth(LIST_PAYLOAD, rendered=remote)
    response = router_module.generate_academic_document(
        AcademicDocumentGenerateRequest(document_id="transcript-zh"), auth,
    )

    assert response.media_type == "application/octet-stream"
    assert response.headers["x-academic-document-format"] == "pdf"
    assert "inline" in response.headers["content-disposition"]
    assert remote.closed is True


def test_generate_route_makes_official_html_assets_resolvable():
    remote = FakeResponse(
        content=b"<html><head><title>report</title></head><body></body></html>",
        content_type="text/html;charset=UTF-8",
    )
    response = router_module.generate_academic_document(
        AcademicDocumentGenerateRequest(document_id="transcript-zh"),
        FakeAuth(LIST_PAYLOAD, rendered=remote),
    )

    assert response.headers["x-academic-document-format"] == "html"
    assert b'<base href="https://jwxt.neu.edu.cn/jwapp/sys/zmdyneu/">' in response.body


def test_generate_route_rejects_unexpectedly_large_response(monkeypatch):
    monkeypatch.setattr(router_module, "MAX_DOCUMENT_BYTES", 3)
    remote = FakeResponse(content=b"%PDF-example", content_type="application/pdf")
    with pytest.raises(HTTPException) as exc:
        router_module.generate_academic_document(
            AcademicDocumentGenerateRequest(document_id="transcript-zh"),
            FakeAuth(LIST_PAYLOAD, rendered=remote),
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "证明文件过大，已停止传输"
    assert remote.closed is True


def test_known_official_error_is_exposed_without_internal_error_id(monkeypatch):
    monkeypatch.setattr(
        router_module.AcademicDocumentAPI,
        "generate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AcademicDocumentError("已达打印上限")),
    )
    with pytest.raises(HTTPException) as exc:
        router_module.generate_academic_document(
            AcademicDocumentGenerateRequest(document_id="transcript-zh"),
            SimpleNamespace(),
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "已达打印上限"
