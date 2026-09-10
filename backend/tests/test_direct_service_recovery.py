import json
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from fastapi import Response

from backend.app.routers import course_selection
from backend.core.auth import NEUAuthClient
from backend.core.auth.client import NEULoginError, SERVICE_CONFIGS
import backend.core.auth.client as auth_module


class RouteStorage:
    def __init__(self, mode):
        self.mode = mode

    def load_config(self):
        return {"course_selection": {"network_mode": self.mode}}


def remote_response(url, body="", *, payload=None):
    response = requests.Response()
    response.status_code = 200
    response.url = url
    response._content = (json.dumps(payload) if payload is not None else body).encode()
    response.headers["Content-Type"] = (
        "application/json" if payload is not None else "text/html"
    )
    return response


@pytest.mark.parametrize("primary_mode,preference", [
    ("webvpn", "direct"), ("direct", "direct"), ("direct", "follow"),
])
@pytest.mark.parametrize("outcome", ["success", "wrong_password", "timeout", "no_credentials"])
def test_direct_status_recovers_only_service_identity(
    monkeypatch, tmp_path, primary_mode, preference, outcome,
):
    cookie_file = tmp_path / "session.json"
    primary = NEUAuthClient(
        username="student", network_mode=primary_mode,
        cookie_file=str(cookie_file), restore_session=False,
    )
    primary._logged_in = True
    primary.session.cookies.set(
        "primary-session", "preserved", domain="webvpn.neu.edu.cn", path="/",
    )
    primary._save_cookies()
    original_target = primary.target
    original_timeout = primary.timeout
    shared_session = primary.session
    submissions = []
    requests_seen = []

    def attach_credentials(client):
        assert client is primary
        if outcome == "no_credentials":
            return False
        client.password = "fixture-password"
        return True

    def request(method, url, **kwargs):
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.hostname in {"pass.neu.edu.cn", "jwxk.neu.edu.cn"}
        assert primary.session is shared_session
        requests_seen.append((method, parsed.hostname, parsed.path))
        if parsed.hostname == "pass.neu.edu.cn":
            assert parse_qs(parsed.query)["service"] == [SERVICE_CONFIGS["jwxk"]["service"]]
            if method.upper() == "POST":
                submissions.append(parsed.path)
                assert kwargs["data"]["un"] == "student"
                if outcome == "timeout":
                    raise requests.Timeout()
                if outcome == "wrong_password":
                    return remote_response(url, '<span id="errormsg">用户名或密码错误</span>')
                primary.session.cookies.set(
                    "token", "direct-token", domain="jwxk.neu.edu.cn", path="/xsxk",
                )
                return remote_response("https://jwxk.neu.edu.cn/xsxk/profile/index.html")
            return remote_response(url, '<input type="hidden" name="lt" value="fixture">')
        if parsed.path == "/xsxk/auth/cas":
            return remote_response("https://pass.neu.edu.cn/tpass/login")
        assert kwargs["headers"]["Authorization"] == "direct-token"
        data = {"currentTime": "2026-09-10 12:00:00"}
        if parsed.path.endswith("studentInfo"):
            data = {"student": {"electiveBatchList": [{
                "code": "fixture-round", "name": "Fixture round", "canSelect": "1",
                "clazzTypeList": ["XGKC"],
            }]}}
        return remote_response(url, payload={"code": 200, "data": data})

    def forbidden(*_args, **_kwargs):
        pytest.fail("a direct service probe must not recover JWXT or WebVPN")

    monkeypatch.setattr(primary.session, "request", request)
    monkeypatch.setattr(primary, "ensure_login", forbidden)
    monkeypatch.setattr(primary, "start_webvpn_password_login", forbidden)
    monkeypatch.setattr(course_selection, "peek_auth_client", lambda: primary)
    monkeypatch.setattr(course_selection, "attach_saved_auth_credentials", attach_credentials)
    monkeypatch.setattr(course_selection.JwxkPublicClient, "get_batches", forbidden)

    result = course_selection.get_jwxk_status(Response(), RouteStorage(preference))

    expected = {
        "success": "authenticated", "wrong_password": "login_required",
        "timeout": "network_unreachable", "no_credentials": "login_required",
    }[outcome]
    assert result.service_auth_state == expected
    assert result.effective_network_mode == "direct"
    assert result.primary_authenticated is True
    assert primary.active_mode == primary_mode
    assert primary.target == original_target
    assert primary.timeout == original_timeout
    assert primary.is_logged_in is True
    assert primary.session.cookies.get("primary-session") == "preserved"
    assert json.loads(cookie_file.read_text(encoding="utf-8"))["active_mode"] == primary_mode
    assert len(submissions) == (0 if outcome == "no_credentials" else 1)
    if outcome == "success":
        assert [batch.code for batch in result.batches] == ["fixture-round"]
        # A second status check reuses the child token without resubmitting credentials.
        again = course_selection.get_jwxk_status(Response(), RouteStorage(preference))
        assert again.service_authenticated is True
        assert len(submissions) == 1
    assert requests_seen


@pytest.mark.parametrize("service", ["jwxk", "cxcy"])
@pytest.mark.parametrize("succeeds", [True, False])
def test_direct_key_refresh_preserves_other_sessions(monkeypatch, tmp_path, service, succeeds):
    cookie_file = tmp_path / "session.json"
    client = NEUAuthClient(
        username="student", password="fixture-password", network_mode="webvpn",
        cookie_file=str(cookie_file), restore_session=False,
    )
    client._logged_in = True
    preserved_domains = ["webvpn.neu.edu.cn", "jwxt.neu.edu.cn"]
    preserved_domains.append("cxcy.neu.edu.cn" if service == "jwxk" else "jwxk.neu.edu.cn")
    for domain in preserved_domains:
        client.session.cookies.set("session", "keep", domain=domain, path="/")
    client._save_cookies()
    submissions = []

    def submit(*_args):
        submissions.append(True)
        if len(submissions) == 1:
            return "fixture stale RSA key"
        client.session.cookies.set(
            "service-session", "fresh", domain=SERVICE_CONFIGS[service]["host"], path="/",
        )
        if not succeeds:
            raise NEULoginError("fixture rejected")
        return None

    monkeypatch.setattr(client.session, "get", lambda url, **_: remote_response(url))
    monkeypatch.setattr(client, "_do_login_submit", submit)
    monkeypatch.setattr(auth_module, "_fetch_rsa_key_from_server", lambda _: "fixture-new-key")

    if succeeds:
        assert client._login_direct_service(SERVICE_CONFIGS[service]) is True
    else:
        with pytest.raises(NEULoginError, match="fixture rejected"):
            client._login_direct_service(SERVICE_CONFIGS[service])
    assert len(submissions) == 2
    assert client.active_mode == "webvpn"
    assert client.is_logged_in is True
    for domain in preserved_domains:
        assert client.session.cookies.get("session", domain=domain, path="/") == "keep"
    saved = json.loads(cookie_file.read_text(encoding="utf-8"))
    assert saved["active_mode"] == "webvpn"
    assert set(preserved_domains) <= {cookie["domain"] for cookie in saved["cookies"]}
