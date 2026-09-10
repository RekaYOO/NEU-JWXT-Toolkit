import json

import pytest
import requests
from fastapi import Response

from backend.app.routers import course_selection
from backend.core.auth import NEUAuthClient
from backend.core.auth.client import NEULoginError, SERVICE_CONFIGS


@pytest.mark.parametrize('mode', ['direct', 'webvpn'])
@pytest.mark.parametrize('rejected_again', [False, True])
def test_student_info_refreshes_both_credentials_without_replaying_writes(monkeypatch, mode, rejected_again):
    client = NEUAuthClient(network_mode=mode, restore_session=False)
    client._logged_in = True
    token = 'expired-fixture'
    attempts = []
    recoveries = []
    data = {'token': token, 'other': 'keep'}

    monkeypatch.setattr(client, 'get_service_token', lambda *_args, **_kwargs: token)

    def recover(service, **kwargs):
        nonlocal token
        assert service == 'jwxk'
        assert kwargs['network_mode_override'] == mode
        assert kwargs['force_refresh'] is True
        token = 'fresh-fixture'
        recoveries.append(True)
        return True

    def request(_method, url, **kwargs):
        attempts.append((kwargs['headers']['Authorization'], kwargs['data']['token']))
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers['Content-Type'] = 'application/json'
        accepted = attempts[-1] == ('fresh-fixture', 'fresh-fixture') and not rejected_again
        response._content = json.dumps({'code': 200 if accepted else 401,
            'msg': 'ok' if accepted else 'token expired', 'data': {}}).encode()
        return response

    monkeypatch.setattr(client, 'ensure_service_session', recover)
    monkeypatch.setattr(client, '_request_service_redirects', request)
    if rejected_again:
        with pytest.raises(NEULoginError):
            client.request_service('jwxk', 'POST', '/xsxk/web/studentInfo',
                                   network_mode_override=mode, data=data)
    else:
        result = client.request_service('jwxk', 'POST', '/xsxk/web/studentInfo',
                                        network_mode_override=mode, data=data)
        assert result.json()['code'] == 200
    assert attempts == [('expired-fixture', 'expired-fixture'), ('fresh-fixture', 'fresh-fixture')]
    assert recoveries == [True]
    assert data == {'token': 'expired-fixture', 'other': 'keep'}
    assert client.active_mode == mode

    # Token form injection is specific to the read-only student endpoint.
    options = client._service_request_options('jwxk', SERVICE_CONFIGS['jwxk'],
        {'data': {'token': 'business-field'}}, network_mode=mode,
        request_path='/xsxk/volunteer/select')
    assert options['data']['token'] == 'business-field'


def test_direct_status_finishes_token_recovery_in_first_probe(monkeypatch):
    client = NEUAuthClient(username='fixture', network_mode='direct', restore_session=False)
    client._logged_in = True
    token = 'expired-fixture'
    calls = []

    class Storage:
        def load_config(self):
            return {'course_selection': {'network_mode': 'direct'}}

    def ensure(_service, **kwargs):
        nonlocal token
        if kwargs.get('force_refresh'):
            token = 'fresh-fixture'
        return True

    def request(_method, url, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers['Content-Type'] = 'application/json'
        data = {}
        accepted = True
        if url.endswith('/studentInfo'):
            calls.append((kwargs['headers']['Authorization'], kwargs['data']['token']))
            accepted = calls[-1] == ('fresh-fixture', 'fresh-fixture')
            data = {'student': {'electiveBatchList': []}}
        response._content = json.dumps({'code': 200 if accepted else 401,
            'msg': 'ok' if accepted else 'token expired', 'data': data}).encode()
        return response

    monkeypatch.setattr(client, 'get_service_token', lambda *_args, **_kwargs: token)
    monkeypatch.setattr(client, 'ensure_service_session', ensure)
    monkeypatch.setattr(client, '_request_service_redirects', request)
    monkeypatch.setattr(course_selection, 'peek_auth_client', lambda: client)
    monkeypatch.setattr(course_selection, 'attach_saved_auth_credentials', lambda _: False)
    result = course_selection.get_jwxk_status(Response(), Storage())
    assert result.service_authenticated is True
    assert result.service_auth_state == 'authenticated'
    assert calls == [('expired-fixture', 'expired-fixture'), ('fresh-fixture', 'fresh-fixture')]
