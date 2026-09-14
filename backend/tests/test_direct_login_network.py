from unittest.mock import Mock

import pytest
import requests

from backend.app.routers import auth as auth_router
from backend.app.schemas import LoginRequest
from backend.core.auth import client as auth_module
from backend.core.auth.client import DirectAccessError, NEUAuthClient, NEULoginError


@pytest.mark.parametrize("error", [requests.Timeout, requests.ConnectionError])
def test_unreachable_direct_login_returns_webvpn_hint_after_one_attempt(monkeypatch, error):
    client = NEUAuthClient("20240001", "synthetic-password", restore_session=False)
    get = Mock(side_effect=error("synthetic network failure"))
    sleep = Mock()
    monkeypatch.setattr(client._session, "get", get)
    monkeypatch.setattr(auth_module.time, "sleep", sleep)
    monkeypatch.setattr(auth_router, "NEUAuthClient", Mock(return_value=client))

    result = auth_router.login(LoginRequest(username="20240001", password="synthetic-password"))

    assert not result.success
    assert result.requires_webvpn
    assert result.error_code == "DIRECT_ACCESS_FAILED"
    get.assert_called_once()
    sleep.assert_not_called()


def test_cas_login_page_without_explicit_rejection_returns_webvpn_hint(monkeypatch):
    client = NEUAuthClient("20240001", "synthetic-password", restore_session=False)
    login_page = Mock(
        url=auth_module.CAS_LOGIN_URL,
        text='<form id="loginForm"><input type="hidden" name="lt" value="synthetic"></form>',
    )
    login_page.raise_for_status = Mock()
    submit_response = Mock(
        url=auth_module.CAS_LOGIN_URL,
        text='<form id="loginForm"></form>',
    )
    submit_response.raise_for_status = Mock()
    monkeypatch.setattr(client._session, "get", Mock(return_value=login_page))
    monkeypatch.setattr(client, "_submit_login_form", Mock(return_value=submit_response))
    refresh_key = Mock()
    monkeypatch.setattr(auth_module, "_fetch_rsa_key_from_server", refresh_key)
    monkeypatch.setattr(auth_router, "NEUAuthClient", Mock(return_value=client))

    result = auth_router.login(LoginRequest(
        username="20240001",
        password="synthetic-password",
        network_mode="direct",
    ))

    assert not result.success
    assert result.requires_webvpn
    assert result.error_code == "DIRECT_ACCESS_FAILED"
    assert "WebVPN" in result.suggestion
    refresh_key.assert_not_called()


def test_lost_password_response_is_not_treated_as_a_rotated_rsa_key(monkeypatch):
    client = NEUAuthClient("20240001", "synthetic-password", restore_session=False)
    page = Mock(url=auth_module.CAS_LOGIN_URL, text='<input name="lt" value="synthetic">')
    get = Mock(return_value=page)
    submit = Mock(side_effect=requests.Timeout("synthetic response lost"))
    refresh_key = Mock(return_value=None)
    save = Mock()
    monkeypatch.setattr(client._session, "get", get)
    monkeypatch.setattr(client, "_submit_login_form", submit)
    monkeypatch.setattr(auth_module, "_fetch_rsa_key_from_server", refresh_key)
    monkeypatch.setattr(client, "_save_cookies", save)

    with pytest.raises(DirectAccessError):
        client.login()

    get.assert_called_once()
    submit.assert_called_once()
    refresh_key.assert_not_called()
    save.assert_not_called()
    assert not client.is_logged_in


def test_explicit_key_rejection_still_allows_one_corrected_submission(monkeypatch):
    client = NEUAuthClient("20240001", "synthetic-password", restore_session=False)
    page = Mock(url=auth_module.CAS_LOGIN_URL, text='<input name="lt" value="synthetic">')
    monkeypatch.setattr(client._session, "get", Mock(return_value=page))
    submit = Mock(side_effect=["RSA decrypt error", None])
    refresh_key = Mock(return_value="synthetic-new-key")
    monkeypatch.setattr(client, "_do_login_submit", submit)
    monkeypatch.setattr(client, "clear_cookies", Mock())
    monkeypatch.setattr(client, "_save_cookies", Mock())
    monkeypatch.setattr(auth_module, "_fetch_rsa_key_from_server", refresh_key)

    assert client.login()
    assert submit.call_count == 2
    assert submit.call_args.args[-1] == "synthetic-new-key"
    refresh_key.assert_called_once()


def test_wrong_password_never_triggers_network_or_key_retries(monkeypatch):
    client = NEUAuthClient("20240001", "synthetic-password", restore_session=False)
    page = Mock(url=auth_module.CAS_LOGIN_URL, text='<input name="lt" value="synthetic">')
    monkeypatch.setattr(client._session, "get", Mock(return_value=page))
    submit = Mock(return_value="wrong password")
    refresh_key = Mock()
    monkeypatch.setattr(client, "_do_login_submit", submit)
    monkeypatch.setattr(auth_module, "_fetch_rsa_key_from_server", refresh_key)

    with pytest.raises(NEULoginError) as failure:
        client.login()
    assert failure.value.error_type == auth_module.LOGIN_ERR_WRONG_PWD
    submit.assert_called_once()
    refresh_key.assert_not_called()


def test_generic_login_failure_is_not_misclassified_as_wrong_password():
    assert auth_module._classify_login_error("统一认证失败，请稍后重试") == auth_module.LOGIN_ERR_UNKNOWN


def test_http_200_login_form_triggers_session_recovery(monkeypatch):
    client = NEUAuthClient("20240001", "synthetic-password", restore_session=False)
    client._logged_in = True
    login_html = Mock(
        status_code=200,
        url="https://jwxt.neu.edu.cn/jwapp/sys/student/home.do",
        headers={"Content-Type": "text/html; charset=utf-8"},
        history=[],
        text=(
            '<html>统一身份认证 pass.neu.edu.cn'
            '<form action="https://pass.neu.edu.cn/tpass/login">'
            '<input name="un"><input name="pd"></form></html>'
        ),
    )
    business = Mock(
        status_code=200,
        url="https://jwxt.neu.edu.cn/jwapp/sys/student/home.do",
        headers={"Content-Type": "application/json"},
        history=[],
        text='{"code":"0"}',
    )
    request = Mock(side_effect=[login_html, business])
    monkeypatch.setattr(client, "_session_request", request)
    monkeypatch.setattr(client, "ensure_login", Mock(return_value=True))

    assert client.get("https://jwxt.neu.edu.cn/jwapp/sys/student/home.do") is business
    assert request.call_count == 2
    client.ensure_login.assert_called_once()


def test_silent_direct_recovery_switches_to_webvpn(monkeypatch):
    client = NEUAuthClient("20240001", "synthetic-password", restore_session=False)
    monkeypatch.setattr(
        client, "_try_refresh_ticket",
        Mock(side_effect=DirectAccessError("direct unavailable")),
    )
    webvpn = Mock(return_value={"status": "authenticated"})
    monkeypatch.setattr(client, "start_webvpn_password_login", webvpn)

    assert client.ensure_login()
    assert client.active_mode == "webvpn"
    webvpn.assert_called_once_with()
