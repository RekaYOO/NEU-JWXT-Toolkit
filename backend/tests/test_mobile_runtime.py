from dataclasses import replace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.routers import runtime as runtime_router
from backend.core.notifications import MobileNotificationService
from backend.core.auth.mobile_recovery import MobileAuthRecoveryService
from backend.core.runtime import get_runtime_config
from backend.core.runtime.access import AccessGatewayMiddleware


def _mobile_app(token: str = "m" * 48) -> TestClient:
    app = FastAPI()

    @app.get("/api/private")
    def private():
        return {"ok": True}

    @app.get("/")
    def frontend():
        return {"frontend": True}

    config = replace(
        get_runtime_config(),
        profile="mobile",
        mobile_session_token=token,
    )
    app.add_middleware(AccessGatewayMiddleware, config=config)
    return TestClient(app)


def test_mobile_gateway_requires_token_for_every_api_path():
    client = _mobile_app()

    denied = client.get("/api/private")
    public_health_is_also_denied = client.get("/api/health")
    frontend = client.get("/")

    assert denied.status_code == 401
    assert denied.json()["code"] == "MOBILE_ACCESS_REQUIRED"
    assert public_health_is_also_denied.status_code == 401
    assert frontend.status_code == 200


def test_mobile_runtime_rejects_missing_token_and_non_loopback_bind(monkeypatch, tmp_path):
    monkeypatch.setenv("NEU_JWXT_PROFILE", "mobile")
    monkeypatch.setenv("NEU_JWXT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("NEU_JWXT_MOBILE_TOKEN", raising=False)
    monkeypatch.setenv("HOST", "127.0.0.1")
    try:
        get_runtime_config()
    except ValueError as error:
        assert "NEU_JWXT_MOBILE_TOKEN" in str(error)
    else:
        raise AssertionError("missing mobile token was accepted")

    monkeypatch.setenv("NEU_JWXT_MOBILE_TOKEN", "x" * 48)
    monkeypatch.setenv("HOST", "0.0.0.0")
    try:
        get_runtime_config()
    except ValueError as error:
        assert "回环地址" in str(error)
    else:
        raise AssertionError("non-loopback mobile bind was accepted")


def test_mobile_gateway_accepts_only_exact_token():
    client = _mobile_app("secret-" + "x" * 42)

    wrong = client.get("/api/private", headers={"X-NEU-Mobile-Token": "secret-" + "x" * 41})
    allowed = client.get("/api/private", headers={"X-NEU-Mobile-Token": "secret-" + "x" * 42})

    assert wrong.status_code == 401
    assert allowed.json() == {"ok": True}


def test_mobile_notification_outbox_is_durable_deduplicated_and_acknowledged(tmp_path):
    listener = Mock()
    service = MobileNotificationService(tmp_path, Mock())
    service.register_delivery_listener("grade_tracking", listener)

    assert service.queue_notification("grade_tracking", "成绩更新", "课程有变化", "grade:1")
    assert not service.queue_notification("grade_tracking", "重复", "不会写入", "grade:1")

    restarted = MobileNotificationService(tmp_path, Mock())
    restarted.register_delivery_listener("grade_tracking", listener)
    pending = restarted.pending()

    assert len(pending) == 1
    assert pending[0]["route"] == "/scores"
    assert restarted.acknowledge(pending[0]["id"])
    assert restarted.pending() == []
    listener.assert_called_once()


def test_runtime_capabilities_are_profile_specific(monkeypatch):
    base = get_runtime_config()
    monkeypatch.setattr(runtime_router, "config", replace(base, profile="mobile"))
    assert runtime_router.runtime_capabilities(runtime_router.config) == {
        "native_notifications": True,
        "remote_auth_recovery": False,
        "system_mail": False,
        "mobile_local_backend": True,
    }

    monkeypatch.setattr(runtime_router, "config", replace(base, profile="server"))
    assert runtime_router.runtime_capabilities(runtime_router.config) == {
        "native_notifications": False,
        "remote_auth_recovery": True,
        "system_mail": True,
        "mobile_local_backend": False,
    }


def test_mobile_foreground_login_clears_prompt_and_resumes_registered_tasks(tmp_path):
    notifications = MobileNotificationService(tmp_path, Mock())
    recovery = MobileAuthRecoveryService(notifications)
    grade_resume = Mock()
    selection_resume = Mock()
    recovery.register_recovered_callback("grade_tracking", grade_resume)
    recovery.register_recovered_callback("course_selection", selection_resume)
    recovery.request_notification(
        source="grade_tracking",
        target_service="primary",
        account_id="20250001",
        subject="登录失效",
        body="请重新登录",
        dedupe_key="one",
    )

    recovery.notify_foreground_login("20250001")

    assert notifications.pending() == []
    grade_resume.assert_called_once_with("20250001")
    selection_resume.assert_called_once_with("20250001")


def test_mobile_notification_discard_respects_kind_context_and_source(tmp_path):
    service = MobileNotificationService(tmp_path, Mock())
    service.queue_notification("auth", "first", "", "1", kind="recovery",
                               template_metadata={"context_id": "first"})
    service.queue_notification("auth", "second", "", "2", kind="recovery",
                               template_metadata={"context_id": "second"})
    service.queue_notification("grade_tracking", "grade", "", "3")
    service.discard(source="auth", kind="recovery", template_context_ids={"first"})
    assert not service.has_pending("auth", "1")
    assert service.has_pending("auth", "2")
    assert service.has_pending("grade_tracking", "3")


def test_mobile_failed_delivery_callback_stays_durable_for_retry(tmp_path):
    service = MobileNotificationService(tmp_path, Mock())
    listener = Mock(side_effect=[RuntimeError("try again"), None])
    service.register_delivery_listener("grade_tracking", listener)
    service.queue_notification("grade_tracking", "grade", "", "retry")
    notification = service.pending()[0]
    assert not service.acknowledge(notification["id"])
    assert MobileNotificationService(tmp_path, Mock()).pending_count() == 1
    assert service.acknowledge(notification["id"])
    assert service.pending_count() == 0


def test_mobile_invalid_notification_is_discarded_before_delivery(tmp_path):
    service = MobileNotificationService(tmp_path, Mock())
    service.register_delivery_validator("grade_tracking", lambda item: False)
    service.queue_notification("grade_tracking", "grade", "", "stale")
    assert service.pending() == []
    assert service.pending_count() == 0


def test_mobile_launcher_reuses_process_identity_and_protects_real_socket(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    script = r"""
import json, sys, time, urllib.request, urllib.error
from launchers.mobile import start, stop
token = "launcher-test-" + "x" * 48
root = sys.argv[1]
port = start(root + "/data", root + "/resources", token, "1.0.0-test")
try:
    assert start(root + "/data", root + "/resources", token, "1.0.0-test") == port
    try:
        start(root + "/data", root + "/resources", "y" * 48, "1.0.0-test")
    except RuntimeError:
        pass
    else:
        raise AssertionError("process accepted a new token for the old backend")
    url = f"http://127.0.0.1:{port}/api/health"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(100):
        try:
            request = urllib.request.Request(url, headers={"X-NEU-Mobile-Token": token})
            with opener.open(request, timeout=1) as response:
                result = json.load(response)
                assert result["profile"] == "mobile"
                assert result["mobile_api_version"] == 1
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise AssertionError("mobile socket never became healthy")
    for path in ["/api/health", "/api/status", "/api/mobile/notifications"]:
        try:
            opener.open(url.removesuffix("/api/health") + path, timeout=1)
        except urllib.error.HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("unauthenticated API was reachable")
finally:
    stop()
"""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("NEU_JWXT_")}
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[2], env=env,
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
