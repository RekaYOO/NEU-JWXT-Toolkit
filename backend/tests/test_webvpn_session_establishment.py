import json
import time
from unittest.mock import Mock

import pytest
import requests

from backend.app.client_snapshot import pending_auth_challenge_snapshot
from backend.core.auth.client import NEUAuthClient, WebVPNLoginError
from backend.core.network import WebVPNUrlCodec


def response(payload=None, status=200, path="/jwapp/sys/homeapp/api/home/currentUser.do"):
    result = requests.Response()
    result.status_code = status
    result.url = WebVPNUrlCodec.convert_url("https://jwxt.neu.edu.cn" + path)
    result._content = json.dumps(payload or {}).encode()
    result._content_consumed = True
    return result


def client():
    return NEUAuthClient("20250001", network_mode="webvpn", restore_session=False)


def sms_flow():
    return {
        "id": "fixture-flow", "source": "password", "target_service": "primary",
        "expires_at": time.time() + 300,
        "form_action": "https://webvpn.neu.edu.cn/fixture/secondAuth",
        "page_url": "https://webvpn.neu.edu.cn/fixture/login",
        "hidden_fields": {"execution": "fixture"},
        "captcha_code": "abcd", "captcha_image": "fixture",
    }


def test_first_identity_probe_can_bootstrap_cas_without_resubmitting_credentials(monkeypatch):
    auth = client()
    health = Mock(side_effect=[False, True])
    bootstrap = Mock(return_value=response(path="/jwapp/sys/homeapp/index.do"))
    monkeypatch.setattr(auth, "_webvpn_health_check", health)
    monkeypatch.setattr(auth, "_request_service_redirects", bootstrap)
    monkeypatch.setattr(auth, "start_webvpn_password_login", Mock(side_effect=AssertionError))
    assert auth._verify_webvpn_login_target("primary")["authenticated"]
    assert health.call_count == 2
    assert bootstrap.call_args.args == (
        "GET", WebVPNUrlCodec.convert_url("https://jwxt.neu.edu.cn/jwapp/sys/homeapp/index.do"),
    )
    assert bootstrap.call_args.kwargs["network_mode"] == "webvpn"


def test_valid_identity_does_not_bootstrap_again(monkeypatch):
    auth = client()
    monkeypatch.setattr(auth, "_webvpn_health_check", Mock(return_value=True))
    monkeypatch.setattr(auth, "_request_service_redirects", Mock(side_effect=AssertionError))
    assert auth._establish_webvpn_primary_session()


def test_bootstrap_timeout_is_not_login_success(monkeypatch):
    auth = client()
    monkeypatch.setattr(auth, "_webvpn_health_check", Mock(return_value=False))
    monkeypatch.setattr(auth, "_request_service_redirects", Mock(side_effect=requests.Timeout))
    assert not auth._establish_webvpn_primary_session()
    assert not auth.is_logged_in


def test_bootstrap_timeout_can_still_leave_a_valid_business_session(monkeypatch):
    auth = client()
    monkeypatch.setattr(auth, "_webvpn_health_check", Mock(side_effect=[False, True]))
    monkeypatch.setattr(auth, "_request_service_redirects", Mock(side_effect=requests.Timeout))
    assert auth._establish_webvpn_primary_session()


@pytest.mark.parametrize("payload,status,valid", [
    ({"code": "0", "datas": {"userId": "20250001"}}, 200, True),
    ({"code": 0, "datas": {"userId": "20250001"}}, 200, True),
    ({"code": "0", "datas": {}}, 200, False),
    ({"code": "0", "datas": {"userId": "20250001"}}, 503, False),
    ({"code": "0", "datas": []}, 200, False),
    ({"code": "1", "datas": {"userId": "20250001"}}, 200, False),
    (["unexpected"], 200, False),
])
def test_health_requires_verified_identity_and_closes_response(monkeypatch, payload, status, valid):
    auth = client()
    result = response(payload, status)
    result.close = Mock()
    monkeypatch.setattr(auth, "_session_request", Mock(return_value=result))
    assert auth._webvpn_health_check({}) is valid
    result.close.assert_called_once()


def test_completed_sms_retries_session_only_and_never_resends_code(monkeypatch):
    auth = client()
    auth._webvpn_sms_flow = sms_flow()
    submit = Mock(return_value=response())
    monkeypatch.setattr(auth.session, "post", submit)
    monkeypatch.setattr(auth, "_sync_cas_cookie_to_webvpn", Mock())
    monkeypatch.setattr(auth, "_verify_webvpn_login_target", Mock(side_effect=[
        {"authenticated": False}, {"authenticated": True},
    ]))
    first = auth.verify_webvpn_sms_code("fixture-flow", "123456")
    assert first["status"] == "session_pending"
    assert first["sms_verified"]
    assert not auth.is_logged_in
    deadline = auth._webvpn_sms_flow["expires_at"]
    snapshot = pending_auth_challenge_snapshot((auth,))
    assert snapshot["sms_verified"]
    assert snapshot["captcha_image"] is None
    assert "hidden_fields" not in auth._webvpn_sms_flow
    assert "captcha_code" not in auth._webvpn_sms_flow
    assert auth.send_webvpn_sms_code("fixture-flow", "abcd")["status"] == "session_pending"
    assert auth.refresh_webvpn_captcha("fixture-flow")["status"] == "session_pending"
    assert auth._webvpn_sms_flow["expires_at"] == deadline
    assert auth.verify_webvpn_sms_code("fixture-flow", "")["status"] == "authenticated"
    submit.assert_called_once()
    assert auth._webvpn_sms_flow is None
    assert auth.is_logged_in


def test_cookie_bridge_timeout_keeps_verified_flow(monkeypatch):
    auth = client()
    auth._webvpn_sms_flow = sms_flow()
    monkeypatch.setattr(auth.session, "post", Mock(return_value=response()))
    monkeypatch.setattr(auth, "_sync_cas_cookie_to_webvpn", Mock(side_effect=requests.Timeout))
    result = auth.verify_webvpn_sms_code("fixture-flow", "123456")
    assert result["status"] == "session_pending"
    assert auth._webvpn_sms_flow["sms_verified"]
    assert not auth.is_logged_in


def test_invalid_sms_never_enters_verified_phase(monkeypatch):
    auth = client()
    auth._webvpn_sms_flow = sms_flow()
    monkeypatch.setattr(auth.session, "post", Mock(return_value=response({"code": "sms_error"})))
    monkeypatch.setattr(auth, "_sync_cas_cookie_to_webvpn", Mock(side_effect=AssertionError))
    with pytest.raises(WebVPNLoginError):
        auth.verify_webvpn_sms_code("fixture-flow", "123456")
    assert not auth._webvpn_sms_flow.get("sms_verified")


def test_verified_session_retry_still_expires_and_can_be_cancelled(monkeypatch):
    auth = client()
    auth._webvpn_sms_flow = {**sms_flow(), "sms_verified": True, "expires_at": time.time() - 1}
    monkeypatch.setattr(auth.session, "post", Mock(side_effect=AssertionError))
    with pytest.raises(WebVPNLoginError) as caught:
        auth.verify_webvpn_sms_code("fixture-flow", "")
    assert caught.value.error_code == "WEBVPN_FLOW_EXPIRED"
    auth._webvpn_sms_flow = {**sms_flow(), "sms_verified": True}
    auth.cancel_webvpn_sms_login("fixture-flow")
    assert auth._webvpn_sms_flow is None
