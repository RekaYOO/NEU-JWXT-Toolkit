import asyncio
import io
import time
import zipfile
from contextlib import nullcontext
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from fastapi import HTTPException, Response
from pydantic import ValidationError

from backend.app import dependencies
from backend.app.routers import auth as auth_router
from backend.app.routers import festival_activities as router
from backend.app.schemas.auth import WebVPNPasswordStartRequest, WebVPNQRStartRequest
from backend.app.schemas.festival_activities import CertificateArchiveRequest, FestivalSettingsUpdate
from backend.core.auth.client import NEUAuthClient, NEULoginError, SERVICE_CONFIGS
from backend.core.festival_activities import fetch_festival_activities, parse_participation_page
from backend.core.festival_service import FestivalServiceClient, FestivalServiceError, read_preference
from backend.core.network import WebVPNUrlCodec
from backend.core.storage.storage import Storage, StorageConfig

ORIGIN = "https://cxcy.neu.edu.cn"
PROBE = "/popscience/comp/ucenter/main/index"


def response(url, text="", *, status=200, location=None):
    result = requests.Response()
    result.url = url
    result.status_code = status
    result._content = text.encode()
    result._content_consumed = True
    result.headers["Content-Type"] = "text/html"
    if location:
        result.headers["Location"] = location
    return result


def storage(mode="follow"):
    return SimpleNamespace(load_config=lambda: {"cxcy": {"network_mode": mode}})


@pytest.mark.parametrize("primary", ["direct", "webvpn"])
@pytest.mark.parametrize("preference", ["follow", "direct", "webvpn"])
def test_whole_scan_uses_one_route_without_mutating_primary(primary, preference):
    auth = NEUAuthClient(username="20250001", network_mode=primary, restore_session=False)
    auth._logged_in = True
    calls = []

    def request(method, url, **kwargs):
        calls.append(url)
        return response(url)

    auth.session.request = request
    client = FestivalServiceClient(auth, storage(preference))
    assert fetch_festival_activities(client) == {"activities": [], "warnings": []}
    assert len(calls) == 4
    expected = primary if preference == "follow" else preference
    assert all(("webvpn.neu.edu.cn" in url) == (expected == "webvpn") for url in calls)
    assert auth.active_mode == primary


def test_client_freezes_route_while_new_client_observes_settings():
    mode = {"cxcy": {"network_mode": "direct"}}
    store = SimpleNamespace(load_config=lambda: mode)
    auth = Mock(active_mode="direct")
    old = FestivalServiceClient(auth, store)
    mode["cxcy"]["network_mode"] = "webvpn"
    assert old.network_mode == "direct"
    assert FestivalServiceClient(auth, store).network_mode == "webvpn"


def test_settings_persist_and_preserve_other_settings(tmp_path, monkeypatch):
    config = StorageConfig(data_dir=str(tmp_path))
    store = Storage(config)
    store.save_config({"course_selection": {"network_mode": "direct"}, "other": 7})
    monkeypatch.setattr(router, "peek_auth_client", lambda: None)
    monkeypatch.setattr(router, "get_primary_network_mode_hint", lambda _: "webvpn")
    assert read_preference(store) == "follow"
    result = router.update_festival_settings(
        FestivalSettingsUpdate(network_mode="webvpn"), Response(), store, probe=False,
    )
    assert result.network_mode == "webvpn"
    restored = Storage(config)
    assert read_preference(restored) == "webvpn"
    assert restored.load_config()["other"] == 7
    assert restored.load_config()["course_selection"]["network_mode"] == "direct"


def test_settings_reject_unknown_route():
    with pytest.raises(ValidationError):
        FestivalSettingsUpdate(network_mode="auto")


@pytest.mark.parametrize("relative_proxy", [False, True])
@pytest.mark.parametrize("section", ["originality", "popscience", "technical", "business"])
def test_proxy_list_detail_pagination_and_certificate_links_are_canonical(section, relative_proxy):
    def proxy(path):
        url = WebVPNUrlCodec.convert_url(ORIGIN + path)
        return url.removeprefix("https://webvpn.neu.edu.cn") if relative_proxy else url

    html = f"""<div class="list_item">
      <a href="{proxy(f'/{section}/comp/front/comp/info?id=1')}">team</a>
      <a href="{proxy(f'/static/uploads/res/{section}cert/test.png')}">证书</a>
      </div><a href="{proxy(f'/{section}/comp/ucenter/main/index?page=2')}">下一页</a>"""
    activities, pages = parse_participation_page(html, section)
    assert len(activities) == 1
    assert activities[0].detail_url == f"{ORIGIN}/{section}/comp/front/comp/info?id=1"
    assert activities[0].certificate_url == f"{ORIGIN}/static/uploads/res/{section}cert/test.png"
    assert pages == [f"/{section}/comp/ucenter/main/index?page=2"]


@pytest.mark.parametrize("bad", [
    "https://other.example/static/uploads/res/popsciencecert/x.png",
    WebVPNUrlCodec.convert_url("https://jwxt.neu.edu.cn/static/uploads/res/popsciencecert/x.png"),
    WebVPNUrlCodec.convert_url("http://cxcy.neu.edu.cn/static/uploads/res/popsciencecert/x.png"),
    WebVPNUrlCodec.convert_url("https://cxcy.neu.edu.cn:444/static/uploads/res/popsciencecert/x.png"),
    WebVPNUrlCodec.convert_url(ORIGIN + "/static/uploads/res/popsciencecert/../avatar/x.png"),
    WebVPNUrlCodec.convert_url(ORIGIN + "/static/uploads/res/popsciencecert/%252e%252e/x.png"),
    "https://webvpn.neu.edu.cn/untrusted/" + WebVPNUrlCodec.encrypt_hostname("cxcy.neu.edu.cn") + "/x",
])
def test_untrusted_proxy_urls_are_rejected(bad):
    with pytest.raises(ValueError):
        router._safe_certificate_path(bad)
    html = f'<div class="list_item"><a href="{bad}">证书</a></div>'
    assert parse_participation_page(html, "popscience") == ([], [])


def test_cxcy_cas_redirects_stay_on_gateway_and_upgrade_official_cas():
    client = NEUAuthClient(restore_session=False)
    calls = []
    destinations = iter([
        ("http://pass.neu.edu.cn/tpass/login?service=test", 302),
        (ORIGIN + "/ucenter/main/index", 302),
        (None, 200),
    ])

    def request(method, url, **kwargs):
        calls.append(url)
        location, status = next(destinations)
        return response(url, status=status, location=location)

    client.session.request = request
    assert client.ensure_service_session("cxcy", network_mode_override="webvpn")
    assert all(url.startswith("https://webvpn.neu.edu.cn/https/") for url in calls)
    assert client.active_mode == "direct"


def test_cxcy_auth_flow_does_not_use_jwxk_token_or_primary_health(monkeypatch):
    client = NEUAuthClient(restore_session=False)
    monkeypatch.setattr(client, "ensure_service_session", Mock(return_value=True))
    monkeypatch.setattr(client, "_webvpn_health_check", Mock(side_effect=AssertionError))
    monkeypatch.setattr(client, "get_service_token", Mock(side_effect=AssertionError))
    monkeypatch.setattr(client, "_request_service_redirects", Mock(return_value=response(
        WebVPNUrlCodec.convert_url(ORIGIN + PROBE),
    )))
    result = client._verify_webvpn_login_target("cxcy")
    assert result == {"authenticated": True, "service_auth_state": "authenticated"}
    client._request_service_redirects.assert_called_once()


def test_gateway_success_is_retained_when_cxcy_unavailable(monkeypatch):
    client = NEUAuthClient(restore_session=False)
    monkeypatch.setattr(client, "ensure_service_session", Mock(side_effect=requests.Timeout))
    assert client._verify_webvpn_login_target("cxcy") == {
        "authenticated": True, "service_auth_state": "service_unavailable",
    }


def test_pending_sms_is_not_replaced_by_service_probe(monkeypatch):
    client = NEUAuthClient("20250001", "unused", restore_session=False)
    client._logged_in = True
    flow = {"id": "pending", "target_service": "cxcy", "expires_at": time.time() + 300}
    client._webvpn_sms_flow = flow
    monkeypatch.setattr(client, "_request_service_redirects", Mock(return_value=response(
        WebVPNUrlCodec.convert_url("https://pass.neu.edu.cn/tpass/login"),
    )))
    recover = Mock(side_effect=AssertionError)
    monkeypatch.setattr(client, "_login_webvpn_service_identity", recover)
    with pytest.raises(NEULoginError):
        client.ensure_service_session("cxcy", network_mode_override="webvpn")
    assert client._webvpn_sms_flow is flow
    recover.assert_not_called()


@pytest.mark.parametrize("error,code", [
    (requests.Timeout(), "CXCY_NETWORK_UNREACHABLE"),
    (NEULoginError("private upstream text"), "CXCY_LOGIN_REQUIRED"),
    (requests.HTTPError(), "CXCY_SERVICE_UNAVAILABLE"),
])
def test_errors_are_service_scoped_and_do_not_leak_upstream_text(error, code):
    primary = Mock(active_mode="direct")
    primary.request_service.side_effect = error
    client = FestivalServiceClient(primary, storage())
    with pytest.raises(FestivalServiceError) as caught:
        client.request_service("cxcy", "GET", PROBE)
    assert caught.value.error_code == code
    assert caught.value.auth_scope == "cxcy"
    assert "private upstream" not in str(caught.value)


def test_status_probes_only_one_page_and_closes_response(monkeypatch):
    primary = Mock(username="20250001", is_logged_in=True, active_mode="direct")
    result = Mock()
    service = Mock(network_mode="webvpn")
    service.request_service.return_value = result
    monkeypatch.setattr(router, "peek_auth_client", lambda: primary)
    monkeypatch.setattr(router, "get_festival_service_client", lambda _: service)
    monkeypatch.setattr(router, "remote_session_guard", nullcontext)
    status = router.get_festival_service_status(Response(), storage("webvpn"))
    assert status.service_authenticated
    service.request_service.assert_called_once_with("cxcy", "GET", PROBE, timeout=8)
    result.close.assert_called_once()


def test_cxcy_password_target_merges_without_replacing_primary(monkeypatch):
    active = Mock(username="20250001", is_logged_in=True)
    candidate = Mock(username="20250001", password="unused")
    candidate.start_webvpn_password_login.return_value = {
        "status": "authenticated", "target_service": "cxcy",
    }
    builder = Mock(return_value=candidate)
    monkeypatch.setattr(auth_router, "NEUAuthClient", builder)
    monkeypatch.setattr(auth_router, "peek_auth_client", lambda: active)
    monkeypatch.setattr(auth_router, "set_auth_client", Mock(side_effect=AssertionError))
    result = auth_router.start_webvpn_password_login(WebVPNPasswordStartRequest(
        username="20250001", password="unused", target_service="cxcy",
    ))
    assert result["success"]
    assert builder.call_args.kwargs["cookie_file"] is None
    candidate.start_webvpn_password_login.assert_called_once_with(target_service="cxcy")
    active.adopt_webvpn_gateway_session.assert_called_once_with(candidate)


def test_service_gateway_merge_rejects_different_account(tmp_path, monkeypatch):
    active = NEUAuthClient("20250001", cookie_file=str(tmp_path / "session.json"), restore_session=False)
    active._logged_in = True
    active.session.cookies.set("primary", "kept", domain="jwxt.neu.edu.cn")
    candidate = NEUAuthClient("20250002", restore_session=False)
    candidate.session.cookies.set("gateway", "candidate", domain="webvpn.neu.edu.cn")
    monkeypatch.setattr(auth_router, "peek_auth_client", lambda: active)
    with pytest.raises(NEULoginError):
        auth_router._commit_webvpn_login(candidate, target_service="cxcy", remember=False)
    assert active.active_mode == "direct"
    assert active.session.cookies.get("primary") == "kept"
    assert not (tmp_path / "session.json").exists()


@pytest.mark.parametrize("actual,code", [
    ("20250002", "WEBVPN_ACCOUNT_MISMATCH"),
    ("", "WEBVPN_ACCOUNT_UNVERIFIED"),
])
def test_qr_identity_uses_authenticated_account_not_the_supplied_hint(monkeypatch, actual, code):
    candidate = NEUAuthClient("20250001", restore_session=False)

    def identity():
        candidate.username = actual
        return bool(actual)

    monkeypatch.setattr(candidate, "_webvpn_health_check", identity)
    with pytest.raises(NEULoginError) as caught:
        candidate._verify_service_qr_identity("cxcy")
    assert caught.value.error_code == code


def test_matching_qr_identity_can_continue_without_changing_primary_route(monkeypatch):
    candidate = NEUAuthClient("20250001", restore_session=False)

    def identity():
        candidate.username = "20250001"
        return True

    monkeypatch.setattr(candidate, "_webvpn_health_check", identity)
    candidate._verify_service_qr_identity("cxcy")
    assert candidate.username == "20250001"
    assert candidate.active_mode == "direct"


def test_proxy_certificate_archive_uses_same_route_and_keeps_download_validation(monkeypatch):
    certificate = ORIGIN + "/static/uploads/res/popsciencecert/test.png"
    primary = Mock(username="20250001", active_mode="direct")
    image = response(WebVPNUrlCodec.convert_url(certificate))
    image._content = b"\x89PNG\r\n\x1a\nfixture"
    image.headers["Content-Type"] = "image/png"
    primary.request_service.return_value = image
    service = FestivalServiceClient(primary, storage("webvpn"))
    monkeypatch.setattr(router, "fetch_festival_activities", lambda auth: {
        "activities": [{
            "id": "1", "name": "fixture", "section": "科普节",
            "start_time": "2026-04-20T08:30", "certificate_available": True,
            "certificate_url": certificate,
        }],
    })
    archive = router.download_certificate_archive(CertificateArchiveRequest(
        start_date=date(2026, 3, 1), end_date=date(2026, 8, 31),
    ), service)

    async def consume():
        return b"".join([chunk async for chunk in archive.body_iterator])

    body = asyncio.run(consume())
    asyncio.run(archive.background())
    with zipfile.ZipFile(io.BytesIO(body)) as saved:
        assert len(saved.namelist()) == 1
        assert saved.read(saved.namelist()[0]).startswith(b"\x89PNG")
    assert primary.request_service.call_args.kwargs["network_mode_override"] == "webvpn"


def test_archive_auth_failure_after_one_image_keeps_scoped_error_and_closes_spool(monkeypatch):
    certificate = ORIGIN + "/static/uploads/res/popsciencecert/test.png"
    primary = Mock(username="20250001", active_mode="direct")
    image = response(certificate)
    image._content = b"\x89PNG\r\n\x1a\nfixture"
    image.headers["Content-Type"] = "image/png"
    primary.request_service.side_effect = [image, NEULoginError("expired")]
    service = FestivalServiceClient(primary, storage("direct"))
    item = {
        "name": "fixture", "section": "科普节", "start_time": "2026-04-20T08:30",
        "certificate_available": True, "certificate_url": certificate,
    }
    monkeypatch.setattr(router, "fetch_festival_activities", lambda _: {"activities": [item, item]})
    spool = io.BytesIO()
    monkeypatch.setattr(router.tempfile, "SpooledTemporaryFile", lambda **_: spool)
    with pytest.raises(HTTPException) as caught:
        router.download_certificate_archive(CertificateArchiveRequest(
            start_date=date(2026, 3, 1), end_date=date(2026, 8, 31),
        ), service)
    assert caught.value.status_code == 401
    assert caught.value.detail["auth_scope"] == "cxcy"
    assert spool.closed


def test_legacy_http_activity_links_upgrade_without_accepting_http_certificates():
    html = """<div class="list_item">
      <a href="http://cxcy.neu.edu.cn/popscience/comp/front/comp/info?id=1">team</a>
      <a href="http://cxcy.neu.edu.cn/static/uploads/res/popsciencecert/test.png">证书</a>
      </div><a href="http://cxcy.neu.edu.cn/popscience/comp/ucenter/main/index?page=2">下一页</a>"""
    activities, pages = parse_participation_page(html, "popscience")
    assert activities[0].detail_url == ORIGIN + "/popscience/comp/front/comp/info?id=1"
    assert not activities[0].certificate_url
    assert pages == ["/popscience/comp/ucenter/main/index?page=2"]


@pytest.mark.parametrize("mode", ["direct", "webvpn"])
def test_full_scan_follows_pagination_and_details_on_fixed_route(mode):
    primary = NEUAuthClient("20250001", network_mode="direct", restore_session=False)
    primary._logged_in = True
    calls = []

    def routed(path):
        url = ORIGIN + path
        return WebVPNUrlCodec.convert_url(url) if mode == "webvpn" else url

    def request(method, url, **kwargs):
        calls.append(url)
        canonical = WebVPNUrlCodec.restore_service_url(url, origin=ORIGIN)
        path = canonical.removeprefix(ORIGIN)
        section = path.split("/")[1]
        if "/front/" in path:
            if section == "business":
                return response(url, status=404)
            return response(url, "<h1>fixture</h1><div>活动时间：2026-04-20 08:30</div>")
        if "?page=2" in path:
            return response(url, f'<a href="{routed(f"/{section}/comp/front/comp/info?id=1")}">team</a>')
        return response(url, f'<a href="{routed(f"/{section}/comp/ucenter/main/index?page=2")}">下一页</a>')

    primary.session.request = request
    result = fetch_festival_activities(FestivalServiceClient(primary, storage(mode)))
    assert len(result["activities"]) == 4
    assert len(calls) == 12
    assert all(("webvpn.neu.edu.cn" in url) == (mode == "webvpn") for url in calls)
    assert len(result["warnings"]) == 1
    assert "详情不存在" in result["warnings"][0]
    assert primary.active_mode == "direct"
