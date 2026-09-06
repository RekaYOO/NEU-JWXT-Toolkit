"""CAPTCHA rejection must survive real image-refresh and HTTP envelopes."""

from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from backend.app.routers import auth
from backend.core.auth.client import NEUAuthClient, WebVPNLoginError


@pytest.fixture
def client():
    candidate = NEUAuthClient("test-student", network_mode="webvpn", restore_session=False)
    candidate._webvpn_sms_flow = {
        "id": "sms-flow",
        "page_url": "https://webvpn.neu.edu.cn/https/token/tpass/login",
        "form_action": "https://webvpn.neu.edu.cn/https/token/tpass/secondAuth",
        "expires_at": 4102444800,
        "captcha_code": "old-input",
        "captcha_image": "old-image",
    }
    candidate._session = Mock()
    image = Mock(
        url="https://webvpn.neu.edu.cn/https/token/tpass/code",
        content=b"\xff\xd8\xfffixture", status_code=200,
        headers={"Content-Type": "image/jpeg"}, text="",
    )
    candidate.session.get.return_value = image
    response = Mock(
        url=candidate._webvpn_sms_flow["form_action"],
        text="", status_code=200, headers={"Content-Type": "application/json"},
    )
    response.json.return_value = {"info": "codeerr"}
    candidate.session.post.return_value = response
    return candidate


@pytest.mark.parametrize("operation", ["send", "verify"])
@pytest.mark.parametrize("response", [
    {"info": "codeerr"},
    {"code": "captcha_invalid"},
    {"code": "codeerror", "message": "图形验证码错误"},
])
def test_rejection_refreshes_image_but_never_becomes_success(client, operation, response):
    client.session.post.return_value.json.return_value = response
    result = (client.send_webvpn_sms_code("sms-flow", "0000") if operation == "send"
              else client.verify_webvpn_sms_code("sms-flow", "123456"))
    assert result["status"] == "captcha_invalid"
    assert result["success"] is False
    assert result["captcha_invalid"] is True
    assert result["error_code"] == "WEBVPN_CAPTCHA_INVALID"
    assert result["captcha_image"].startswith("data:image/jpeg;base64,")
    assert client._webvpn_sms_flow["captcha_code"] == ""
    assert client._webvpn_sms_flow["id"] == "sms-flow"
    assert not client.is_logged_in
    assert client.session.post.call_count == 1  # No automatic resend/verify.
    assert client.session.get.call_count == 1


@pytest.mark.parametrize("message,is_graphic", [
    ("图形验证码不正确", True),
    ("短信验证码错误", False),
    ("短信验证码已过期", False),
])
def test_html_form_distinguishes_sms_from_image_errors(client, monkeypatch, message, is_graphic):
    client.session.post.return_value.json.return_value = {}
    monkeypatch.setattr(client, "_extract_second_auth_form", lambda *_: {"form_action": "unused"})
    monkeypatch.setattr(client, "_extract_error_message", lambda *_: message)
    if is_graphic:
        assert client.verify_webvpn_sms_code("sms-flow", "123456")["status"] == "captcha_invalid"
    else:
        with pytest.raises(WebVPNLoginError) as error:
            client.verify_webvpn_sms_code("sms-flow", "123456")
        assert error.value.error_code == "WEBVPN_SMS_INVALID"
        client.session.get.assert_not_called()
        assert client._webvpn_sms_flow["captcha_code"] == "old-input"


def test_failed_replacement_keeps_flow_but_removes_stale_image(client):
    import requests
    client.session.get.side_effect = requests.Timeout()
    result = client.send_webvpn_sms_code("sms-flow", "0000")
    assert result["status"] == "captcha_invalid"
    assert result["captcha_image"] == ""
    assert result["captcha_refresh_failed"] is True
    assert client._webvpn_sms_flow["captcha_code"] == ""
    assert "刷新图片" in result["message"]


def test_success_message_mentioning_graphic_code_is_not_rejected(client):
    client.session.post.return_value.json.return_value = {
        "info": "send", "message": "图形验证码正确，短信已发送",
    }
    assert client.send_webvpn_sms_code("sms-flow", "1234")["status"] == "sent"
    client.session.get.assert_not_called()


@pytest.mark.parametrize("operation", ["send", "verify"])
def test_regular_api_preserves_real_rejection_envelope(client, monkeypatch, operation):
    monkeypatch.setattr(auth, "_webvpn_sms_client", lambda _: client)
    monkeypatch.setattr(auth, "peek_auth_client", lambda: client)
    monkeypatch.setattr(auth, "remote_session_guard", nullcontext)
    if operation == "send":
        result = auth.send_webvpn_sms_code(auth.WebVPNSMSSendRequest(
            flow_id="sms-flow", captcha_code="0000",
        ))
    else:
        result = auth.verify_webvpn_sms_code(auth.WebVPNSMSVerifyRequest(
            flow_id="sms-flow", code="123456",
        ))
    assert result["success"] is False
    assert result["status"] == "captcha_invalid"
    assert result["captcha_image"].startswith("data:image/jpeg;base64,")
