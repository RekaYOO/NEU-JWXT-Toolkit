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
