from datetime import datetime

import pytest
from fastapi import Response

from backend.core.course_selection import (
    JwxkError,
    parse_public_batches,
    resolve_network_mode,
)
from backend.core.auth import NEUAuthClient
from backend.core.auth.client import NEULoginError
from backend.core.network import WebVPNUrlCodec
from backend.app.routers import course_selection
from backend.app.schemas.course_selection import JwxkSettingsUpdate


class MemoryStorage:
    def __init__(self, config=None):
        self.config = dict(config or {})

    def load_config(self):
        return dict(self.config)

    def save_config(self, config):
        self.config = dict(config)


def _html(rows: str) -> str:
    return f"<script>loginVue.batchList = {rows}; loginVue.creditRatios = [];</script>"


def test_public_batch_parser_distinguishes_weight_and_grab_windows():
    batches = parse_public_batches(_html(r'''[
      {"code":"weight","name":"选修课调整","schoolTerm":"2026-2027-1",
       "schoolTermName":"2026-2027学年秋季学期","beginTime":"2026-08-18 13:00:00",
       "endTime":"2026-08-19 13:00:00","active":"1","typeCode":"04",
       "typeName":"权重","tacticName":"可选可退","clazzTypeList":["TJKC","ALLKC"],
       "needConfirm":"1","confirmInfo":"权重值选课"},
      {"code":"grab","name":"辅修选课","schoolTerm":"2026-2027-1",
       "schoolTermName":"2026-2027学年秋季学期","beginTime":"2026-08-18 13:00:00",
       "endTime":"2026-08-19 13:00:00","active":"1","typeCode":"02",
       "typeName":"抢选","tacticName":"可选可退","clazzTypeList":["FXKC"],
       "needConfirm":"0","confirmInfo":""}
    ]'''), now=datetime(2026, 8, 13, 12, 0, 0))

    assert [item.selection_type for item in batches] == ["权重", "抢选"]
    assert all(item.state == "not_started" and not item.can_enter for item in batches)
    active = parse_public_batches(_html(r'''[
      {"code":"weight","name":"选修课调整","beginTime":"2026-08-18 13:00:00",
       "endTime":"2026-08-19 13:00:00","active":"1","typeName":"权重"}
    ]'''), now=datetime(2026, 8, 18, 14, 0, 0))
    assert active[0].state == "active"
    assert active[0].can_enter is True


def test_public_batch_parser_fails_closed_on_changed_page_contract():
    with pytest.raises(JwxkError):
        parse_public_batches("<html>no batch assignment</html>")


@pytest.mark.parametrize(
    "preference,primary,expected",
    [
        ("follow", "direct", "direct"),
        ("follow", "webvpn", "webvpn"),
        ("direct", "webvpn", "direct"),
        ("webvpn", "direct", "webvpn"),
    ],
)
def test_jwxk_network_mode_can_follow_or_override_primary(preference, primary, expected):
    assert resolve_network_mode(preference, primary) == expected


def test_jwxk_network_mode_rejects_unknown_values():
    with pytest.raises(ValueError):
        resolve_network_mode("automatic", "direct")


def test_status_route_is_no_store_and_preserves_public_batch_contract(monkeypatch):
    storage = MemoryStorage()
    monkeypatch.setattr(course_selection, "peek_auth_client", lambda: None)
    monkeypatch.setattr(
        course_selection.JwxkPublicClient,
        "get_batches",
        lambda _self: parse_public_batches(_html(r'''[
          {"code":"weight","name":"选修课调整","beginTime":"2026-08-18 13:00:00",
           "endTime":"2026-08-19 13:00:00","active":"1","typeName":"权重"}
        ]'''), now=datetime(2026, 8, 13, 12, 0, 0)),
    )
    response = Response()
    result = course_selection.get_jwxk_status(response, storage)

    assert response.headers["cache-control"] == "no-store"
    assert result.available is True
    assert result.batches[0].state == "not_started"
    assert result.authenticated is False
    assert result.primary_authenticated is False
    assert result.service_authenticated is False


def test_status_route_reuses_primary_client_under_remote_guard(monkeypatch):
    storage = MemoryStorage()
    events = []

    class Primary:
        active_mode = "direct"
        is_logged_in = True

        def ensure_service_session(self, service, *, network_mode_override=None):
            events.append((service, network_mode_override))
            return True

    primary = Primary()
    monkeypatch.setattr(course_selection, "peek_auth_client", lambda: primary)

    class Guard:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, *_args):
            events.append("exit")

    monkeypatch.setattr(course_selection, "remote_session_guard", lambda: Guard())
    monkeypatch.setattr(course_selection.JwxkPublicClient, "get_batches", lambda _self: [])

    result = course_selection.get_jwxk_status(Response(), storage)

    assert events == ["enter", ("jwxk", "direct"), "exit"]
    assert result.primary_authenticated is True
    assert result.service_authenticated is True
    assert result.authenticated is True


def test_jwxk_service_uses_shared_session_and_does_not_change_primary_mode(monkeypatch):
    client = NEUAuthClient(network_mode="webvpn", restore_session=False)
    client._logged_in = True
    shared_session = client.session
    original_mode = client.active_mode
    calls = []

    def response(url):
        item = __import__("requests").Response()
        item.status_code = 200
        item.url = url
        item._content = b"ok"
        return item

    def fake_request(method, url, **kwargs):
        calls.append((client.session, method, url, kwargs.get("allow_redirects")))
        return response(url)

    monkeypatch.setattr(client.session, "request", fake_request)
    result = client.request_service(
        "jwxk", "GET", "/xsxk/profile/index.html", network_mode_override="direct"
    )

    assert result.url == "https://jwxk.neu.edu.cn/xsxk/profile/index.html"
    assert calls == [(shared_session, "GET", result.url, False)]
    assert client.active_mode == original_mode == "webvpn"


def test_jwxk_webvpn_override_routes_only_service_request(monkeypatch):
    client = NEUAuthClient(network_mode="direct", restore_session=False)
    client._logged_in = True
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(url)
        item = __import__("requests").Response()
        item.status_code = 200
        item.url = url
        item._content = b"ok"
        return item

    monkeypatch.setattr(client.session, "request", fake_request)
    result = client.request_service(
        "jwxk", "GET", "/xsxk/profile/index.html", network_mode_override="webvpn"
    )

    assert result.url == WebVPNUrlCodec.convert_url(
        "https://jwxk.neu.edu.cn/xsxk/profile/index.html"
    )
    assert calls == [result.url]
    assert client.active_mode == "direct"


def test_jwxk_webvpn_cas_callback_also_targets_webvpn(monkeypatch):
    client = NEUAuthClient(network_mode="direct", restore_session=False)
    client._logged_in = True
    calls = []
    expected_callback = WebVPNUrlCodec.convert_url(
        "https://jwxk.neu.edu.cn/xsxk/auth/cas"
    )

    def fake_redirects(method, url, **kwargs):
        calls.append(url)
        item = __import__("requests").Response()
        item.status_code = 200
        item.url = WebVPNUrlCodec.convert_url(
            "https://jwxk.neu.edu.cn/xsxk/profile/index.html"
        )
        item._content = b"ok"
        return item

    monkeypatch.setattr(client, "_request_service_redirects", fake_redirects)
    assert client.ensure_service_session("jwxk", network_mode_override="webvpn") is True
    assert len(calls) == 1
    assert "webvpn.neu.edu.cn" in calls[0]
    assert __import__("requests").utils.quote(expected_callback, safe="") in calls[0]
    assert client.active_mode == "direct"


def test_jwxk_service_rejects_paths_and_untrusted_redirects(monkeypatch):
    client = NEUAuthClient(restore_session=False)
    client._logged_in = True
    with pytest.raises(ValueError, match="not allowed"):
        client.request_service("jwxk", "GET", "/jwapp/private")

    def fake_request(method, url, **kwargs):
        item = __import__("requests").Response()
        item.status_code = 302
        item.url = url
        item.headers["Location"] = "https://evil.example/steal"
        item._content = b""
        return item

    monkeypatch.setattr(client.session, "request", fake_request)
    with pytest.raises(NEULoginError, match="不受信任"):
        client.request_service("jwxk", "GET", "/xsxk/profile/index.html")


def test_network_setting_update_keeps_unrelated_config(monkeypatch):
    storage = MemoryStorage({"other": {"enabled": True}})
    monkeypatch.setattr(course_selection, "peek_auth_client", lambda: None)
    monkeypatch.setattr(course_selection.JwxkPublicClient, "get_batches", lambda _self: [])

    result = course_selection.update_jwxk_settings(
        JwxkSettingsUpdate(network_mode="webvpn"),
        Response(),
        storage,
    )

    assert storage.config["other"] == {"enabled": True}
    assert storage.config["course_selection"] == {"network_mode": "webvpn"}
    assert result.effective_network_mode == "webvpn"
