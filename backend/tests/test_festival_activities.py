import asyncio
import io
import zipfile
from datetime import date

import pytest
import requests
from fastapi import HTTPException, Response

from backend.app.routers import festival_activities as festival_router
from backend.app.routers.festival_activities import (
    _archive_scope_label,
    _cache_response,
    _remote_response,
    _safe_certificate_path,
    download_certificate_archive,
    delete_festival_activities_cache,
    get_festival_activities,
)
from backend.app.schemas.festival_activities import CertificateArchiveRequest
from backend.core.auth.client import NEUAuthClient, NEULoginError
from backend.core.festival_activities import FestivalActivity, parse_activity_detail, parse_participation_page


def test_participation_parser_resolves_real_relative_detail_and_labels():
    html = """
    <div class="card">
      活动名称：科学之光 类别：报告 活动状态：已结束
      签到情况：已签到 签退情况：已签退
      <a href="../../front/comp/info?id=54">加油</a>
      <a href="/static/uploads/res/popsciencecert/a.png">下载证书</a>
    </div>
    <a href="?page=2">下一页</a>
    """
    rows, pages = parse_participation_page(html, "popscience")
    assert len(rows) == 1
    assert rows[0].id == "54"
    assert rows[0].name == "科学之光"
    assert rows[0].team_name == "加油"
    assert rows[0].sign_in == "已签到"
    assert rows[0].sign_out == "已签退"
    assert rows[0].certificate_available is True
    assert pages == ["/popscience/comp/ucenter/main/index?page=2"]


@pytest.mark.parametrize("certificate_markup", [
    '<a href="/static/uploads/res/popsciencecert/a.png"></a>',
    '<a href="/static/uploads/res/certificate/17/a.png?t=1"></a>',
    '<a href="/static/uploads/res/popsciencecert/a.png"><img alt="download"></a>',
    '<img data-src="/static/uploads/res/popsciencecert/a.jpg">',
    '<button data-url="/static/uploads/res/popsciencecert/a.webp">download</button>',
    '''<button onclick="window.open('/static/uploads/res/popsciencecert/a.png')">download</button>''',
    '''<button onclick='window.open("/static/uploads/res/certificate/cert_17/a.jpg?t=123")'>download</button>''',
])
def test_participation_parser_finds_certificate_without_visible_label(certificate_markup):
    html = f"""
    <div class="list_item clearfix">
      <div class="media"><div class="media_body">
        <h3><a href="../../front/comp/info?id=54">team</a></h3>
      </div></div>
      <div class="actions">{certificate_markup}</div>
    </div>
    """
    rows, _ = parse_participation_page(html, "popscience")
    assert len(rows) == 1
    assert rows[0].certificate_available is True
    assert rows[0].certificate_url.startswith(
        "https://cxcy.neu.edu.cn/static/uploads/res/"
    )
    assert rows[0].certificate_url.endswith((".png", ".jpg", ".webp"))


@pytest.mark.parametrize("bad_url", [
    "https://evil.example/static/uploads/res/popsciencecert/a.png",
    "http://cxcy.neu.edu.cn/static/uploads/res/popsciencecert/a.png",
    "/static/uploads/res/originalitycert/a.png",
    "/static/uploads/res/popsciencecert/a.svg",
    "/static/uploads/res/certificate/a.png",
    "/static/uploads/res/certificate/17/nested/a.png",
    "/static/uploads/res/certificate/17/a.png?download=https://evil.example",
    "/static/uploads/res/certificate/17/a.png?t=not-a-timestamp",
    "/static/uploads/res/certificate/17/%2e%2e/a.png",
    "https://user@cxcy.neu.edu.cn/static/uploads/res/certificate/17/a.png",
    "https://cxcy.neu.edu.cn:444/static/uploads/res/certificate/17/a.png",
])
def test_participation_parser_rejects_untrusted_certificate_candidates(bad_url):
    html = f"""
    <div class="list_item">
      <a href="../../front/comp/info?id=54">team</a>
      <a href="{bad_url}">download</a>
    </div>
    """
    rows, _ = parse_participation_page(html, "popscience")
    assert len(rows) == 1
    assert rows[0].certificate_available is False


@pytest.mark.parametrize("certificate_url", [
    "/static/uploads/res/popsciencecert/unrelated.png",
    "/static/uploads/res/certificate/other_record/unrelated.png",
])
def test_participation_parser_does_not_attach_page_level_certificate(certificate_url):
    html = f"""
    <div class="page">
      <div class="media_body">
        <h3><a href="../../front/comp/info?id=54">team</a></h3>
      </div>
      <aside><a href="{certificate_url}"></a></aside>
    </div>
    """
    rows, _ = parse_participation_page(html, "popscience")
    assert len(rows) == 1
    assert rows[0].certificate_available is False


def test_participation_parser_keeps_neighboring_certificates_isolated():
    html = """
    <div class="list_item">
      <div><a href="../../front/comp/info?id=1">one</a></div>
      <div><a href="/static/uploads/res/popsciencecert/one.png"></a></div>
    </div>
    <div class="list_item">
      <div><a href="../../front/comp/info?id=2">two</a></div>
      <div><a href="/static/uploads/res/popsciencecert/two.png"></a></div>
    </div>
    """
    rows, _ = parse_participation_page(html, "popscience")
    assert [row.id for row in rows] == ["1", "2"]
    assert rows[0].certificate_url.endswith("/one.png")
    assert rows[1].certificate_url.endswith("/two.png")


def test_participation_parser_keeps_generic_certificates_with_their_record():
    html = """
    <div class="list_item">
      <div><a href="../../front/comp/info?id=1">one</a></div>
      <div><a href="/static/uploads/res/certificate/cert_one/one.png?t=1"></a></div>
    </div>
    <div class="list_item">
      <div><a href="../../front/comp/info?id=2">two</a></div>
      <div><a href="/static/uploads/res/certificate/cert_two/two.png?t=2"></a></div>
    </div>
    """
    rows, _ = parse_participation_page(html, "popscience")
    assert [row.id for row in rows] == ["1", "2"]
    assert rows[0].certificate_url.endswith("/cert_one/one.png")
    assert rows[1].certificate_url.endswith("/cert_two/two.png")


def test_detail_parser_produces_iso_start_and_plain_text():
    activity = FestivalActivity("54", "科普节", "旧名称", "https://cxcy.neu.edu.cn/popscience/comp/front/comp/info?id=54")
    html = """
    <header><div class="title">最新公告</div></header>
    <div class="body_box"><div class="body_part1"><div class="title">科学探秘</div>
      <div>类别：竞赛活动</div><div>类型：报告</div>
      <div>活动时间：2025-04-20 08:30（2小时）</div>
      <div>所属部门：体育部</div><div>活动地点：体育馆</div>
    </div></div>
    <div class="body_part2"><div class="p_title">活动简介</div>
      <div class="content"><p>第一段</p><script>secret()</script><p>第二段</p></div>
    </div>
    """
    result = parse_activity_detail(html, activity)
    assert result.name == "科学探秘"
    assert result.start_time == "2025-04-20T08:30"
    assert "第一段" in result.description and "第二段" in result.description
    assert "secret" not in result.description


def test_service_redirect_is_rejected_before_contacting_untrusted_host(monkeypatch):
    client = NEUAuthClient(restore_session=False)
    client._logged_in = True
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(url)
        response = requests.Response()
        response.status_code = 302
        response.url = url
        response.headers["Location"] = "https://evil.example/steal"
        response._content = b""
        return response

    monkeypatch.setattr(client.session, "request", fake_request)
    with pytest.raises(NEULoginError, match="不受信任"):
        client.request_service("cxcy", "GET", "/popscience/comp/ucenter/main/index")
    assert calls == ["https://cxcy.neu.edu.cn/popscience/comp/ucenter/main/index"]


@pytest.mark.parametrize("location", [
    "http://cxcy.neu.edu.cn/ucenter/index/login",
    "https://cxcy.neu.edu.cn:444/ucenter/index/login",
    "https://user@cxcy.neu.edu.cn/ucenter/index/login",
])
def test_service_redirect_rejects_noncanonical_origin(monkeypatch, location):
    client = NEUAuthClient(restore_session=False)
    client._logged_in = True

    def fake_request(method, url, **kwargs):
        response = requests.Response()
        response.status_code = 302
        response.url = url
        response.headers["Location"] = location
        response._content = b""
        return response

    monkeypatch.setattr(client.session, "request", fake_request)
    with pytest.raises(NEULoginError, match="不受信任"):
        client.request_service("cxcy", "GET", "/popscience/comp/ucenter/main/index")


def test_service_same_origin_login_trampoline_establishes_session_and_retries(monkeypatch):
    client = NEUAuthClient(restore_session=False)
    client._logged_in = True
    urls = []

    def response(url, status=200):
        item = requests.Response()
        item.status_code = status
        item.url = url
        item._content = b"ok"
        return item

    results = iter([
        response("https://cxcy.neu.edu.cn/ucenter/index/login?bloginurl=x"),
        response("https://cxcy.neu.edu.cn/ucenter/main/index"),
        response("https://cxcy.neu.edu.cn/popscience/comp/ucenter/main/index"),
    ])

    def fake_redirects(method, url, **kwargs):
        urls.append(url)
        return next(results)

    monkeypatch.setattr(client, "_request_service_redirects", fake_redirects)
    result = client.request_service("cxcy", "GET", "/popscience/comp/ucenter/main/index")
    assert result.status_code == 200
    assert urls[0] == urls[2]
    assert urls[1] == "https://cxcy.neu.edu.cn/ucenter/auth/caslogin?type=student"


def test_service_expired_cas_recovers_primary_login_then_rebuilds_service_session(monkeypatch):
    client = NEUAuthClient(restore_session=False)
    client._logged_in = True
    urls = []
    recoveries = []

    def response(url):
        item = requests.Response()
        item.status_code = 200
        item.url = url
        item._content = b"ok"
        return item

    results = iter([
        response("https://cxcy.neu.edu.cn/ucenter/index/login"),
        response("https://pass.neu.edu.cn/tpass/login?service=cxcy"),
        response("https://cxcy.neu.edu.cn/ucenter/main/index"),
        response("https://cxcy.neu.edu.cn/popscience/comp/ucenter/main/index"),
    ])

    def fake_redirects(method, url, **kwargs):
        urls.append(url)
        return next(results)

    def recover():
        recoveries.append(True)
        client._logged_in = True
        return True

    monkeypatch.setattr(client, "_request_service_redirects", fake_redirects)
    monkeypatch.setattr(client, "ensure_login", recover)

    result = client.request_service("cxcy", "GET", "/popscience/comp/ucenter/main/index")

    assert result.url.endswith("/popscience/comp/ucenter/main/index")
    assert recoveries == [True]
    assert urls.count("https://cxcy.neu.edu.cn/ucenter/auth/caslogin?type=student") == 2


def test_service_expired_cas_reports_login_failure_after_one_recovery_attempt(monkeypatch):
    client = NEUAuthClient(restore_session=False)
    client._logged_in = True

    def response(url):
        item = requests.Response()
        item.status_code = 200
        item.url = url
        item._content = b"ok"
        return item

    results = iter([
        response("https://cxcy.neu.edu.cn/ucenter/index/login"),
        response("https://pass.neu.edu.cn/tpass/login?service=cxcy"),
    ])
    monkeypatch.setattr(client, "_request_service_redirects", lambda *_args, **_kwargs: next(results))
    monkeypatch.setattr(client, "ensure_login", lambda: False)

    with pytest.raises(NEULoginError, match="统一认证会话已过期"):
        client.request_service("cxcy", "GET", "/popscience/comp/ucenter/main/index")


def test_certificate_path_and_inclusive_range_security():
    assert _safe_certificate_path("https://cxcy.neu.edu.cn/static/uploads/res/popsciencecert/a.png") == "/static/uploads/res/popsciencecert/a.png"
    assert _safe_certificate_path("https://cxcy.neu.edu.cn/static/uploads/res/certificate/17/a.png?t=1") == "/static/uploads/res/certificate/17/a.png?t=1"
    with pytest.raises(ValueError):
        _safe_certificate_path("https://cxcy.neu.edu.cn/static/uploads/res/comp/a.png")
    with pytest.raises(ValueError):
        _safe_certificate_path("https://cxcy.neu.edu.cn/static/uploads/res/popsciencecert/%2e%2e/comp/a.png")
    with pytest.raises(ValueError):
        _safe_certificate_path("https://cxcy.neu.edu.cn/static/uploads/res/popsciencecert/%252e%252e/comp/a.png")
    CertificateArchiveRequest(start_date=date(2025, 1, 1), end_date=date(2026, 1, 5))
    with pytest.raises(ValueError):
        CertificateArchiveRequest(start_date=date(2025, 1, 1), end_date=date(2026, 1, 6))
    assert _archive_scope_label(date(2025, 9, 1), date(2026, 2, 28)) == "2025-2026秋季学期"
    assert _archive_scope_label(date(2026, 3, 1), date(2026, 8, 31)) == "2025-2026春季学期"
    assert _archive_scope_label(date(2025, 8, 31), date(2026, 8, 30)) == "2025-2026学年"
    assert _archive_scope_label(date(2026, 3, 2), date(2026, 8, 31)) == "2026-03-02_2026-08-31"


@pytest.mark.parametrize("bad_url", [
    "/static/uploads/res/certificate/a.png",
    "/static/uploads/res/certificate/17/nested/a.png",
    "/static/uploads/res/certificate/17/a.svg",
    "/static/uploads/res/certificate/17/%2e%2e/a.png",
    "/static/uploads/res/certificate/17/a.png?next=https://evil.example",
    "/static/uploads/res/certificate/17/a.png?t=not-a-timestamp",
    "https://user@cxcy.neu.edu.cn/static/uploads/res/certificate/17/a.png",
    "https://cxcy.neu.edu.cn:444/static/uploads/res/certificate/17/a.png",
])
def test_generic_certificate_path_rejects_ambiguous_or_noncanonical_urls(bad_url):
    with pytest.raises(ValueError):
        _safe_certificate_path(bad_url)


def test_public_remote_response_hides_remote_urls():
    payload = {"activities": [{"id": "1", "section": "科普节", "name": "A", "detail_url": "secret", "certificate_url": "secret"}]}
    result = _remote_response("user", payload)
    assert "detail_url" not in result["activities"][0]
    assert "certificate_url" not in result["activities"][0]
    assert result["source"] == "remote" and result["cache"] is None


def test_cached_response_preserves_metadata_and_hides_private_urls():
    class Entry:
        payload = {"activities": [{
            "id": "1", "section": "科普节", "name": "A",
            "detail_url": "secret", "certificate_url": "secret",
        }], "warnings": []}

        def metadata(self, *, is_stale):
            return {"revision": "v1:test", "is_stale": is_stale}

    result = _cache_response("user", Entry(), True)
    assert result["source"] == "cache"
    assert result["cache"] == {"revision": "v1:test", "is_stale": True}
    assert "detail_url" not in result["activities"][0]
    assert "certificate_url" not in result["activities"][0]


def test_list_fetches_remote_on_every_request(monkeypatch):
    calls = []
    auth = _ArchiveAuth([])

    def fetch(received_auth):
        calls.append(received_auth)
        return {"activities": [], "warnings": []}

    monkeypatch.setattr(festival_router, "fetch_festival_activities", fetch)
    first_response = Response()
    second_response = Response()
    first = get_festival_activities(first_response, auth)
    second = get_festival_activities(second_response, auth)
    assert calls == [auth, auth]
    assert first["source"] == second["source"] == "remote"
    assert first_response.headers["Cache-Control"] == "no-store"
    assert second_response.headers["Cache-Control"] == "no-store"


def test_list_maps_expired_login_to_recoverable_401(monkeypatch):
    monkeypatch.setattr(
        festival_router,
        "fetch_festival_activities",
        lambda _auth: (_ for _ in ()).throw(NEULoginError("expired")),
    )

    with pytest.raises(HTTPException) as error:
        get_festival_activities(Response(), _ArchiveAuth([]))

    assert error.value.status_code == 401


def test_list_retries_once_after_login_expires_mid_fetch(monkeypatch):
    calls = []

    def fetch(_auth):
        calls.append(True)
        if len(calls) == 1:
            raise NEULoginError("expired")
        return {"activities": [], "warnings": []}

    monkeypatch.setattr(festival_router, "fetch_festival_activities", fetch)

    result = get_festival_activities(Response(), _ArchiveAuth([]))

    assert result["source"] == "remote"
    assert len(calls) == 2


def test_delete_cache_is_idempotent_and_scoped_to_active_account(monkeypatch):
    calls = []

    def delete_resource(**kwargs):
        calls.append(kwargs)
        return False, 0

    monkeypatch.setattr(
        festival_router._cache_coordinator, "delete_resource", delete_resource
    )
    monkeypatch.setattr(festival_router, "get_auth_generation", lambda: 7)
    result = delete_festival_activities_cache(_ArchiveAuth([]))
    assert result == {"success": True, "deleted": False, "cancelled_jobs": 0}
    assert calls == [{
        "account_id": "20250001",
        "resource": "festival-activities",
        "identity_epoch": 7,
    }]


def test_delete_cache_returns_conflict_after_account_switch(monkeypatch):
    monkeypatch.setattr(
        festival_router._cache_coordinator,
        "delete_resource",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("identity changed")),
    )
    with pytest.raises(HTTPException) as conflict:
        delete_festival_activities_cache(_ArchiveAuth([]))
    assert conflict.value.status_code == 409


class _CertificateResponse:
    def __init__(
        self,
        body,
        content_type="image/png",
        url="https://cxcy.neu.edu.cn/static/uploads/res/popsciencecert/a.png",
    ):
        self.body = body
        self.url = url
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
        }
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, _size):
        yield self.body

    def close(self):
        self.closed = True


class _ArchiveAuth:
    username = "20250001"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.paths = []

    def request_service(self, service, method, path, **kwargs):
        assert service == "cxcy" and method == "GET" and kwargs.get("stream") is True
        self.paths.append(path)
        return next(self.responses)


def _archive_item(url, *, activity_id="1", name="同名活动"):
    return {
        "id": activity_id,
        "section": "科普节",
        "name": name,
        "start_time": "2025-04-20T08:30",
        "certificate_available": True,
        "certificate_url": url,
    }


def _mock_fetch(monkeypatch, activities):
    monkeypatch.setattr(
        festival_router,
        "fetch_festival_activities",
        lambda _auth: {"activities": activities, "warnings": []},
    )


def _read_streaming_response(response):
    async def consume():
        chunks = [chunk async for chunk in response.body_iterator]
        if response.background is not None:
            await response.background()
        return b"".join(chunks)

    return asyncio.run(consume())


def test_archive_success_deduplicates_names_and_closes_responses(monkeypatch):
    items = [
        _archive_item("/static/uploads/res/popsciencecert/a.png", activity_id="1"),
        _archive_item("/static/uploads/res/popsciencecert/b.png", activity_id="2"),
    ]
    _mock_fetch(monkeypatch, items)
    monkeypatch.setattr(
        festival_router,
        "read_cache",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("archive must not read metadata cache")
        ),
    )
    png = b"\x89PNG\r\n\x1a\n" + b"data"
    upstream = [_CertificateResponse(png), _CertificateResponse(png)]
    response = download_certificate_archive(
        CertificateArchiveRequest(start_date=date(2025, 3, 1), end_date=date(2025, 8, 31)),
        _ArchiveAuth(upstream),
    )
    body = _read_streaming_response(response)
    assert response.headers["x-certificate-succeeded"] == "2"
    assert response.headers["x-certificate-failed"] == "0"
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        names = archive.namelist()
        assert len(names) == 2 and names[0] != names[1]
        assert "下载说明.txt" not in names
    assert all(item.closed for item in upstream)


def test_archive_partial_failure_contains_manifest(monkeypatch):
    items = [
        _archive_item("/static/uploads/res/popsciencecert/a.png", activity_id="1", name="成功活动"),
        _archive_item("/static/uploads/res/popsciencecert/b.png", activity_id="2", name="失败活动"),
    ]
    _mock_fetch(monkeypatch, items)
    png = b"\x89PNG\r\n\x1a\n" + b"data"
    upstream = [_CertificateResponse(png), _CertificateResponse(b"<html>login</html>", "text/html")]
    response = download_certificate_archive(
        CertificateArchiveRequest(start_date=date(2025, 3, 1), end_date=date(2025, 8, 31)),
        _ArchiveAuth(upstream),
    )
    body = _read_streaming_response(response)
    assert response.headers["x-certificate-succeeded"] == "1"
    assert response.headers["x-certificate-failed"] == "1"
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert "下载说明.txt" in archive.namelist()
        assert "失败活动" in archive.read("下载说明.txt").decode("utf-8")
    assert all(item.closed for item in upstream)


def test_archive_rejects_zero_certificates_all_invalid_and_excess_count(monkeypatch):
    request = CertificateArchiveRequest(
        start_date=date(2025, 3, 1), end_date=date(2025, 8, 31)
    )
    _mock_fetch(monkeypatch, [])
    with pytest.raises(HTTPException) as no_data:
        download_certificate_archive(request, _ArchiveAuth([]))
    assert no_data.value.status_code == 404

    item = _archive_item("/static/uploads/res/popsciencecert/a.png")
    _mock_fetch(monkeypatch, [item])
    invalid = _CertificateResponse(b"<html>login</html>", "text/html")
    with pytest.raises(HTTPException) as all_failed:
        download_certificate_archive(request, _ArchiveAuth([invalid]))
    assert all_failed.value.status_code == 502 and invalid.closed

    _mock_fetch(monkeypatch, [item, {**item, "id": "2"}])
    monkeypatch.setattr(festival_router, "MAX_CERTIFICATES", 1)
    with pytest.raises(HTTPException) as too_many:
        download_certificate_archive(request, _ArchiveAuth([]))
    assert too_many.value.status_code == 413


def test_archive_aborts_with_401_when_login_expires_during_certificate_download(monkeypatch):
    item = _archive_item("/static/uploads/res/popsciencecert/a.png")
    _mock_fetch(monkeypatch, [item])
    auth = _ArchiveAuth([])
    auth.request_service = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        NEULoginError("expired")
    )

    with pytest.raises(HTTPException) as error:
        download_certificate_archive(
            CertificateArchiveRequest(
                start_date=date(2025, 3, 1), end_date=date(2025, 8, 31)
            ),
            auth,
        )

    assert error.value.status_code == 401


def test_archive_retries_the_current_certificate_after_login_recovery(monkeypatch):
    item = _archive_item("/static/uploads/res/popsciencecert/a.png")
    _mock_fetch(monkeypatch, [item])
    png = b"\x89PNG\r\n\x1a\n" + b"data"
    recovered_response = _CertificateResponse(png)
    calls = []

    class RecoveringAuth(_ArchiveAuth):
        def request_service(self, service, method, path, **kwargs):
            calls.append(path)
            if len(calls) == 1:
                raise NEULoginError("expired")
            return recovered_response

    response = download_certificate_archive(
        CertificateArchiveRequest(
            start_date=date(2025, 3, 1), end_date=date(2025, 8, 31)
        ),
        RecoveringAuth([]),
    )
    body = _read_streaming_response(response)

    assert len(calls) == 2
    assert response.headers["x-certificate-succeeded"] == "1"
    assert zipfile.is_zipfile(io.BytesIO(body))
    assert recovered_response.closed is True


def test_archive_rejects_same_origin_redirect_outside_certificate_directories(monkeypatch):
    request = CertificateArchiveRequest(
        start_date=date(2025, 3, 1), end_date=date(2025, 8, 31)
    )
    item = _archive_item("/static/uploads/res/popsciencecert/a.png")
    _mock_fetch(monkeypatch, [item])
    redirected = _CertificateResponse(
        b"\x89PNG\r\n\x1a\n" + b"data",
        url="https://cxcy.neu.edu.cn/static/uploads/res/avatar/a.png",
    )
    with pytest.raises(HTTPException) as all_failed:
        download_certificate_archive(request, _ArchiveAuth([redirected]))
    assert all_failed.value.status_code == 502
    assert redirected.closed is True
